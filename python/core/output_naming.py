# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/output_naming.py
Slot 12.5 Stage D item 1 — Q4 Path A-minus output naming resolver.

Single source-of-truth for the names of every output comp Dimension
produces. The locked Q4 decision:

  - Output naming is suffix-based: `<source><suffix>`, default `_D`.
  - Per-preset authored template (`output_name_template` on `Target`),
    default `"{source}_D"`.
  - One function — `resolve_output_name` — substitutes the template,
    bumps `_v2` / `_v3` against the project's existing comp names, and
    returns the resolved name. Called from every site that produces
    a conformed-output comp name:
      - Root output comp (Babysitter's `setupWorkspace` — Stage D item 2)
      - Every duplicated precomp (DuplicationPlanner)
      - The conform/inject path's mirror-tree (Stage D item 2)
  - JSX never parses templates. Babysitter receives the resolved name
    over the bridge and writes it verbatim.

Before Stage D item 1, the codebase carried two naming conventions
(scope-doc 2026-05-16, "Asymmetry finding"):

  | Surface              | Convention                          | Style       |
  | -------------------- | ----------------------------------- | ----------- |
  | Root output comp     | `[DIMENSION] <preset_label>`        | hardcoded   |
  | Duplicated precomps  | `<original>_<W>x<H>_vN`             | suffix + WxH|

This module retires both. Both surfaces flow through
`resolve_output_name`. Existing presets that don't carry
`output_name_template` get the Pydantic default `"{source}_D"` on
read — backwards-compatible (additive Optional default).

PRINCIPLE — "seams, not stubs" (scope-doc Q4): this function is live
code that Slot 12.5 executes on every run. No stub functions, no
placeholder classes. A future Preset Library UI for visual template
editing attaches to a field that already exists and is already wired.
"""

from __future__ import annotations

from typing import List, NamedTuple, Optional, Set

from core.logger import log
from core.layer_utils import canon_tag as _canon
from models.conformed_manifest import MirrorRewire, MirrorTreeEntry
from models.project_structure import ProjectStructure
from models.scrape_manifest import ScrapeManifest
from models.target import Target


_EXCLUDED_TAGS = {"GUIDE", "PROTECT"}


DEFAULT_OUTPUT_NAME_TEMPLATE = "{source}_{preset}"
"""Default produces `<source>_<preset>` — e.g. `Final Comp_tiktok_video`
for a TikTok conform. Designers override per-preset by editing
`output_name_template` on the Target YAML. Tokens supported:
  {source}  — source comp name
  {preset}  — slug of the preset id (colons → underscores)
  {width}   — conform target width
  {height}  — conform target height
The pre-v6 default was `{source}_D` (just a `_D` suffix); v6 ships
with preset info so multi-target batches don't produce
indistinguishable `_D` comps next to each other."""


# Defensive cap on collision-suffix bumping. A project with `_v1` ...
# `_v999` of the same base name is pathological — but if we ever hit
# it the resolver bails out gracefully with the high version embedded,
# rather than spinning forever. Matches the v5.8 `_unique_duplicate_name`
# behavior the resolver replaces.
_COLLISION_BUMP_CAP = 999


# B2 — AE item-name character blacklist. ExtendScript's `Item.name`
# setter rejects these characters; substitute underscore to prevent
# silent failures in setupWorkspace and mirror-tree naming.
import re as _re


_AE_ILLEGAL_CHARS_RE = _re.compile(r'[:\*\?"<>|/\\]+')


def sanitize_comp_name(name: str) -> str:
    """Strip characters illegal in AE composition names and collapse
    repeated underscores / leading-trailing whitespace.

    After Effects rejects: ``* ? " < > | / \\ :``
    This function replaces runs of those characters with a single
    underscore and strips leading/trailing whitespace.
    """
    cleaned = _AE_ILLEGAL_CHARS_RE.sub("_", name)
    cleaned = _re.sub(r"_+", "_", cleaned)
    return cleaned.strip().strip("_") or "Untitled"


class ResolvedName(NamedTuple):
    """Result of `resolve_output_name`. `name` is the final string the
    caller writes into the manifest / sends over the bridge; `version`
    is 1 when no collision occurred and `>= 2` when the resolver bumped
    a `_vN` suffix to avoid a name already in `existing_names`.

    Callers that only care about the name can read `.name` directly
    (or unpack — it's a NamedTuple). Callers that need to surface the
    version (DuplicationPlanner's `PrecompDuplicate.name_version`) read
    `.version`.
    """
    name: str
    version: int


def resolve_output_name(
    source_name: str,
    existing_names: Set[str],
    *,
    template: str = DEFAULT_OUTPUT_NAME_TEMPLATE,
    target_width: int = 0,
    target_height: int = 0,
    preset_id: str = "",
) -> ResolvedName:
    """Slot 12.5 Stage D item 1 — resolve a conformed-output comp name.

    Steps:
      1. Substitute the template against {source}/{width}/{height}.
         Unknown tokens pass through unchanged (forward-compat).
      2. If the resulting base name is free, return it (version=1).
      3. Otherwise bump a `_vN` suffix (`_v2`, `_v3`, ...) until a free
         slot is found, capped at `_COLLISION_BUMP_CAP`.

    The function does NOT reserve the returned name into
    `existing_names`. Callers must do that themselves before resolving
    a sibling — otherwise two sibling resolutions could return the same
    name. See `DuplicationPlanner.plan` for the canonical reservation
    pattern.

    Parameters:
      source_name:    name of the source comp being conformed.
      existing_names: every CompItem name in the project (from
                      `project_structure.json::comps`), Dimension-
                      created AND externally-created. The collision
                      check guarantees nothing already on disk gets
                      clobbered.
      template:       template string with `{source}`/`{width}`/`{height}`
                      tokens. Defaults to `"{source}_D"`.
      target_width:   the conform target's width (substituted into
                      `{width}`). Required only when the template
                      references it; default 0 is fine when it doesn't.
      target_height:  same, for `{height}`.

    Returns:
      ResolvedName(name, version).
    """
    base = sanitize_comp_name(
        _apply_template(template, source_name, target_width, target_height, preset_id)
    )
    if base not in existing_names:
        return ResolvedName(base, 1)
    version = 2
    while True:
        candidate = f"{base}_v{version}"
        if candidate not in existing_names:
            return ResolvedName(candidate, version)
        version += 1
        if version > _COLLISION_BUMP_CAP:
            # Pathological. Return the high-version candidate so the
            # caller sees the issue (the modal renders `vN` chip; an
            # audit can flag it) rather than spinning.
            return ResolvedName(f"{base}_v{version}", version)


def resolve_output_name_for_preset(
    source_name: str,
    preset: Target,
    existing_names: Set[str],
) -> ResolvedName:
    """Convenience wrapper that pulls `template`/`width`/`height` off a
    `Target` and delegates to `resolve_output_name`. Matches the
    scope-doc's `resolve_output_name(source_name, preset, existing_names)`
    signature; the underlying function takes individual fields so
    callers without a full Target (e.g. callers with only
    `target_dimensions=(w, h)`) can still resolve."""
    return resolve_output_name(
        source_name=source_name,
        existing_names=existing_names,
        template=preset.output_name_template,
        target_width=preset.width,
        target_height=preset.height,
        preset_id=getattr(preset, "id", "") or "",
    )


# ──────────────────────────────────────────────────────────────────────
#  Mirror-tree spec builder (Slot 12.5 Stage D Item 2)
# ──────────────────────────────────────────────────────────────────────


def clean_preset_name(preset_id: str) -> str:
    """Get a clean, artist-friendly brand/preset name.
    e.g. 'builtin:tiktok' -> 'TikTok'
         'builtin:tiktok_video' -> 'TikTok Video'
         'builtin:youtube_shorts' -> 'YouTube Shorts'
    """
    if not preset_id:
        return "Conform"
    s = preset_id
    for prefix in ("builtin:", "custom:", "user:"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break

    # Mapping for common presets to be very premium and well-branded
    brand_mappings = {
        "tiktok": "TikTok",
        "tiktok_video": "TikTok Video",
        "youtube": "YouTube",
        "youtube_shorts": "YouTube Shorts",
        "instagram": "Instagram",
        "instagram_reels": "Instagram Reels",
        "facebook": "Facebook",
    }

    s_lower = s.lower()
    if s_lower in brand_mappings:
        return brand_mappings[s_lower]

    # General title-casing fallback: replace any non-alphanumeric chars with spaces, capitalize each word
    words = _re.split(r"[^A-Za-z0-9]+", s)
    cleaned = " ".join(w.capitalize() for w in words if w)
    return cleaned or "Conform"


def format_session_timestamp(timestamp_str: str) -> str:
    """Format timestamp_str from YYYY-MM-DD_HHMMSS or YYYY-MM-DD-HHMMSS to YYYY-MM-DD HH:MM
    e.g. '2026-06-05_105722' -> '2026-06-05 10:57'
         '2026-05-21_093534' -> '2026-05-21 09:35'
    """
    if not timestamp_str:
        return ""

    # Try to match YYYY-MM-DD_HHMMSS or YYYY-MM-DD_HHMM
    m = _re.match(r"^(\d{4}-\d{2}-\d{2})[_-](\d{2})(\d{2})\d*$", timestamp_str)
    if m:
        return f"{m.group(1)} {m.group(2)}:{m.group(3)}"

    # Fallback to basic cleanup
    cleaned = timestamp_str.replace("_", " ")
    return cleaned


def session_bin_path(preset_id: str, timestamp_str: str) -> str:
    """Compute the target bin path for one conform session.

    Artist-friendly format: e.g. "From Dimensions/TikTok (2026-05-21 09:35)"
    """
    name = clean_preset_name(preset_id)
    formatted_ts = format_session_timestamp(timestamp_str)
    if formatted_ts:
        return f"From Dimensions/{name} ({formatted_ts})"
    return f"From Dimensions/{name}"



def build_mirror_tree_spec(
    manifest: ScrapeManifest,
    project_structure: ProjectStructure,
    preset: Target,
    *,
    existing_comp_names: Optional[Set[str]] = None,
    preserve_nested_dims: bool = False,
    sealed_cids: Optional[Set[int]] = None,
) -> List[MirrorTreeEntry]:
    """Slot 12.5 Stage D Item 2 — build the mirror-tree spec for one
    conform session.

    U3 Phase B — `sealed_cids` (optional) carries the ENGINE's actual
    `sealed_precomp_cids` set for precise per-comp `keep_source_dims`.
    When given, `keep_source_dims = cid in sealed_cids` (byte-identical
    to today wherever the resolution never produces a *partial* seal —
    which is every case so far; this closes the gap for a future
    partial-seal archetype). `preserve_nested_dims` (the coarse bool)
    is KEPT for back-compat — `test_scene_preserve_layout.py`'s `_spec()`
    helper calls it positionally; do not remove/rename. When
    `sealed_cids` is omitted (None), falls back to the original
    `preserve_nested_dims and not is_root` coercion, unchanged.

    Walks the scrape's unique `containing_comp_id` set in BFS-visit
    order (the order Stage B's recursive walker emitted layers in).
    For each unique comp:
      1. Looks up the source name + AE id in `project_structure.comps`.
      2. Resolves the output name via `resolve_output_name_for_preset`
         with collision-bumping against `existing_comp_names`.
      3. Reserves the resolved name in `existing_comp_names` so the
         next iteration's bump-check sees it (caller-must-reserve
         contract from `resolve_output_name`).
      4. Emits a `MirrorTreeEntry` with `is_root=True` for the active
         comp; `False` for every nested precomp.

    De-dupe per Q3A: a shared precomp referenced N times produces ONE
    entry. The first-seen layer determines the entry; subsequent
    references to the same comp_id do not re-emit.

    Backwards-compat for legacy 5.0 manifests (no `containing_comp_id`
    on any layer): returns a single entry for the active comp marked
    is_root=True. This is the 87N flat-regression case — N=1 mirror.

    Returns the list in root-first BFS order. Caller passes the list
    to `slice_and_export` which writes it into `chunk_manifest.json`
    for Babysitter to consume.
    """
    if existing_comp_names is None:
        existing_comp_names = {c.name for c in project_structure.comps}

    comps_by_id = project_structure.comps_by_id()
    active_comp_name = manifest.project_info.name if manifest.project_info else ""

    # Build a set of nested comp IDs whose wrapper layer is GUIDE or PROTECT.
    # These precomps are structural overlays — never inject into them.
    # AE scrapes populate source_item.id (the precomp's own comp id); the
    # legacy nested_comp_id field is null in most real manifests, so fall
    # back to source_item.id when nested_comp_id is absent.
    guide_comp_ids: Set[int] = set()
    for _lyr in manifest.layers:
        if (
            _lyr.source_item is not None
            and _lyr.source_item.kind == "comp"
            and _canon(_lyr.content_tag) in _EXCLUDED_TAGS
        ):
            _cid = _lyr.source_item.nested_comp_id or _lyr.source_item.id
            if _cid is not None:
                guide_comp_ids.add(_cid)

    # Walk manifest.layers in order; track first-seen unique comp ids.
    # Stage B's BFS walker emits layers root-first, so first-seen order
    # is BFS order. Layers without containing_comp_id (legacy 5.0) all
    # fall into the None bucket → one entry for the active comp.
    seen_ids: Set[Optional[int]] = set()
    ordered_ids: List[Optional[int]] = []

    if manifest.scrape_meta and getattr(manifest.scrape_meta, "scraped_comp_ids", None):
        for cid in manifest.scrape_meta.scraped_comp_ids:
            if cid in seen_ids:
                continue
            if cid in guide_comp_ids:
                continue
            seen_ids.add(cid)
            ordered_ids.append(cid)

    # Fallback / safety pass for layers
    for layer in manifest.layers:
        cid = layer.containing_comp_id
        if cid in seen_ids:
            continue
        if cid in guide_comp_ids:
            continue
        seen_ids.add(cid)
        ordered_ids.append(cid)

    # If the walker found nothing (empty manifest / no containing_comp_id
    # on any layer), fall back to the active comp via project_structure
    # name lookup. This preserves the 87N flat-regression behavior:
    # legacy 5.0 manifests produce exactly one mirror entry.
    if not ordered_ids or all(cid is None for cid in ordered_ids):
        active_node = next(
            (c for c in project_structure.comps if c.name == active_comp_name),
            None,
        )
        if active_node is None:
            log.warning(
                "Mirror-tree builder: active comp not in project_structure",
                extra={"active_comp_name": active_comp_name},
            )
            return []
        resolved = resolve_output_name_for_preset(
            active_node.name, preset, existing_comp_names
        )
        existing_comp_names.add(resolved.name)
        return [MirrorTreeEntry(
            source_comp_name=active_node.name,
            source_comp_id=active_node.id,
            output_name=resolved.name,
            name_version=resolved.version,
            is_root=True,
        )]

    entries: List[MirrorTreeEntry] = []
    for cid in ordered_ids:
        if cid is None:
            # Mixed legacy / v5.1 manifest — None-bucket layers belong
            # to the active comp. Skip the None bucket; the named
            # comps below cover the real entries.
            continue
        node = comps_by_id.get(cid)
        if node is None:
            log.warning(
                "Mirror-tree builder: containing_comp_id not in project_structure",
                extra={"missing_comp_id": cid},
            )
            continue
        resolved = resolve_output_name_for_preset(
            node.name, preset, existing_comp_names
        )
        existing_comp_names.add(resolved.name)
        _entry_is_root = (node.name == active_comp_name)
        entries.append(MirrorTreeEntry(
            source_comp_name=node.name,
            source_comp_id=node.id,
            output_name=resolved.name,
            name_version=resolved.version,
            is_root=_entry_is_root,
            # Scene-preserve (2026-07-02): nested precomps are sealed
            # units — their mirrors keep source dimensions so the
            # design inside is never re-laid-out. Root always resizes.
            # U3 Phase B: prefer the PRECISE sealed_cids set when given
            # (a partial seal would otherwise be lost to the coarse
            # bool coercion); fall back to the legacy bool otherwise.
            keep_source_dims=(
                (cid in sealed_cids) if sealed_cids is not None
                else (preserve_nested_dims and not _entry_is_root)),
        ))

    return entries


# ──────────────────────────────────────────────────────────────────────
#  Mirror-rewires spec builder (Slot 12.5 Stage D Item 3)
# ──────────────────────────────────────────────────────────────────────


def build_mirror_rewires_spec(
    manifest: ScrapeManifest,
    project_structure: ProjectStructure,
) -> List[MirrorRewire]:
    """Slot 12.5 Stage D Item 3 — build the mirror-tree wrapper rewires
    for one conform session.

    Walks the scrape manifest. For every layer whose source is a
    CompItem (i.e. a precomp wrapper), emits one `MirrorRewire` that
    tells Babysitter to repoint the wrapper from the original source
    comp to the target-dimensioned mirror copy.

    The emitted entries identify:
      - `wrapper_layer_uid` — the wrapper's UID (Babysitter finds it
        via `findLayerByUID`)
      - `wrapper_in_source_comp_name` — the source comp the wrapper
        lives in (which mirror comp Babysitter searches)
      - `target_mirror_source_name` — the source comp the wrapper
        points at (which mirror comp the wrapper rewires to)

    Skipped on:
      - Layers without a precomp source (footage / solids / nulls)
      - Layers whose source comp isn't in `project_structure` (deleted
        or external — a warning logs, the wrapper stays unrewired)
      - Layers without a UID (no anchor for `findLayerByUID`)
      - Layers in a containing comp not in `project_structure` (same
        warning)

    Q3A default (the common case): one rewire per wrapper, target is
    the shared mirror keyed by source name. Q3B forks are NOT emitted
    here in Item 3 — when a designer flips a precomp to Q3B via the
    preflight modal, the planner's existing LayerRewire mechanism
    carries fork info, and a follow-up step unifies both surfaces.
    Item 3 ships Q3A; the schema's `fork_consumer_layer_uid` field is
    forward-compat scaffolding (Babysitter resolution handles both
    shapes; the orchestrator just doesn't yet emit forks).

    Returns the list in scrape-walk order (BFS root-first per Stage B).
    For 87N (flat comp, no precomp wrappers) the list is empty — the
    rewire phase no-ops and the flat conform stays byte-identical.
    """
    comps_by_id = project_structure.comps_by_id()
    active_comp_name = manifest.project_info.name if manifest.project_info else ""

    rewires: List[MirrorRewire] = []
    for layer in manifest.layers:
        src_item = getattr(layer, "source_item", None)
        if src_item is None:
            continue
        if getattr(src_item, "kind", None) != "comp":
            continue
        # Skip GUIDE/PROTECT wrapper layers — these are structural overlays
        # (e.g. safe-zone checkers) that should never be injected into.
        if _canon(layer.content_tag) in _EXCLUDED_TAGS:
            continue
        # Resolve the source-of-the-wrapper. SourceItem.nested_comp_id
        # is the canonical link to the target comp; `id` carries the
        # SourceItem's own id when nested_comp_id is None (legacy).
        target_comp_id = (
            getattr(src_item, "nested_comp_id", None)
            or getattr(src_item, "id", None)
        )
        if target_comp_id is None:
            continue
        target_node = comps_by_id.get(target_comp_id)
        if target_node is None:
            log.warning(
                "Mirror-rewires builder: wrapper source comp missing "
                "from project_structure",
                extra={
                    "wrapper_uid": layer.uid,
                    "wrapper_name": layer.name,
                    "missing_target_comp_id": target_comp_id,
                },
            )
            continue

        if not layer.uid:
            log.warning(
                "Mirror-rewires builder: wrapper has no UID — "
                "cannot anchor findLayerByUID",
                extra={"wrapper_name": layer.name},
            )
            continue

        # Identify which mirror comp contains this wrapper.
        containing_comp_id = getattr(layer, "containing_comp_id", None)
        if containing_comp_id is None:
            # Legacy 5.0 manifest — single mirror = active comp. The
            # wrapper lives in the active comp's mirror.
            containing_name = active_comp_name
        else:
            containing_node = comps_by_id.get(containing_comp_id)
            if containing_node is None:
                log.warning(
                    "Mirror-rewires builder: wrapper containing comp "
                    "missing from project_structure",
                    extra={
                        "wrapper_uid": layer.uid,
                        "missing_containing_comp_id": containing_comp_id,
                    },
                )
                continue
            containing_name = containing_node.name

        rewires.append(MirrorRewire(
            wrapper_layer_uid=layer.uid,
            wrapper_layer_name=layer.name,
            wrapper_in_source_comp_name=containing_name,
            target_mirror_source_name=target_node.name,
            fork_consumer_layer_uid=None,   # Q3A default; Q3B is future scope
        ))

    return rewires


# ──────────────────────────────────────────────────────────────────────
#  Internal — template substitution
# ──────────────────────────────────────────────────────────────────────


def _apply_template(
    template: str,
    source_name: str,
    target_width: int,
    target_height: int,
    preset_id: str = "",
) -> str:
    """Substitute supported tokens. Unknown tokens pass through
    unchanged so a template authored against a future token set
    doesn't break on older Dimension versions — the unsubstituted
    `{token}` falls through as literal text, which is visible in the
    output name and easy to diagnose.

    Currently supported tokens:
      {source}  — source comp name
      {preset}  — slug of preset_id (colons + non-alnum → underscores,
                  trimmed). e.g. "builtin:tiktok_video" → "tiktok_video"
                  (the "builtin:" prefix is stripped because every
                  built-in target carries it and it adds noise).
      {width}   — conform target width
      {height}  — conform target height
      {date}    — today's date in ISO YYYY-MM-DD format (e.g. 2026-08-26)
    """
    from datetime import date as _date
    return (
        template
        .replace("{source}", source_name)
        .replace("{preset}", _slug_preset(preset_id))
        .replace("{width}", str(target_width))
        .replace("{height}", str(target_height))
        .replace("{date}", _date.today().isoformat())
    )


def _slug_preset(preset_id: str) -> str:
    """Slugify a preset id for use in filenames + AE bin names.
    `builtin:tiktok_video` → `tiktok_video`; `custom:my_format` →
    `my_format`; raw values fall through after non-alnum → `_`."""
    if not preset_id:
        return "conform"
    s = preset_id
    # Drop the bundle prefix ("builtin:" / "custom:" / "user:") so the
    # slug reads as just the format name.
    for prefix in ("builtin:", "custom:", "user:"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    # Replace any remaining colon / slash / space with underscore.
    return _re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_") or "conform"
