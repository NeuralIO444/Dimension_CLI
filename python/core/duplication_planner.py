# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/duplication_planner.py
v5.8 — Plans flat precomp duplications for one conform invocation.

The planner is PURE: it consumes a ProjectStructure (project-wide
inventory) plus the active comp's ScrapeManifest and emits a
DuplicationPlan that the user reviews in the modal and Babysitter
later executes. No I/O, no AE calls, no side effects.

Decision matrix (Q1 + Q5 locked):

  shared(comp) ∧ ¬protect(comp)  →  duplicate (will_be_skipped=False)
  shared(comp) ∧ protect(comp)   →  emit, but will_be_skipped=True
                                     (PROTECT auto-skip; user can
                                      override in the modal)
  ¬shared(comp)                  →  not emitted (Q1: only shared)

User overrides: a {original_uid → bool} map can flip the will_be_skipped
flag on any candidate. Override ON for an unshared comp adds a
USER_REQUESTED entry (override-includes-unplanned path).

Aspect detection (Q6 modal warning trigger):
  source aspect = manifest.project_info.width / manifest.project_info.height
  target aspect = target_dimensions.w / target_dimensions.h
  |source - target| / source >= 0.005   →  RELAYOUT (warn)
  else                                  →  SCALE   (no warn)

PROTECT detection scope (Phase B simplification):
  A precomp is flagged PROTECT when ANY layer in the ACTIVE manifest
  pointing at that precomp carries content_tag == "PROTECT". We don't
  recursively scrape inside precomps in Phase B — that requires Phase
  C's recursive scrape pass and is not load-bearing for the
  flat-duplication path.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from core.duplication_hooks import DEFAULT_REGISTRY, HookRegistry
from core.output_naming import resolve_output_name, session_bin_path
from core.project_structure_analyzer import ProjectStructureAnalyzer
from models.duplication_plan import (
    DuplicationPlan,
    LayerRewire,
    PrecompDuplicate,
)
from models.project_structure import ProjectStructure
from models.scrape_manifest import ScrapeManifest


# Aspect-ratio tolerance below which a conform is "uniform scale"
# rather than "relayout". 0.5% per spec — covers minor rounding when
# the user picks a 1.000:1 vs 0.998:1 preset, but trips for any real
# aspect change (16:9 → 9:16 is many orders of magnitude over).
_ASPECT_TOLERANCE = 0.005


class DuplicationPlanner:
    """Builds DuplicationPlans. Stateless beyond construction args —
    `plan()` is callable repeatedly with different overrides and
    yields fresh, independent plan objects each time."""

    def __init__(
        self,
        structure: ProjectStructure,
        manifest: ScrapeManifest,
        target_dimensions: Tuple[int, int],
        preset_id: str,
        *,
        session_id: Optional[str] = None,
        hooks: Optional[HookRegistry] = None,
        timestamp: Optional[datetime] = None,
    ):
        self.structure = structure
        self.manifest = manifest
        self.target_dimensions = (int(target_dimensions[0]),
                                  int(target_dimensions[1]))
        self.preset_id = preset_id
        self.session_id = session_id or str(uuid.uuid4())
        self.hooks = hooks if hooks is not None else DEFAULT_REGISTRY
        # Captured at __init__ so session_folder_name() and the plan's
        # `session_folder` agree even if `plan()` is called minutes
        # later. Lets tests inject a deterministic timestamp.
        self._ts = timestamp or datetime.now()

        self._analyzer = ProjectStructureAnalyzer(structure)
        self._comp_id_by_name: Dict[str, int] = {
            c.name: c.id for c in structure.comps
        }
        # v5.8.8 — set of every comp name in the project (used by
        # `_unique_duplicate_name` to bump a `_v2` / `_v3` suffix when
        # the proposed `<original>_<W>x<H>` already exists). Captured
        # once at init; the planner is short-lived per conform so the
        # snapshot is never stale at use.
        self._existing_comp_names: Set[str] = {
            c.name for c in structure.comps
        }

    # ── Public API ───────────────────────────────────────────────────

    def plan(
        self,
        user_overrides: Optional[Dict[str, bool]] = None,
        *,
        user_fork_overrides: Optional[Dict[str, bool]] = None,
    ) -> DuplicationPlan:
        """Build the plan.

        `user_overrides` maps original_uid → should_duplicate. Defaults:
        shared+!protect=duplicate, shared+protect=skip, unshared=omit.

        `user_fork_overrides` (Slot 12.5 Stage C, Q3B) maps
        original_uid → fork_per_consumer. Default Q3A (one shared
        conformed copy per unique precomp). When the override is True
        for a given precomp, the planner emits N PrecompDuplicate
        entries — one per consumer in the active chain — each with
        `fork_per_consumer=True` and `consumer_layer_uid` set. Q3B is
        mandatory-correct for cases where consumers need divergent
        conform (per-consumer tag differences, differently-retimed
        consumers); the preflight modal surfaces the toggle per-row."""
        overrides = dict(user_overrides or {})
        fork_overrides = dict(user_fork_overrides or {})
        active_comp_id = self._resolve_active_comp_id()
        active_chain = self._active_chain(active_comp_id)

        # Per-precomp PROTECT flags derived from active-comp layer tags.
        protect_flags = self._collect_protect_flags()

        # Layers in the active comp that point at OTHER comps (the
        # rewire candidates). Map: precomp_id → list of (layer_uid,
        # layer_name, layer_content_tag).
        active_refs = self._active_layer_refs_to_comps()

        shared_ids = set(self._analyzer.shared_precomps())

        duplicates: List[PrecompDuplicate] = []
        rewires: List[LayerRewire] = []

        # Walk every comp that's a candidate for duplication.
        # Default policy: emit only if shared. Overrides can include
        # unshared comps too (USER_REQUESTED reason).
        candidates: Set[int] = set()
        for cid in shared_ids:
            candidates.add(cid)
        for uid_str, should in overrides.items():
            try:
                cid = int(uid_str)
            except (TypeError, ValueError):
                continue
            if should:
                candidates.add(cid)

        # Restrict to comps that appear in the active conform chain.
        # Anything outside the active chain isn't reachable from the
        # current conform and shouldn't be duplicated by it.
        in_chain_candidates = sorted(c for c in candidates if c in active_chain)

        for cid in in_chain_candidates:
            comp_node = self._analyzer.comp(cid)
            if comp_node is None:
                continue   # corrupt structure ref — defensive

            is_shared = cid in shared_ids
            is_protect = protect_flags.get(cid, False)

            # Reason precedence: override-includes-unshared wins
            # USER_REQUESTED; everything else is SHARED (since we
            # filtered out non-candidates above).
            reason = "SHARED" if is_shared else "USER_REQUESTED"

            # Default policy:
            #   shared + !protect → duplicate
            #   shared + protect  → skip (user can override)
            #   !shared           → only here via override → duplicate
            if reason == "SHARED" and is_protect:
                will_be_skipped = True
            else:
                will_be_skipped = False

            # User override flips the skip flag.
            uid_key = str(cid)
            if uid_key in overrides:
                will_be_skipped = not bool(overrides[uid_key])

            # Q3B per-precomp fork override (Slot 12.5 Stage C).
            fork_per_consumer = bool(fork_overrides.get(uid_key, False))

            consumers = list(active_refs.get(cid, ()))
            non_protect_consumers = [
                (luid, lname, ltag)
                for (luid, lname, ltag) in consumers
                if ltag not in ("PROTECT", "GUIDE")
            ]

            if reason == "SHARED" and not will_be_skipped and not consumers:
                # Shared project-wide (per shared_precomps(), which walks
                # the full reference graph), but no layer in the ACTIVE
                # manifest's own direct layer list points at it — only
                # reachable via a nested (2+ level) chain, e.g. Master ->
                # SceneA -> Card and Master -> SceneB -> Card, where Card
                # never appears as a direct Master layer. Phase B's
                # active-manifest-only scan (see module docstring) can't
                # see that reference, so there is nothing to rewire this
                # duplicate to. Emitting it anyway creates an orphaned
                # comp in AE that nothing ever points at. Distinguish
                # from the PROTECT/GUIDE-filtered case below (consumers
                # non-empty, non_protect_consumers empty) — that one is
                # intentionally still emitted (see
                # test_user_override_includes_protected).
                continue

            if fork_per_consumer and not will_be_skipped and non_protect_consumers:
                # Q3B path — emit one PrecompDuplicate per consumer.
                # Each fork gets its own unique name (via
                # `_unique_duplicate_name`'s collision-aware versioning,
                # which reserves names as they're emitted); each
                # rewire points at its own fork via a consumer-keyed
                # placeholder `dup:<cid>#<consumer_uid>`.
                for (luid, lname, ltag) in non_protect_consumers:
                    fork_name, fork_version = self._unique_duplicate_name(comp_node.name)
                    fork_dup = PrecompDuplicate(
                        original_uid=uid_key,
                        original_name=comp_node.name,
                        duplicate_name=fork_name,
                        target_folder_path=self.session_folder_name(),
                        original_width=comp_node.width,
                        original_height=comp_node.height,
                        reason=reason,
                        is_protected=is_protect,
                        will_be_skipped=will_be_skipped,
                        name_version=fork_version,
                        fork_per_consumer=True,
                        consumer_layer_uid=luid or None,
                    )
                    self._existing_comp_names.add(fork_name)
                    fork_dup = self.hooks.fire_on_pre_duplicate(fork_dup)
                    duplicates.append(fork_dup)
                    rewires.append(LayerRewire(
                        conformed_layer_uid=luid,
                        conformed_layer_name=lname,
                        original_source_uid=uid_key,
                        # Consumer-keyed placeholder — Babysitter maps
                        # `dup:<cid>#<consumer_uid>` to the specific
                        # fork created for that consumer.
                        new_source_uid=f"dup:{uid_key}#{luid}",
                    ))
                continue

            # Q3A default path — one shared conformed copy.
            base_name, version = self._unique_duplicate_name(comp_node.name)
            dup = PrecompDuplicate(
                original_uid=uid_key,
                original_name=comp_node.name,
                duplicate_name=base_name,
                target_folder_path=self.session_folder_name(),
                original_width=comp_node.width,
                original_height=comp_node.height,
                reason=reason,
                is_protected=is_protect,
                will_be_skipped=will_be_skipped,
                name_version=version,
                fork_per_consumer=False,
                consumer_layer_uid=None,
            )
            # Reserve this name so a sibling duplicate later in the loop
            # can't collide with it (rare but possible: two precomps
            # with names that resolve to the same `<original>_<W>x<H>`).
            self._existing_comp_names.add(base_name)
            dup = self.hooks.fire_on_pre_duplicate(dup)
            duplicates.append(dup)

            # Rewires: every active-comp layer pointing at this precomp
            # gets a rewire entry, EXCEPT layers tagged PROTECT —
            # those are explicitly excluded from rewire (the user
            # locked that layer down). Skipped duplicates also produce
            # no rewires — there's nothing to rewire to.
            if will_be_skipped:
                continue
            for layer_uid, layer_name, layer_tag in non_protect_consumers:
                rewires.append(LayerRewire(
                    conformed_layer_uid=layer_uid,
                    conformed_layer_name=layer_name,
                    original_source_uid=uid_key,
                    # Phase B placeholder — Babysitter rewrites this
                    # at execution time using the original→new map it
                    # builds during the duplicate phase. The contract
                    # field stays for Phase C/D introspection.
                    new_source_uid=f"dup:{uid_key}",
                ))

        # Compute depth_max for the planned set — used by the modal's
        # progress-bar threshold and Phase C/D depth-aware hooks.
        depth_max = 0
        for d in duplicates:
            if d.will_be_skipped:
                continue
            d_depth = self._analyzer.comp_depth(int(d.original_uid))
            if d_depth > depth_max:
                depth_max = d_depth

        plan = DuplicationPlan(
            session_id=self.session_id,
            session_folder=self.session_folder_name(),
            preset_id=self.preset_id,
            target_dimensions=self.target_dimensions,
            aspect_ratio_changed=self._aspect_changed(),
            duplicates=duplicates,
            rewires=rewires,
            depth_max=depth_max,
        )
        plan = self.hooks.fire_on_plan_built(plan)
        return plan

    # ── Naming (Q2 + Q3) ─────────────────────────────────────────────

    def session_folder_name(self) -> str:
        """Artist-friendly: 'From Dimensions/<PRESET> (<YYYY-MM-DD HH:MM>)'."""
        ts = self._ts.strftime("%Y-%m-%d_%H%M%S")
        return session_bin_path(self.preset_id, ts)

    def duplicate_name(self, original_name: str) -> str:
        """Q4 Path A-minus base name (no collision check).

        Slot 12.5 Stage D item 1 replaced the v5.8 `<original>_<W>x<H>`
        Q2 format with the Q4 suffix-based template via the shared
        resolver `core.output_naming.resolve_output_name`. The planner
        doesn't yet carry a full Target object (constructor takes
        `target_dimensions` and `preset_id`), so the resolver runs with
        the default template `"{source}_D"`. A future planner refactor
        threading a `preset: Target` through the constructor will pick
        up per-preset templates here for free — this method delegates
        to the resolver.

        Use `_unique_duplicate_name` for the collision-aware version
        the planner actually emits."""
        return resolve_output_name(
            source_name=original_name,
            existing_names=set(),  # base form: no collision check
            target_width=self.target_dimensions[0],
            target_height=self.target_dimensions[1],
            preset_id=self.preset_id,
        ).name

    def _unique_duplicate_name(self, original_name: str) -> tuple[str, int]:
        """Collision-aware Q4 Path A-minus name. Returns (resolved_name,
        version). Version 1 means no collision; the proposed name was
        free. Version >= 2 means the planner bumped a `_v2`, `_v3`, ...
        suffix to avoid an existing CompItem name in the project.

        Slot 12.5 Stage D item 1 — delegates to
        `core.output_naming.resolve_output_name`. Pre-Stage-D this
        method built `<original>_<W>x<H>` inline (the v5.8 Q2 format);
        post-Stage-D the single resolver handles every output-name
        site (root comp, duplicated precomps, mirror tree) with
        identical collision logic. The version-bump suffix is `_vN`
        in both eras."""
        result = resolve_output_name(
            source_name=original_name,
            existing_names=self._existing_comp_names,
            target_width=self.target_dimensions[0],
            target_height=self.target_dimensions[1],
            preset_id=self.preset_id,
        )
        return result.name, result.version

    # ── Internals ────────────────────────────────────────────────────

    def _aspect_changed(self) -> bool:
        """True when source/target aspect ratios differ by ≥ 0.5%."""
        pinfo = self.manifest.project_info
        if not pinfo or not pinfo.height or not pinfo.width:
            return False
        if self.target_dimensions[1] == 0:
            return False
        src_aspect = pinfo.width / pinfo.height
        tgt_aspect = self.target_dimensions[0] / self.target_dimensions[1]
        if src_aspect <= 0:
            return False
        return abs(src_aspect - tgt_aspect) / src_aspect >= _ASPECT_TOLERANCE

    def _resolve_active_comp_id(self) -> Optional[int]:
        """Match the active manifest's comp name against the
        ProjectStructure to get its AE id. The match is name-based —
        AE doesn't expose a stable comp UUID, only ids that change
        across project re-saves. If no match, the active chain is
        empty and the planner emits no duplicates."""
        name = self.manifest.project_info.name
        return self._comp_id_by_name.get(name)

    def _active_chain(self, active_comp_id: Optional[int]) -> Set[int]:
        """The active comp + every comp it transitively references.
        Anything outside this set isn't reachable from the conform
        target and is irrelevant to this plan even if it's shared."""
        if active_comp_id is None:
            return set()
        chain = self._analyzer.descendants(active_comp_id)
        chain.add(active_comp_id)
        return chain

    def _collect_protect_flags(self) -> Dict[int, bool]:
        """Per-precomp PROTECT signal: a precomp is flagged when any
        layer in the active manifest pointing at it carries
        content_tag == 'PROTECT' or content_tag == 'GUIDE'.

        Phase B simplification — see module docstring. Phase C will
        recursively scrape into precomps and detect PROTECT layers
        regardless of whether they surface in the active manifest."""
        flags: Dict[int, bool] = {}
        for layer in self.manifest.layers:
            cid = self._layer_nested_comp_id(layer)
            if cid is None:
                continue
            tag = (layer.content_tag or "").upper()
            if tag in ("PROTECT", "GUIDE"):
                flags[cid] = True
            else:
                # Don't downgrade a previous PROTECT — first protected
                # layer wins.
                flags.setdefault(cid, False)
        return flags

    def _active_layer_refs_to_comps(
        self,
    ) -> Dict[int, List[Tuple[str, str, Optional[str]]]]:
        """Map: precomp_id → list of (layer_uid, layer_name, content_tag)
        for every layer in the active manifest whose source is that
        precomp. Used to emit rewires."""
        refs: Dict[int, List[Tuple[str, str, Optional[str]]]] = {}
        for layer in self.manifest.layers:
            cid = self._layer_nested_comp_id(layer)
            if cid is None:
                continue
            entry = (
                layer.uid or "",
                layer.name,
                (layer.content_tag or None),
            )
            refs.setdefault(cid, []).append(entry)
        return refs

    @staticmethod
    def _layer_nested_comp_id(layer) -> Optional[int]:
        """Pull the precomp id off a v5 manifest layer. Returns None
        for layers whose source is footage / solid / placeholder."""
        si = getattr(layer, "source_item", None)
        if si is None:
            return None
        if getattr(si, "kind", None) != "comp":
            return None
        cid = getattr(si, "nested_comp_id", None)
        if cid is None:
            # Some scrapes populate `id` instead — fall back.
            cid = getattr(si, "id", None)
        return cid
