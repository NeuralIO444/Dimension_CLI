# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_duplication_planner.py
v5.8 — coverage for DuplicationPlanner: shared detection, PROTECT
auto-skip, user override, aspect-ratio classification, name format,
rewire scope.

Synthetic ProjectStructure + ScrapeManifest fixtures — no AE round-trip.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


# ── Fixture builders ─────────────────────────────────────────────────


def _structure(comps, references):
    """comps: [(id, name, w, h)], references: [(from_id, to_id, layer_idx, layer_name)]."""
    from models.project_structure import (
        CompNode, CompReference, ProjectStructure, ScanMeta,
    )
    return ProjectStructure(
        status="OK",
        schema_version="1.0",
        scan_meta=ScanMeta(scanned_at="2026-04-25T15:00:00Z"),
        comps=[
            CompNode(id=cid, name=name, width=w, height=h,
                     fps=23.976, duration=10.0, pixel_aspect=1.0,
                     bg_color=[0.0, 0.0, 0.0], layer_count=3,
                     is_render_target=False, folder_path="")
            for (cid, name, w, h) in comps
        ],
        references=[
            CompReference(from_comp_id=f, to_comp_id=t,
                          layer_index=li, layer_name=ln)
            for (f, t, li, ln) in references
        ],
    )


def _manifest(active_name, active_w, active_h, layers):
    """layers: [{index, name, uid, source_comp_id, content_tag}]
    Layers without source_comp_id are footage layers (skipped by
    planner). content_tag is optional."""
    from models.scrape_manifest import ScrapeManifest

    layer_dicts = []
    for L in layers:
        d = {
            "index":        L["index"],
            "name":         L["name"],
            "uid":          L["uid"],
            "parent_index": -1,
            "layer_kind":   "av",
        }
        if "content_tag" in L:
            d["content_tag"] = L["content_tag"]
            d["content_tag_source"] = "manual_label"
        if "source_comp_id" in L:
            d["source_item"] = {
                "kind":           "comp",
                "name":           f"src_{L['source_comp_id']}",
                "id":             L["source_comp_id"],
                "nested_comp_id": L["source_comp_id"],
            }
        layer_dicts.append(d)

    return ScrapeManifest.model_validate({
        "status": "OK",
        "project_info": {"name": active_name, "width": active_w,
                          "height": active_h, "fps": 23.976},
        "layers": layer_dicts,
    })


# ── Planner: empty / no-shared ───────────────────────────────────────


class TestPlannerNoShared:
    def test_no_shared_no_plan(self):
        from core.duplication_planner import DuplicationPlanner

        # ART_v14 → BG_unique. BG_unique used by nothing else.
        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080), (2, "BG_unique", 1920, 1080)],
            references=[(1, 2, 1, "bg")],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [{"index": 1, "name": "bg", "uid": "u1", "source_comp_id": 2}],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan()
        assert plan.is_empty
        assert plan.duplicates == []
        assert plan.rewires == []

    def test_active_comp_not_in_structure_returns_empty(self):
        """Manifest comp name doesn't match any structure comp →
        active chain empty → no plan."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "OTHER", 1920, 1080), (2, "BG", 1920, 1080)],
            references=[(1, 2, 1, "bg"), (1, 2, 2, "bg2")],   # BG shared
        )
        manifest = _manifest(
            "GHOST_COMP", 1920, 1080,   # name doesn't match structure
            [{"index": 1, "name": "x", "uid": "u1", "source_comp_id": 2}],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan()
        assert plan.is_empty


# ── Planner: shared detection ────────────────────────────────────────


class TestPlannerSharedDetection:
    def test_shared_without_protect_planned(self):
        from core.duplication_planner import DuplicationPlanner

        # Hero used by ART_v14 + ART_v15 → shared.
        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero",     1920, 1080)],
            references=[(1, 3, 1, "hero"), (2, 3, 1, "hero")],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [{"index": 1, "name": "hero", "uid": "L1", "source_comp_id": 3}],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan()
        assert len(plan.duplicates) == 1
        d = plan.duplicates[0]
        assert d.original_uid == "3"
        assert d.original_name == "Hero"
        assert d.reason == "SHARED"
        assert d.is_protected is False
        assert d.will_be_skipped is False
        assert d.original_width == 1920 and d.original_height == 1080

    def test_shared_with_protect_auto_skipped(self):
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "CTA",     1920, 1080)],
            references=[(1, 3, 1, "cta"), (2, 3, 1, "cta")],
        )
        # The active layer pointing at CTA is tagged PROTECT.
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [{
                "index": 1, "name": "cta_locked", "uid": "L1",
                "source_comp_id": 3, "content_tag": "PROTECT",
            }],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan()
        assert len(plan.duplicates) == 1
        d = plan.duplicates[0]
        assert d.is_protected is True
        assert d.will_be_skipped is True
        # Reporting: row appears in modal so user can override; no rewire emitted.
        assert plan.rewires == []

    def test_shared_only_via_nested_chain_produces_no_orphaned_duplicate(self):
        """A precomp shared only through a nested (2+ level) reference
        chain — reachable via descendants() and picked up by
        shared_precomps() (both project-wide graph walks) — but never
        referenced by a layer directly in the ACTIVE manifest itself.
        Phase B's active-manifest-only scan (see module docstring) has
        no way to produce a rewire for it, so planning a duplicate here
        would create an orphaned comp in AE that nothing ever points at.
        Master -> SceneA -> Card and Master -> SceneB -> Card, but
        Master's own manifest only has direct layers for SceneA/SceneB,
        never Card."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "Master", 1920, 1080),
                   (2, "SceneA", 1920, 1080),
                   (3, "SceneB", 1920, 1080),
                   (4, "Card", 1920, 1080)],
            references=[
                (1, 2, 1, "sceneA"), (1, 3, 2, "sceneB"),
                (2, 4, 1, "card"), (3, 4, 1, "card"),  # Card is shared, but only 2 levels deep
            ],
        )
        manifest = _manifest(
            "Master", 1920, 1080,
            [{"index": 1, "name": "sceneA", "uid": "L1", "source_comp_id": 2},
             {"index": 2, "name": "sceneB", "uid": "L2", "source_comp_id": 3}],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan()
        assert plan.is_empty
        assert plan.duplicates == []
        assert plan.rewires == []

    def test_shared_outside_active_chain_excluded(self):
        """A precomp shared between two OTHER comps but not used by
        the active comp must NOT be planned."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "OtherA", 1920, 1080),
                   (3, "OtherB", 1920, 1080),
                   (4, "Shared", 1920, 1080)],
            references=[
                (2, 4, 1, "shared"), (3, 4, 1, "shared"),  # Shared is shared
                # ART_v14 references nothing — Shared is irrelevant to it.
            ],
        )
        manifest = _manifest("ART_v14", 1920, 1080, [])
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan()
        assert plan.is_empty


# ── Planner: user overrides ──────────────────────────────────────────


class TestPlannerUserOverride:
    def test_user_override_skips_planned(self):
        """User unchecks a SHARED+!PROTECT precomp — will_be_skipped flips."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero",     1920, 1080)],
            references=[(1, 3, 1, "hero"), (2, 3, 1, "hero")],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [{"index": 1, "name": "hero", "uid": "L1", "source_comp_id": 3}],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan(user_overrides={"3": False})
        assert plan.duplicates[0].will_be_skipped is True
        # Skipped duplicate yields no rewire.
        assert plan.rewires == []

    def test_user_override_includes_protected(self):
        """User checks a PROTECT row — will_be_skipped goes False."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "CTA",     1920, 1080)],
            references=[(1, 3, 1, "cta"), (2, 3, 1, "cta")],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [{"index": 1, "name": "cta", "uid": "L1",
              "source_comp_id": 3, "content_tag": "PROTECT"}],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan(user_overrides={"3": True})
        d = plan.duplicates[0]
        assert d.is_protected is True       # reporting unchanged
        assert d.will_be_skipped is False   # but execution proceeds
        # PROTECT-tagged layer is excluded from rewire even when the
        # precomp itself is being duplicated — the user locked the
        # individual layer, not just the precomp.
        assert plan.rewires == []

    def test_user_override_includes_unshared(self):
        """User adds an unshared precomp to the plan — emitted with
        reason=USER_REQUESTED."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "BG_unique", 1920, 1080)],
            references=[(1, 2, 1, "bg")],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [{"index": 1, "name": "bg", "uid": "L1", "source_comp_id": 2}],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan(user_overrides={"2": True})
        assert len(plan.duplicates) == 1
        assert plan.duplicates[0].reason == "USER_REQUESTED"
        assert plan.duplicates[0].will_be_skipped is False


# ── Planner: aspect ratio ─────────────────────────────────────────────


class TestPlannerAspect:
    def test_aspect_ratio_change_detected(self):
        """16:9 → 9:16 must trip the relayout flag."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "MAIN", 1920, 1080)], references=[],
        )
        manifest = _manifest("MAIN", 1920, 1080, [])
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan()
        assert plan.aspect_ratio_changed is True

    def test_aspect_ratio_unchanged_on_uniform_scale(self):
        """HD → 4K is the same 16:9 — no relayout warning."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "MAIN", 1920, 1080)], references=[],
        )
        manifest = _manifest("MAIN", 1920, 1080, [])
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(3840, 2160),
            preset_id="UHD",
        ).plan()
        assert plan.aspect_ratio_changed is False

    def test_aspect_ratio_within_half_percent_is_uniform(self):
        """0.4% off (under tolerance) classifies as SCALE."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "MAIN", 1000, 1000)], references=[],
        )
        manifest = _manifest("MAIN", 1000, 1000, [])
        # 1003/1000 vs 1000/1000 → 0.3% delta
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1003, 1000),
            preset_id="ROUNDING_TEST",
        ).plan()
        assert plan.aspect_ratio_changed is False


# ── Planner: naming (Q2 + Q3) ────────────────────────────────────────


class TestPlannerNaming:
    def test_session_folder_name_format(self):
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(comps=[(1, "M", 1, 1)], references=[])
        manifest = _manifest("M", 1, 1, [])
        ts = datetime(2026, 4, 25, 15, 30, 22)
        planner = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK_VERT",
            timestamp=ts,
        )
        assert planner.session_folder_name() == \
            "From Dimensions/Tiktok Vert (2026-04-25 15:30)"

    def test_duplicate_name_format(self):
        """Slot 12.5 Stage D item 1 — Q4 Path A-minus naming.

        Replaces the pre-Stage-D v5.8 Q2 format (`<source>_<W>x<H>`)
        with the suffix-based Q4 template. v6 default is
        `{source}_{preset}` — the planner now threads its `preset_id`
        into the resolver, so a `preset_id="TIKTOK"` produces
        `Hero_Title_TIKTOK`."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(comps=[(1, "M", 1, 1)], references=[])
        manifest = _manifest("M", 1, 1, [])
        planner = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        )
        assert planner.duplicate_name("Hero_Title") == "Hero_Title_TIKTOK"


# ── Planner: rewires ─────────────────────────────────────────────────


class TestPlannerRewires:
    def test_rewire_targets_active_chain_only(self):
        """A shared precomp in the active chain emits one rewire per
        active-comp layer that points at it. Layers in OTHER comps
        that point at it produce nothing here — those are different
        conform invocations."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero",     1920, 1080)],
            references=[(1, 3, 1, "hero"), (2, 3, 1, "hero")],
        )
        # Active manifest has TWO layers pointing at Hero — two rewires.
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [
                {"index": 1, "name": "hero_a", "uid": "La",
                 "source_comp_id": 3},
                {"index": 2, "name": "hero_b", "uid": "Lb",
                 "source_comp_id": 3},
            ],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan()
        assert len(plan.duplicates) == 1
        assert len(plan.rewires) == 2
        uids = {r.conformed_layer_uid for r in plan.rewires}
        assert uids == {"La", "Lb"}
        for r in plan.rewires:
            assert r.original_source_uid == "3"
            assert r.new_source_uid == "dup:3"

    def test_rewire_skips_protect_tagged_layers(self):
        """When a precomp is duplicated but ONE of its parent layers
        in the active comp is PROTECT-tagged, that specific layer
        gets no rewire — but other untagged layers pointing at the
        same precomp still do."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero",     1920, 1080)],
            references=[(1, 3, 1, "hero"), (2, 3, 1, "hero")],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [
                {"index": 1, "name": "hero_locked", "uid": "La",
                 "source_comp_id": 3, "content_tag": "PROTECT"},
                {"index": 2, "name": "hero_other", "uid": "Lb",
                 "source_comp_id": 3},
            ],
        )
        # Override the PROTECT auto-skip so the precomp does get duplicated.
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan(user_overrides={"3": True})
        assert plan.duplicates[0].will_be_skipped is False
        # Only the non-PROTECT layer rewires.
        assert len(plan.rewires) == 1
        assert plan.rewires[0].conformed_layer_uid == "Lb"


# ── Planner: hooks integration ───────────────────────────────────────


class TestPlannerHookIntegration:
    def test_planner_fires_on_plan_built(self):
        from core.duplication_hooks import DefaultHooks, HookRegistry
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero",     1920, 1080)],
            references=[(1, 3, 1, "hero"), (2, 3, 1, "hero")],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [{"index": 1, "name": "hero", "uid": "L1",
              "source_comp_id": 3}],
        )

        class TaggingHook(DefaultHooks):
            def __init__(self):
                self.fired = False
            def on_plan_built(self, plan):
                self.fired = True
                return plan

        reg = HookRegistry()
        h = TaggingHook()
        reg.register(h)
        DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
            hooks=reg,
        ).plan()
        assert h.fired is True


# ── Planner: depth_max ───────────────────────────────────────────────


class TestPlannerDepth:
    def test_depth_max_reflects_planned_set(self):
        """Depth_max is the deepest comp_depth across active duplicates.
        Skipped entries don't count."""
        from core.duplication_planner import DuplicationPlanner

        # Hero(3) → SubA(4) → SubB(5). All shared (used by ART_v14 + v15).
        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero",     1920, 1080),
                   (4, "SubA",     1920, 1080),
                   (5, "SubB",     1920, 1080)],
            references=[
                (1, 3, 1, "h"), (2, 3, 1, "h"),
                (3, 4, 1, "a"), (2, 4, 1, "a"),
                (4, 5, 1, "b"), (2, 5, 1, "b"),
            ],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [{"index": 1, "name": "h", "uid": "L1", "source_comp_id": 3}],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan()
        # Hero is depth 2 (Hero → SubA → SubB).
        assert plan.depth_max == 2


# ── Slot 12.5 Stage C item 2 — Q3 planner flip + Q3B fork override ──


class TestPlannerQ3ASharedDefault:
    """Q3A is the locked default: ONE conformed copy per unique shared
    precomp, with N rewires (one per consumer) all pointing at the
    same duplicate. This was already the v5.8 behavior; Stage C item 2
    pins it with explicit tests so a future refactor can't silently
    regress to per-consumer forking."""

    def test_shared_with_three_consumers_produces_one_duplicate(self):
        from core.duplication_planner import DuplicationPlanner

        # Active comp ART_v14 references Hero three times. Hero is also
        # used by ART_v15 → shared. Default Q3A: ONE Hero duplicate,
        # THREE rewires all pointing at it.
        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero",    1920, 1080)],
            references=[
                (1, 3, 1, "hero_a"), (1, 3, 2, "hero_b"), (1, 3, 3, "hero_c"),
                (2, 3, 1, "hero_other"),
            ],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [
                {"index": 1, "name": "hero_a", "uid": "L_a", "source_comp_id": 3},
                {"index": 2, "name": "hero_b", "uid": "L_b", "source_comp_id": 3},
                {"index": 3, "name": "hero_c", "uid": "L_c", "source_comp_id": 3},
            ],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan()
        # Q3A — one duplicate for the unique precomp.
        assert len(plan.duplicates) == 1
        d = plan.duplicates[0]
        assert d.fork_per_consumer is False
        assert d.consumer_layer_uid is None
        # Three rewires, all targeting the same shared duplicate.
        assert len(plan.rewires) == 3
        for r in plan.rewires:
            assert r.original_source_uid == "3"
            assert r.new_source_uid == "dup:3"


class TestPlannerQ3BForkOverride:
    """Q3B is the per-precomp override for divergent-conform cases
    (different tags per consumer, differently-retimed consumers). The
    override is mandatory-correct for those cases, not a power-user
    luxury — wired through the preflight modal as a per-row toggle."""

    def test_q3b_override_emits_one_duplicate_per_consumer(self):
        from core.duplication_planner import DuplicationPlanner

        # Same fixture as TestPlannerQ3ASharedDefault but the user
        # flips Hero to Q3B-fork.
        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero",    1920, 1080)],
            references=[
                (1, 3, 1, "hero_a"), (1, 3, 2, "hero_b"), (1, 3, 3, "hero_c"),
                (2, 3, 1, "hero_other"),
            ],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [
                {"index": 1, "name": "hero_a", "uid": "L_a", "source_comp_id": 3},
                {"index": 2, "name": "hero_b", "uid": "L_b", "source_comp_id": 3},
                {"index": 3, "name": "hero_c", "uid": "L_c", "source_comp_id": 3},
            ],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan(user_fork_overrides={"3": True})
        # Q3B — three duplicates (one per consumer).
        assert len(plan.duplicates) == 3
        # Each fork carries the marker + the consumer it's for.
        consumer_uids = sorted(d.consumer_layer_uid for d in plan.duplicates)
        assert consumer_uids == ["L_a", "L_b", "L_c"]
        for d in plan.duplicates:
            assert d.fork_per_consumer is True
            assert d.original_uid == "3"

    def test_q3b_fork_names_are_unique(self):
        """Each fork goes through `_unique_duplicate_name`, which
        bumps `_v2`, `_v3`, etc. against the project + already-emitted
        names. The N forks emitted in one Q3B run MUST end up with
        distinct names so Babysitter can create them without collision."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero",    1920, 1080)],
            references=[
                (1, 3, 1, "h1"), (1, 3, 2, "h2"),
                (2, 3, 1, "hO"),
            ],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [
                {"index": 1, "name": "h1", "uid": "L1", "source_comp_id": 3},
                {"index": 2, "name": "h2", "uid": "L2", "source_comp_id": 3},
            ],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan(user_fork_overrides={"3": True})
        names = [d.duplicate_name for d in plan.duplicates]
        assert len(names) == len(set(names)), (
            f"Forked duplicate names must be unique; got {names!r}"
        )

    def test_q3b_rewires_point_at_consumer_specific_duplicates(self):
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero",    1920, 1080)],
            references=[
                (1, 3, 1, "h1"), (1, 3, 2, "h2"),
                (2, 3, 1, "hO"),
            ],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [
                {"index": 1, "name": "h1", "uid": "L1", "source_comp_id": 3},
                {"index": 2, "name": "h2", "uid": "L2", "source_comp_id": 3},
            ],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan(user_fork_overrides={"3": True})
        # Two rewires, each carrying a consumer-keyed placeholder.
        rewire_targets = {r.conformed_layer_uid: r.new_source_uid
                          for r in plan.rewires}
        assert rewire_targets == {
            "L1": "dup:3#L1",
            "L2": "dup:3#L2",
        }

    def test_q3a_default_unchanged_when_fork_override_absent(self):
        """Adding the user_fork_overrides param without specifying any
        precomp must leave Q3A behavior unchanged — the override is
        opt-in per-row."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero",    1920, 1080)],
            references=[
                (1, 3, 1, "h1"), (1, 3, 2, "h2"),
                (2, 3, 1, "hO"),
            ],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [
                {"index": 1, "name": "h1", "uid": "L1", "source_comp_id": 3},
                {"index": 2, "name": "h2", "uid": "L2", "source_comp_id": 3},
            ],
        )
        plan_a = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan()
        plan_b = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan(user_fork_overrides={})
        plan_c = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan(user_fork_overrides={"3": False})
        for plan in (plan_a, plan_b, plan_c):
            assert len(plan.duplicates) == 1
            assert plan.duplicates[0].fork_per_consumer is False
            assert len(plan.rewires) == 2

    def test_q3a_and_q3b_mixed_in_one_plan(self):
        """One precomp shared as Q3A, another forked Q3B, in the same
        plan. Each precomp's override is independent."""
        from core.duplication_planner import DuplicationPlanner

        # Hero (cid 3) and Title (cid 4) both shared. Override flips
        # Title to fork; Hero stays Q3A.
        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero",    1920, 1080),
                   (4, "Title",   1920, 1080)],
            references=[
                (1, 3, 1, "h"), (2, 3, 1, "h_other"),
                (1, 4, 2, "t_a"), (1, 4, 3, "t_b"), (2, 4, 2, "t_other"),
            ],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [
                {"index": 1, "name": "h",   "uid": "Lh",  "source_comp_id": 3},
                {"index": 2, "name": "t_a", "uid": "Lta", "source_comp_id": 4},
                {"index": 3, "name": "t_b", "uid": "Ltb", "source_comp_id": 4},
            ],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan(user_fork_overrides={"4": True})
        # Hero stays Q3A (1 dup); Title forks to 2 (one per consumer).
        hero_dups = [d for d in plan.duplicates if d.original_uid == "3"]
        title_dups = [d for d in plan.duplicates if d.original_uid == "4"]
        assert len(hero_dups) == 1
        assert hero_dups[0].fork_per_consumer is False
        assert len(title_dups) == 2
        for d in title_dups:
            assert d.fork_per_consumer is True

    def test_q3b_protect_consumer_not_forked(self):
        """A PROTECT-tagged consumer is excluded from rewire emission
        in Q3A (per existing planner contract). Under Q3B, it must
        also NOT trigger a fork for itself — protected layers stay
        unconformed."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero",    1920, 1080)],
            references=[
                (1, 3, 1, "h_normal"), (1, 3, 2, "h_protected"),
                (2, 3, 1, "h_other"),
            ],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [
                {"index": 1, "name": "h_normal", "uid": "Ln", "source_comp_id": 3},
                {"index": 2, "name": "h_protected", "uid": "Lp",
                 "source_comp_id": 3, "content_tag": "PROTECT"},
            ],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan(user_fork_overrides={"3": True})
        # Hero is_protected=True (because a consumer is PROTECT-tagged),
        # so will_be_skipped=True and no forks emit even under Q3B.
        # The fork path is gated by `not will_be_skipped`.
        for d in plan.duplicates:
            assert d.will_be_skipped is True
            # Forks aren't emitted at all when skipped — schema marker
            # is the default (False) on the PROTECT-skipped Q3A entry.
            assert d.fork_per_consumer is False
        assert plan.rewires == []


# ── Added Coverage Tests (DP-T1 to DP-T6) ──────────────────────────


def test_naming_collision_with_existing_project_comps():
    from core.duplication_planner import DuplicationPlanner
    
    # Structure already contains a comp named "Hero_TIKTOK"
    structure = _structure(
        comps=[(1, "MAIN", 1920, 1080), 
               (2, "Hero", 1920, 1080),
               (3, "Hero_TIKTOK", 1080, 1920)], # Existing collision target
        references=[(1, 2, 1, "hero")]
    )
    
    manifest = _manifest(
        "MAIN", 1920, 1080,
        [{"index": 1, "name": "hero", "uid": "L1", "source_comp_id": 2}]
    )
    
    planner = DuplicationPlanner(
        structure, manifest,
        target_dimensions=(1080, 1920),
        preset_id="TIKTOK"
    )
    
    # Base duplicate name is "Hero_TIKTOK". Since it already exists in the 
    # project comps, it should be resolved to "Hero_TIKTOK_v2".
    resolved_name, version = planner._unique_duplicate_name("Hero")
    assert resolved_name == "Hero_TIKTOK_v2"
    assert version == 2


def test_malformed_user_overrides_keys():
    from core.duplication_planner import DuplicationPlanner
    
    structure = _structure(
        comps=[(1, "MAIN", 1920, 1080), (2, "BG", 1920, 1080)],
        references=[(1, 2, 1, "bg")]
    )
    manifest = _manifest(
        "MAIN", 1920, 1080,
        [{"index": 1, "name": "bg", "uid": "u1", "source_comp_id": 2}]
    )
    
    # Pass an override with a non-int key. It should bypass the block without error.
    plan = DuplicationPlanner(
        structure, manifest,
        target_dimensions=(1080, 1920),
        preset_id="TIKTOK"
    ).plan(user_overrides={"invalid_key": True})
    
    # Ensure plan succeeds and is empty since BG is not shared
    assert plan.is_empty


def test_corrupt_comp_reference_ignored():
    from core.duplication_planner import DuplicationPlanner
    
    # Main references comp 99 which does not exist in comps registry
    structure = _structure(
        comps=[(1, "MAIN", 1920, 1080)],
        references=[(1, 99, 1, "missing")]
    )
    manifest = _manifest(
        "MAIN", 1920, 1080,
        [{"index": 1, "name": "missing", "uid": "u1", "source_comp_id": 99}]
    )
    
    # Force plan to duplicate 99
    plan = DuplicationPlanner(
        structure, manifest,
        target_dimensions=(1080, 1920),
        preset_id="TIKTOK"
    ).plan(user_overrides={"99": True})
    
    assert len(plan.duplicates) == 0


def test_aspect_changed_zero_height_protection():
    from core.duplication_planner import DuplicationPlanner
    
    structure = _structure(comps=[(1, "MAIN", 1920, 0)], references=[])
    manifest = _manifest("MAIN", 1920, 0, []) # src height = 0
    
    planner = DuplicationPlanner(
        structure, manifest,
        target_dimensions=(1920, 0), # target height = 0
        preset_id="TIKTOK"
    )
    # Must return False safely without raising ZeroDivisionError
    assert planner._aspect_changed() is False


def test_q3b_fork_with_mixed_protected_and_normal_consumers():
    from core.duplication_planner import DuplicationPlanner
    
    # Hero (comp 3) is referenced by two layers: one normal (L_normal) and one protected (L_protected)
    structure = _structure(
        comps=[(1, "ART_v14", 1920, 1080),
               (2, "ART_v15", 1920, 1080),
               (3, "Hero", 1920, 1080)],
        references=[
            (1, 3, 1, "h_normal"), (1, 3, 2, "h_protected"),
            (2, 3, 1, "h_other")
        ]
    )
    manifest = _manifest(
        "ART_v14", 1920, 1080,
        [
            {"index": 1, "name": "h_normal", "uid": "L_normal", "source_comp_id": 3},
            {"index": 2, "name": "h_protected", "uid": "L_protected", "source_comp_id": 3, "content_tag": "PROTECT"}
        ]
    )
    
    # Force user_overrides {"3": True} to override the PROTECT auto-skip for the comp,
    # and enable user_fork_overrides {"3": True} for Q3B forks.
    plan = DuplicationPlanner(
        structure, manifest,
        target_dimensions=(1080, 1920),
        preset_id="TIKTOK"
    ).plan(user_overrides={"3": True}, user_fork_overrides={"3": True})
    
    # We expect only ONE duplicate and ONE rewire to be generated (for the normal consumer "L_normal")
    # The protected consumer "L_protected" is excluded.
    assert len(plan.duplicates) == 1
    assert plan.duplicates[0].consumer_layer_uid == "L_normal"
    assert plan.duplicates[0].fork_per_consumer is True
    
    assert len(plan.rewires) == 1
    assert plan.rewires[0].conformed_layer_uid == "L_normal"
    assert plan.rewires[0].new_source_uid == "dup:3#L_normal"


def test_layer_nested_comp_id_fallback():
    from core.duplication_planner import DuplicationPlanner
    
    class DummySourceItem:
        kind = "comp"
        id = 42
        # nested_comp_id is intentionally missing
        
    class DummyLayer:
        source_item = DummySourceItem()
        
    assert DuplicationPlanner._layer_nested_comp_id(DummyLayer()) == 42


# ── Planner: GUIDE exclusion (mirrors PROTECT semantics) ─────────────


class TestPlannerGuideExclusion:
    def test_guide_precomp_auto_skipped(self):
        """Shared precomp whose wrapper layer carries content_tag='GUIDE'
        must be auto-skipped exactly like PROTECT: is_protected=True,
        will_be_skipped=True, no rewires emitted."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "CTA",     1920, 1080)],
            references=[(1, 3, 1, "cta"), (2, 3, 1, "cta")],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [{
                "index": 1, "name": "cta_guide", "uid": "L1",
                "source_comp_id": 3, "content_tag": "GUIDE",
            }],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan()
        assert len(plan.duplicates) == 1
        d = plan.duplicates[0]
        assert d.is_protected is True
        assert d.will_be_skipped is True
        assert plan.rewires == []

    def test_guide_consumer_excluded_from_rewire(self):
        """Shared precomp with two consumers: one tagged GUIDE, one
        untagged. With user_overrides to force execution, the GUIDE
        layer must NOT appear in plan.rewires; the untagged layer must."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero",     1920, 1080)],
            references=[(1, 3, 1, "hero"), (2, 3, 1, "hero")],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [
                {"index": 1, "name": "hero_guide", "uid": "La",
                 "source_comp_id": 3, "content_tag": "GUIDE"},
                {"index": 2, "name": "hero_other", "uid": "Lb",
                 "source_comp_id": 3},
            ],
        )
        # Override the GUIDE auto-skip so the precomp is duplicated.
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan(user_overrides={"3": True})
        assert plan.duplicates[0].will_be_skipped is False
        # Only the non-GUIDE layer gets a rewire.
        assert len(plan.rewires) == 1
        assert plan.rewires[0].conformed_layer_uid == "Lb"

    def test_guide_and_protect_both_skip(self):
        """Two shared precomps: one with a GUIDE consumer, one with a
        PROTECT consumer. Both must be will_be_skipped=True in a
        default (no overrides) plan."""
        from core.duplication_planner import DuplicationPlanner

        structure = _structure(
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Comp_A",  1920, 1080),
                   (4, "Comp_B",  1920, 1080)],
            references=[
                (1, 3, 1, "a"), (2, 3, 1, "a"),
                (1, 4, 2, "b"), (2, 4, 2, "b"),
            ],
        )
        manifest = _manifest(
            "ART_v14", 1920, 1080,
            [
                {"index": 1, "name": "a_guide",   "uid": "L1",
                 "source_comp_id": 3, "content_tag": "GUIDE"},
                {"index": 2, "name": "b_protect",  "uid": "L2",
                 "source_comp_id": 4, "content_tag": "PROTECT"},
            ],
        )
        plan = DuplicationPlanner(
            structure, manifest,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ).plan()
        assert len(plan.duplicates) == 2
        for d in plan.duplicates:
            assert d.will_be_skipped is True
        assert plan.rewires == []

