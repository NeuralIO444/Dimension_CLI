# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
core/placement_units.py — U1: the diagnostic Placement Unit tree.

The root-cause finding (docs/roadmap/PLACEMENT-MODEL-root-cause-and-
unification.md): design intent lives in a tree of UNITS — sealed
precomps, camera scenes, lockup groups, lone layers — but the pipeline
has historically reasoned per-layer-vs-frame. U1 makes the tree
explicit and VISIBLE (report card + log) without changing any conform
behavior. U2/U3 will route the conform and its sibling passes through
this same tree; U4 surfaces it in the panel.

Pure analysis: reads the (post-survey) manifest, mutates nothing,
never raises (returns a degenerate single-unit report on any internal
error — with the error recorded, per the loud-failures policy).

Unit kinds:
  root_frame   — the conform target's own comp (container of all units)
  camera_scene — camera + 3D layers in one comp (detect_3d_camera_scenes)
  precomp      — a nested comp; sealed unit, child edges via wrapper
                 layers' source_item (the nesting-doll chain)
  group        — 2+ same-tag spatially-adjacent 2D layers (spatial_group_key)
  singleton    — a lone layer (degenerates to today's per-layer tag behavior)

`anchor_auto` is DIAGNOSTIC ONLY: what `--layout auto` would do with
the unit. Nothing here is consumed by scale_engine*/gravity/SOE in U1.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from core.classify import detect_3d_camera_scenes_by_comp, is_layer_root
from core.layer_utils import canon_tag
from core.logger import log

# Tags whose layers never participate in placement decisions.
_PROTECTED_TAGS = {"GUIDE", "PROTECT"}

# Fraction of max(orig_w, orig_h) used as the single-linkage spatial clustering
# threshold in the group-aware gravity pre-pass. Layers within this distance of
# any existing cluster member join the same cluster; layers beyond it start a
# new cluster. 0.40 = 40% of the larger comp dimension.
# (U2 Phase 1: moved here from scale_engine_narrow so the unit tree and the
# engine share ONE clustering implementation.)
SPATIAL_CLUSTER_THRESHOLD_FRACTION = 0.40


def single_linkage_clusters(
    group_positions: Dict[tuple, list],
    threshold: float,
) -> Tuple[Dict[tuple, tuple], Dict[tuple, int], Dict[tuple, tuple]]:
    """Single-linkage spatial sub-clustering within each same-tag group.

    U2 Phase 1 — moved VERBATIM from scale_engine_narrow.py's group-aware
    gravity pre-pass (the 2026-06-29 collapse-bug fix + Stage 4 spatial
    clusters) so the placement-unit tree and the conform engine derive
    groups from the same single implementation. Do NOT reimplement this
    math elsewhere — see the CLAUDE.md gravity sharp edges.

    Args:
        group_positions: {(containing_comp_id, canonical_tag): [(x, y,
            layer_index), ...]} — comp-scoped keys ONLY (Slot 13 edge).
        threshold: max Euclidean distance for a position to join a
            cluster (single-linkage: distance to ANY existing member).

    Returns (layer_centroids, layer_cluster_sizes, group_centroids):
        layer_centroids: {(comp_id, layer_index): (cx, cy)} — centroid of
            THIS layer's cluster.
        layer_cluster_sizes: {(comp_id, layer_index): int} — member count
            of THIS layer's cluster.
        group_centroids: {(comp_id, tag): (cx, cy)} — centroid of the
            LARGEST cluster per group.

    A group of size 1 produces exactly 1 cluster whose centroid is the
    layer's own position — byte-identical to pre-fix behavior.
    """
    _layer_centroids: Dict[tuple, tuple] = {}
    _layer_cluster_sizes: Dict[tuple, int] = {}
    _group_centroids: Dict[tuple, tuple] = {}

    for _g_key, _g_positions in group_positions.items():
        _comp_id_g, _tag_g = _g_key
        _n = len(_g_positions)

        if _n == 1:
            # Trivial case: group of 1 is always 1 cluster.
            _li = _g_positions[0][2]  # (x, y, layer_index)
            _lk = (_comp_id_g, _li)
            _layer_centroids[_lk] = (_g_positions[0][0], _g_positions[0][1])
            _layer_cluster_sizes[_lk] = 1
            _group_centroids[_g_key] = (_g_positions[0][0], _g_positions[0][1])
            continue

        # Single-linkage clustering for groups of size 2+.
        # cluster_ids[i] = integer label for position i.
        # Start: each position is its own cluster (label = i).
        cluster_ids = list(range(_n))

        for _i in range(_n):
            for _j in range(_i + 1, _n):
                _pi = _g_positions[_i]
                _pj = _g_positions[_j]
                _dist = ((_pi[0] - _pj[0]) ** 2 + (_pi[1] - _pj[1]) ** 2) ** 0.5
                if _dist <= threshold:
                    # Merge cluster of j into cluster of i (path compression).
                    _cj = cluster_ids[_j]
                    _ci = cluster_ids[_i]
                    if _ci != _cj:
                        for _k in range(_n):
                            if cluster_ids[_k] == _cj:
                                cluster_ids[_k] = _ci

        # Compute per-cluster centroids and sizes.
        _cluster_members: Dict[int, list] = {}
        for _i, _cid in enumerate(cluster_ids):
            _cluster_members.setdefault(_cid, []).append(_i)

        # Assign per-layer dicts.
        _largest_cluster_cid = max(_cluster_members, key=lambda c: len(_cluster_members[c]))
        for _cid, _member_indices in _cluster_members.items():
            _mx = sum(_g_positions[_mi][0] for _mi in _member_indices) / len(_member_indices)
            _my = sum(_g_positions[_mi][1] for _mi in _member_indices) / len(_member_indices)
            _msz = len(_member_indices)
            for _mi in _member_indices:
                _li = _g_positions[_mi][2]  # layer_index stored in tuple
                _lk = (_comp_id_g, _li)
                _layer_centroids[_lk] = (_mx, _my)
                _layer_cluster_sizes[_lk] = _msz

        # _group_centroids gets the LARGEST cluster's centroid for this (comp_id, tag).
        _lc_members = _cluster_members[_largest_cluster_cid]
        _lc_cx = sum(_g_positions[_mi][0] for _mi in _lc_members) / len(_lc_members)
        _lc_cy = sum(_g_positions[_mi][1] for _mi in _lc_members) / len(_lc_members)
        _group_centroids[_g_key] = (_lc_cx, _lc_cy)

        if _n > 1:
            log.debug(
                "scale.gravity.group",
                extra={
                    "comp_id": _comp_id_g,
                    "tag": _tag_g,
                    "group_size": _n,
                    "cluster_count": len(_cluster_members),
                    "centroid": (round(_lc_cx, 2), round(_lc_cy, 2)),
                },
            )

    return _layer_centroids, _layer_cluster_sizes, _group_centroids


# ── U2 Phase 2 — the placement resolution ───────────────────────────────
# The single source object for every placement decision in one conform
# run. The engine builds it once (ScaleEngine.build_units); the narrow
# rule set, the orchestrator's keyframe seal, the mirror-tree sizing and
# the SOE gate all READ it instead of recomputing their own slice of the
# tree (the five scattered mechanisms of
# docs/roadmap/PLACEMENT-MODEL-root-cause-and-unification.md).


@dataclass
class PlacementResolution:
    """Placement decisions for one conform run. Comp-scoped tuple keys
    ONLY (Slot 13 sharp edge) — never flat layer indices."""
    root_cid: Optional[int] = None
    # Nested precomp cids whose internals are sealed (statics pass
    # through, keys not emitted, mirrors keep source dims).
    sealed_precomp_cids: set = field(default_factory=set)
    # Comps whose layers preserve composed geometry (tag gravity
    # bypassed for everything except FILL).
    preserving_scene_cids: set = field(default_factory=set)
    # Group-aware gravity clusters (the 2026-06-29 collapse-bug fix):
    # {(containing_comp_id, layer_index): (cx, cy)} / {...: size}.
    layer_centroids: Dict[tuple, tuple] = field(default_factory=dict)
    layer_cluster_sizes: Dict[tuple, int] = field(default_factory=dict)
    # Largest-cluster centroid per (comp_id, tag).
    group_centroids: Dict[tuple, tuple] = field(default_factory=dict)
    # Comps whose cameras force depth mode K→S (scene scales uniformly).
    camera_depth_force_s_cids: set = field(default_factory=set)
    # True when any unit preserves as a scene — gates SOE and the
    # scene-preserve progress message (same truth table as the legacy
    # global scene_preserve_active flag).
    any_preserving_scene_unit: bool = False
    layout: str = "tags"
    # detect_3d_camera_scenes_by_comp output (comp cids that ARE scenes).
    scenes: set = field(default_factory=set)
    # U3 Phase A — nested precomp cid -> (comp_id, index) of its wrapper
    # layer (the nesting-doll edge). SOE's whole-unit translate uses this
    # to find a sealed precomp's own AABB (to check it against the
    # safe-zone mask) without re-deriving the edge list.
    wrapper_keys: Dict[int, tuple] = field(default_factory=dict)


def _layer_indices_for(manifest: Any) -> set:
    """Build the root/child lookup set exactly the way
    ScaleEngine.conform() does: comp-scoped tuples for recursive
    manifests, flat indices for legacy 5.0 flat manifests."""
    layers = getattr(manifest, "layers", []) or []
    has_containing = any(
        getattr(l, "containing_comp_id", None) is not None for l in layers)
    if has_containing:
        return {(getattr(l, "containing_comp_id", None), l.index)
                for l in layers}
    return {l.index for l in layers}


def baseline_rule_resolver(layer: Any):
    """Default gravity-rule resolver (no studio profile) — mirrors
    ScaleEngine._resolve_gravity_rule with profile=None. The engine
    passes its own bound resolver so profile rules apply identically;
    this default exists for the profile-less diagnostic path."""
    tag = getattr(layer, "content_tag", None)
    if tag and tag.upper() not in ("GUIDE", "PROTECT"):
        from core.gravity import baseline_rule_for
        rule = baseline_rule_for(tag)
        if rule is not None:
            return rule, "baseline"
    return None, None


def gather_gravity_group_positions(
    manifest: Any, *, layer_indices: set, rule_resolver,
    excluded_keys: Optional[set] = None,
) -> Dict[tuple, list]:
    """Gather same-tag root-layer positions per (comp_id, canonical tag).

    U2 Phase 2 — moved verbatim from scale_engine_narrow's group-aware
    gravity pre-pass gather loop. Only layers with a resolved gravity
    rule (i.e. that will actually go through apply_gravity) are
    included; GUIDE/PROTECT and child layers are excluded.

    `excluded_keys` (PR-V2) drops `(comp_id, index)` layers from group
    membership entirely — used for `variant:`-inactive layers, which must
    not contribute to a group's centroid. A hidden stacked headline left
    in the group still drags the centroid, so the VISIBLE headline lands
    wrong: the same collapse-class bug the group-aware fix exists to
    prevent, arriving through a side door. Defaults to None, which is
    byte-identical to the pre-PR-V2 behaviour.

    This function is the single choke point for group membership — both
    `compute_placement_resolution` and the engine's degraded fallback
    path reach it — so the exclusion cannot apply to one and not the
    other."""
    _excluded = excluded_keys or set()
    _group_positions: Dict[tuple, list] = {}
    for _g_layer in getattr(manifest, "layers", []) or []:
        _g_tag = canon_tag(getattr(_g_layer, "content_tag", None))
        if not _g_tag or _g_tag in ("GUIDE", "PROTECT"):
            continue
        _g_parent_idx = getattr(_g_layer, "parent_index", -1)
        _g_comp_id = getattr(_g_layer, "containing_comp_id", None)
        if (_g_comp_id, int(getattr(_g_layer, "index", 0) or 0)) in _excluded:
            continue
        if not is_layer_root(_g_parent_idx, layer_indices, _g_comp_id):
            continue
        _g_rule, _ = rule_resolver(_g_layer)
        if _g_rule is None:
            continue
        _g_pos = _g_layer.position or [0.0, 0.0, 0.0]
        _group_positions.setdefault((_g_comp_id, _g_tag), []).append(
            (_g_pos[0], _g_pos[1], _g_layer.index)
        )
    return _group_positions


def compute_group_clusters(
    manifest: Any,
    *,
    orig_w: int,
    orig_h: int,
    rule_resolver=None,
    layer_indices: Optional[set] = None,
    excluded_keys: Optional[set] = None,
) -> Tuple[Dict[tuple, tuple], Dict[tuple, int], Dict[tuple, tuple]]:
    """Gather → cluster → style-adjust: the full group-aware gravity
    pre-pass as one pure function (U2 — shared by the placement
    resolution and the engine's degraded-fallback path).

    Returns (layer_centroids, layer_cluster_sizes, group_centroids) —
    see single_linkage_clusters."""
    if rule_resolver is None:
        rule_resolver = baseline_rule_resolver
    if layer_indices is None:
        layer_indices = _layer_indices_for(manifest)

    group_positions = gather_gravity_group_positions(
        manifest, layer_indices=layer_indices, rule_resolver=rule_resolver,
        excluded_keys=excluded_keys)
    threshold = max(orig_w, orig_h) * SPATIAL_CLUSTER_THRESHOLD_FRACTION
    layer_centroids, layer_cluster_sizes, group_centroids = (
        single_linkage_clusters(group_positions, threshold))

    return layer_centroids, layer_cluster_sizes, group_centroids


def precomp_wrapper_keys(layers: List[Any]) -> Dict[int, Tuple[Optional[int], int]]:
    """Nested-comp cid → (comp_id, index) of the wrapper layer whose
    ``source_item`` points at that nested comp (the nesting-doll chain).

    U3 Phase A — extracted from ``_build()``'s inline wrapper-lookup loop
    (code motion, byte-identical) so SOE's whole-unit translate can find
    a sealed precomp's wrapper layer without re-deriving the edge list.
    Both ``_build()`` and ``compute_placement_resolution()`` call this —
    one source of the nesting-doll edges, per the "one object every
    consumer reads" contract.
    """
    keys: Dict[int, Tuple[Optional[int], int]] = {}
    for l in layers:
        si = getattr(l, "source_item", None)
        if si is not None and getattr(si, "kind", "") == "comp":
            ccid = getattr(si, "nested_comp_id", None) or getattr(si, "id", None)
            if ccid is not None:
                keys[ccid] = (getattr(l, "containing_comp_id", None),
                              int(getattr(l, "index", 0) or 0))
    return keys


def compute_placement_resolution(
    manifest: Any,
    *,
    layout: str,
    rule_resolver=None,
    orig_w: Optional[int] = None,
    orig_h: Optional[int] = None,
    scenes: Optional[set] = None,
    excluded_keys: Optional[set] = None,
) -> PlacementResolution:
    """Build the placement resolution for one conform run (pure).

    U2 Phase 2 populates the scene/seal sets to reproduce the legacy
    GLOBAL scene-preserve semantics exactly (layout == "scene", or
    layout == "auto" with ANY 3D camera scene in the manifest, flips
    every set). Per-comp resolution under auto is Phase 6.
    """
    layers = list(getattr(manifest, "layers", []) or [])
    pi = getattr(manifest, "project_info", None)
    if orig_w is None:
        orig_w = int(getattr(pi, "width", 0) or 0) # type: ignore
    if orig_h is None:
        orig_h = int(getattr(pi, "height", 0) or 0)
    if scenes is None:
        scenes = detect_3d_camera_scenes_by_comp(layers)

    layer_indices = _layer_indices_for(manifest)
    layer_centroids, layer_cluster_sizes, group_centroids = (
        compute_group_clusters(
            manifest, orig_w=orig_w, orig_h=orig_h,
            rule_resolver=rule_resolver, layer_indices=layer_indices,
            excluded_keys=excluded_keys))

    root_cid = next(
        (getattr(l, "containing_comp_id", None) for l in layers
         if getattr(l, "containing_comp_id", None) is not None), None)

    # U4 Phase 4: Apply 'create' overrides from the sidecar file.
    # This forces a group of layers to share a centroid, making them a rigid unit.
    # This must happen AFTER initial clustering but BEFORE scene/seal resolution.
    overrides = getattr(manifest, 'unit_overrides', None) or {}
    if overrides:
        layers_by_uid = {l.uid: l for l in layers if l.uid}
        for unit_id, override in overrides.items():
            if override.get("action") == "create":
                members = override.get("members", [])
                if len(members) < 2:
                    continue

                member_layers = [layers_by_uid[uid] for uid in members if uid in layers_by_uid]
                if not member_layers:
                    continue

                # Compute the centroid of the new manual unit
                positions = [l.position for l in member_layers if l.position]
                if not positions:
                    continue
                
                cx = sum(p[0] for p in positions) / len(positions)
                cy = sum(p[1] for p in positions) / len(positions)
                
                # Force all members to share this new centroid
                for layer in member_layers:
                    layer_centroids[(layer.containing_comp_id, layer.index)] = (cx, cy)
                    layer_cluster_sizes[(layer.containing_comp_id, layer.index)] = len(member_layers)
            
            elif override.get("action") == "dissolve":
                # Find the unit in the report to get its members without infinite recursion
                temp_res = PlacementResolution(
                    root_cid=root_cid,
                    sealed_precomp_cids=set(),
                    preserving_scene_cids=set(),
                    layer_centroids=dict(layer_centroids),
                    layer_cluster_sizes=dict(layer_cluster_sizes),
                    camera_depth_force_s_cids=set(),
                    any_preserving_scene_unit=False,
                    layout=layout,
                    scenes=scenes,
                )
                unit_report = _build(manifest, resolution=temp_res, overrides=None)
                target_unit = next((u for u in unit_report.units if u.unit_id == unit_id), None)
                if not target_unit:
                    continue

                # Force all members to be singletons by setting their cluster size to 1.
                # This effectively breaks them out of any heuristic group.
                for member in target_unit.members:
                    member_key = (member.comp_id, member.index)
                    if member_key in layer_cluster_sizes:
                        layer_cluster_sizes[member_key] = 1
                    # Absent centroid = "use own position" downstream, the
                    # same contract singletons get from compute_group_clusters.
                    layer_centroids.pop(member_key, None)

    root_cid = next(
        (getattr(l, "containing_comp_id", None) for l in layers
         if getattr(l, "containing_comp_id", None) is not None), None)
    all_cids = {getattr(l, "containing_comp_id", None) for l in layers}
    has_containing = root_cid is not None

    # Per-unit-kind × layout semantics (U2 Phase 6):
    #   tags  — legacy verbatim: nothing preserves, nothing seals, no
    #           camera depth forcing (auto-detect / user override
    #           stands). Byte-identity with pre-layout-mode output.
    #   scene — legacy GLOBAL semantics verbatim (OQ-4): every comp
    #           preserves, every nested comp seals, camera depth forces
    #           K→S globally.
    #   auto  — per-comp resolution (THE U2 behavior change):
    #           * the root comp preserves iff it IS a 3D camera scene;
    #           * every nested precomp seals REGARDLESS of cameras (a
    #             precomp is a sealed unit whose wrapper carries the
    #             transform — even in camera-less projects, where
    #             pre-U2 auto behaved like tags);
    #           * camera depth forces S per comp: iff the camera's own
    #             comp is a scene AND that comp preserves (root-preserve
    #             or sealed — under auto every scene comp is one of the
    #             two, so the force set is exactly `scenes`).
    if layout == "scene":
        preserving_scene_cids = set(all_cids)
        sealed_precomp_cids = (
            (all_cids - {None, root_cid}) if has_containing else set())
        camera_depth_force_s_cids = set(all_cids)
        any_preserving = True
    elif layout == "auto":
        root_is_scene = root_cid in scenes
        # None-cid layers belong to the root comp (legacy/mixed
        # manifests) — they preserve with it.
        preserving_scene_cids = {root_cid, None} if root_is_scene else set()
        sealed_precomp_cids = (
            (all_cids - {None, root_cid}) if has_containing else set())
        camera_depth_force_s_cids = set(scenes)
        # Same truth table as the legacy global flag: any camera-scene
        # comp anywhere preserves (as root or as a sealed unit).
        any_preserving = bool(scenes)
    else:
        preserving_scene_cids = set()
        sealed_precomp_cids = set()
        camera_depth_force_s_cids = set()
        any_preserving = False

    return PlacementResolution(
        root_cid=root_cid,
        sealed_precomp_cids=sealed_precomp_cids,
        preserving_scene_cids=preserving_scene_cids,
        layer_centroids=layer_centroids,
        layer_cluster_sizes=layer_cluster_sizes,
        group_centroids=group_centroids,
        camera_depth_force_s_cids=camera_depth_force_s_cids,
        any_preserving_scene_unit=any_preserving,
        layout=layout,
        scenes=set(scenes),
        wrapper_keys=precomp_wrapper_keys(layers),
    )


def stable_unit_id(unit: 'PlacementUnit') -> str:
    """
    Generate a stable unit ID from sorted member identities, not centroid.
    
    This ID remains constant across rescrapes, retags, and position nudges —
    it only changes if the set of members changes (e.g., artist deletes a layer).
    
    Format: group:{comp_id}:{tag}:{member_0_comp:idx}_{member_1_comp:idx}_...
    Example: group:0:HERO:0:5_0:10
    
    Args:
        unit: PlacementUnit instance with members list
        
    Returns:
        str: Stable unit ID, deterministic and position-independent
    """
    if not unit.members:
        log.warning(f"Unit has no members: {unit}")
        return f"group:{unit.comp_id}:{unit.dominant_tag}:empty"
    
    # Sort members by (comp_id, index) for determinism
    sorted_members = sorted(
        [(m.comp_id, m.index) for m in unit.members]
    )
    
    # Build member key: "0:5_0:10"
    member_key = "_".join(
        f"{cid}:{idx}" for cid, idx in sorted_members
    )
    
    # Full ID: "group:0:HERO:0:5_0:10"
    return f"group:{unit.comp_id}:{unit.dominant_tag}:{member_key}"





class UnitMember(BaseModel):
    comp_id: Optional[int] = None
    index: int = 0
    name: str = ""
    tag: Optional[str] = None
    three_d: bool = False


class PlacementUnit(BaseModel):
    unit_id: str
    kind: str  # root_frame | camera_scene | precomp | group | singleton
    label: str
    comp_id: Optional[int] = None       # the comp whose space this unit lives in
    members: List[UnitMember] = Field(default_factory=list)
    child_unit_ids: List[str] = Field(default_factory=list)
    dominant_tag: Optional[str] = None
    anchor_auto: str = ""               # diagnostic: what --layout auto would do
    layer_count: int = 0
    # U2 Phase 7 — what the conform ACTUALLY did with this unit, stamped
    # from the SAME PlacementResolution the math read (diagnostic and
    # behavior can never silently diverge). Additive + defaulted:
    # backwards-compatible, no schema_version bump.
    anchor_resolved: str = ""


class PlacementUnitsReport(BaseModel):
    units: List[PlacementUnit] = Field(default_factory=list)
    root_unit_id: str = "root"
    summary: str = ""
    error: Optional[str] = None         # loud-failures: builder degradation


def _dominant_tag(members: List[UnitMember]) -> Optional[str]:
    counts: Dict[str, int] = defaultdict(int)
    for m in members:
        t = canon_tag(m.tag)
        if t:
            counts[t] += 1
    return max(counts, key=counts.get) if counts else None


def _member(layer: Any) -> UnitMember:
    return UnitMember(
        comp_id=getattr(layer, "containing_comp_id", None),
        index=int(getattr(layer, "index", 0) or 0),
        name=getattr(layer, "name", "") or "",
        tag=getattr(layer, "content_tag", None),
        three_d=bool(getattr(layer, "threeD", False)),
    )


def build_placement_units(
    manifest: Any,
    resolution: Optional[PlacementResolution] = None,
    overrides: Optional[dict] = None,
) -> PlacementUnitsReport:
    """Build the diagnostic unit tree from a (post-survey) manifest.

    U2 — the report DERIVES its group units from the same
    PlacementResolution the conform math reads (OQ-1: diagnostic must
    match behavior). Callers without an engine (legacy tests, ad-hoc
    diagnostics) omit `resolution`; one is computed with the default
    profile-less resolver.

    Never raises — a failure returns a degenerate report with `error`
    set so the caller can surface it (loud-failures policy). The report
    can degrade; conform decisions cannot (the engine holds its own
    resolution + fallback)."""
    try:
        if resolution is None:
            resolution = compute_placement_resolution(
                manifest, layout="auto")
        return _build(manifest, resolution, overrides)
    except Exception as e:  # noqa: BLE001 — diagnostic must not block conform
        log.warning("Placement-unit builder failed",
                    extra={"error": str(e)})
        return PlacementUnitsReport(
            units=[], summary="placement-unit analysis unavailable",
            error=str(e))


def _build(manifest: Any,
           resolution: Optional[PlacementResolution] = None,
           overrides: Optional[dict] = None) -> PlacementUnitsReport:
    layers = list(getattr(manifest, "layers", []) or [])
    if not layers:
        return PlacementUnitsReport(summary="no layers")

    # U4 Phase 1 — Apply unit overrides to the resolution object.
    # The resolution is mutated in-place; since ScaleEngine reads this
    # exact object, the post-override tree becomes physics for the conform.
    if overrides and resolution:
        for unit_id, override in overrides.items():
            action = override.get("action")
            members_removed = override.get("members_removed") or []
            
            if unit_id.startswith("group:"):
                # Dissolve breaks the group apart (all members become singletons).
                # Remove member drops specific layers from the cluster.
                if action == "dissolve":
                    for m in members_removed:
                        k = (m.get("comp_id"), m.get("index"))
                        if k in resolution.layer_cluster_sizes:
                            resolution.layer_cluster_sizes[k] = 1
                        if k in resolution.layer_centroids:
                            del resolution.layer_centroids[k]
                # "remove" action dropped in v1 (had centroid-recompute bug; use dissolve instead)
            
            elif unit_id.startswith("precomp:"):
                if action == "dissolve":
                    parts = unit_id.split(":")
                    if len(parts) == 2:
                        try:
                            cid = None if parts[1] == "None" else int(parts[1])
                            if cid in resolution.sealed_precomp_cids:
                                resolution.sealed_precomp_cids.remove(cid)
                        except ValueError:
                            pass

    root_cid = next(
        (getattr(l, "containing_comp_id", None) for l in layers
         if getattr(l, "containing_comp_id", None) is not None), None)

    by_comp: Dict[Optional[int], list] = defaultdict(list)
    for l in layers:
        by_comp[getattr(l, "containing_comp_id", None)].append(l)
    # Legacy flat manifests: every layer has cid None → treat as root.
    root_layers = by_comp.get(root_cid, []) + (
        by_comp.get(None, []) if root_cid is not None else [])
    if root_cid is None:
        root_layers = by_comp.get(None, [])

    scenes = detect_3d_camera_scenes_by_comp(layers)

    # Precomp parent edges from wrapper layers (the nesting-doll chain).
    # wrapper's containing comp → the comp its source_item points at.
    # U3 Phase A: child_of derives from the shared precomp_wrapper_keys()
    # helper (code motion, byte-identical) — SOE reuses the same helper.
    _wrapper_keys = precomp_wrapper_keys(layers)
    child_of: Dict[int, Optional[int]] = {      # child cid → parent cid
        ccid: key[0] for ccid, key in _wrapper_keys.items()}
    wrapper_name: Dict[int, str] = {}            # child cid → wrapper layer name
    for l in layers:
        si = getattr(l, "source_item", None)
        if si is not None and getattr(si, "kind", "") == "comp":
            ccid = getattr(si, "nested_comp_id", None) or getattr(si, "id", None)
            if ccid is not None:
                wrapper_name[ccid] = getattr(l, "name", "") or ""

    units: List[PlacementUnit] = []
    root_unit = PlacementUnit(
        unit_id="root", kind="root_frame", comp_id=root_cid,
        label="Root frame", anchor_auto="conform target")
    units.append(root_unit)

    # ── Root comp partition ─────────────────────────────────────────
    used: set = set()

    def _key(l: Any):
        return (getattr(l, "containing_comp_id", None),
                int(getattr(l, "index", 0) or 0))

    # 1. Camera scene (if the root comp is one): camera + 3D layers.
    if root_cid in scenes or (root_cid is None and None in scenes):
        members = [l for l in root_layers
                   if getattr(l, "layer_kind", "av") == "camera"
                   or getattr(l, "threeD", False)]
        if members:
            mm = [_member(l) for l in members]
            u = PlacementUnit(
                unit_id=f"scene:{root_cid}", kind="camera_scene",
                comp_id=root_cid,
                label=f"Camera scene — {len(mm)} layers move as one",
                members=mm, dominant_tag=_dominant_tag(mm),
                anchor_auto="preserve (uniform scale + center, camera scales with scene)",
                layer_count=len(mm))
            units.append(u)
            root_unit.child_unit_ids.append(u.unit_id)
            used.update(_key(l) for l in members)

    # 2. Protected structural overlays.
    for l in root_layers:
        if _key(l) in used:
            continue
        if canon_tag(getattr(l, "content_tag", None)) in _PROTECTED_TAGS:
            m = _member(l)
            u = PlacementUnit(
                unit_id=f"layer:{m.comp_id}:{m.index}", kind="singleton",
                comp_id=root_cid, label=f"Protected — {m.name}",
                members=[m], dominant_tag=canon_tag(m.tag),
                anchor_auto="protect (never moved)", layer_count=1)
            units.append(u)
            root_unit.child_unit_ids.append(u.unit_id)
            used.add(_key(l))

    # 3. Remaining 2D root layers: cluster-derived groups, then singletons.
    #    (Wrapper layers of nested comps get their own precomp units below —
    #    but a 2D wrapper still needs a placement decision in the root, so
    #    it stays in this partition too; the precomp unit describes its
    #    INSIDES, this unit its PLACEMENT.)
    #    U2 (OQ-1 decision): groups derive from the SAME single-linkage
    #    clusters the gravity pre-pass uses (resolution.layer_centroids),
    #    not the old spatial_group_key y-bins — the diagnostic card must
    #    match what the conform math actually groups. Layers outside any
    #    cluster (untagged / no gravity rule / child layers) are
    #    singletons. Cluster identity = the shared (comp_id, tag,
    #    cluster-centroid) triple: members of one cluster carry the exact
    #    same centroid tuple by construction.
    _res_centroids = resolution.layer_centroids if resolution else {}
    _res_sizes = resolution.layer_cluster_sizes if resolution else {}
    buckets: Dict[Any, list] = defaultdict(list)
    for l in root_layers:
        if _key(l) in used:
            continue
        if getattr(l, "layer_kind", "av") in ("camera", "light"):
            continue
        centroid = _res_centroids.get(_key(l))
        if centroid is not None and _res_sizes.get(_key(l), 1) > 1:
            tag = canon_tag(getattr(l, "content_tag", None)) or "NONE"
            bkey = (getattr(l, "containing_comp_id", None), tag, centroid)
        else:
            bkey = ("solo",) + _key(l)
        buckets[bkey].append(l)

    for bkey, blayers in buckets.items():
        mm = [_member(l) for l in blayers]
        dom = _dominant_tag(mm)
        anchor = f"zone: {dom}" if dom else "center-remap (untagged)"
        if len(blayers) > 1:
            _cx, _cy = bkey[2]
            u = PlacementUnit(
                unit_id=f"group:{bkey[0]}:{bkey[1]}:"
                        f"{round(_cx)}x{round(_cy)}",
                kind="group",
                comp_id=root_cid,
                label=f"Group — {len(mm)} {dom or 'untagged'} layers "
                      f"(spacing preserved)",
                members=mm, dominant_tag=dom, anchor_auto=anchor,
                layer_count=len(mm))
        else:
            m = mm[0]
            u = PlacementUnit(
                unit_id=f"layer:{m.comp_id}:{m.index}", kind="singleton",
                comp_id=root_cid, label=m.name or "(unnamed)",
                members=mm, dominant_tag=dom, anchor_auto=anchor,
                layer_count=1)
        units.append(u)
        root_unit.child_unit_ids.append(u.unit_id)

    # ── Nested precomps: sealed units, parented per the doll chain ──
    precomp_units: Dict[int, PlacementUnit] = {}
    for cid, clayers in by_comp.items():
        if cid is None or cid == root_cid:
            continue
        # Only treat as a sealed precomp if we found a wrapper layer pointing to
        # this comp. Comps without a wrapper edge are sibling root comps (e.g.
        # multi-comp scrape fixtures) — they should not be sealed.
        is_wrapped = cid in child_of
        mm = [_member(l) for l in clayers]
        if is_wrapped:
            u = PlacementUnit(
                unit_id=f"precomp:{cid}", kind="precomp", comp_id=cid,
                label=f"Precomp '{wrapper_name.get(cid, str(cid))}' — sealed "
                      f"({len(mm)} layers inside)",
                members=mm, dominant_tag=_dominant_tag(mm),
                anchor_auto="preserve (sealed — internals untouched, wrapper "
                            "layer carries the transform)",
                layer_count=len(mm))
        else:
            # Unwrapped sibling comp — treat as a root-frame equivalent.
            u = PlacementUnit(
                unit_id=f"comp:{cid}", kind="root_frame", comp_id=cid,
                label=f"Root frame (comp {cid})",
                members=mm, dominant_tag=_dominant_tag(mm),
                anchor_auto="conform target",
                layer_count=len(mm))
        units.append(u)
        precomp_units[cid] = u

    for cid, u in precomp_units.items():
        parent_cid = child_of.get(cid)
        parent = precomp_units.get(parent_cid)
        if parent is not None:
            parent.child_unit_ids.append(u.unit_id)
        else:
            root_unit.child_unit_ids.append(u.unit_id)

    # ── Summary ─────────────────────────────────────────────────────
    kind_counts: Dict[str, int] = defaultdict(int)
    for u in units:
        if u.kind != "root_frame":
            kind_counts[u.kind] += 1
    parts = []
    if kind_counts.get("camera_scene"):
        n = sum(u.layer_count for u in units if u.kind == "camera_scene")
        parts.append(f"{kind_counts['camera_scene']} camera scene ({n} layers)")
    if kind_counts.get("precomp"):
        n = sum(u.layer_count for u in units if u.kind == "precomp")
        parts.append(f"{kind_counts['precomp']} sealed precomps ({n} layers)")
    if kind_counts.get("group"):
        parts.append(f"{kind_counts['group']} groups")
    if kind_counts.get("singleton"):
        parts.append(f"{kind_counts['singleton']} singletons")
    total_units = sum(kind_counts.values())
    summary = f"{total_units} units: " + ", ".join(parts) if parts else "no units"

    report = PlacementUnitsReport(units=units, summary=summary)
    log.info("Placement units", extra={"summary": summary})
    return report


def stamp_anchor_resolved(
    report: PlacementUnitsReport,
    resolution: Optional[PlacementResolution],
) -> None:
    """U2 Phase 7 — stamp `anchor_resolved` on every unit: what the
    conform ACTUALLY did, derived from the SAME PlacementResolution the
    math read. Called by ScaleEngine.conform() after the rule set runs
    so the report card can render "auto read → resolved" and diagnostic
    vs behavior can never silently diverge.

    Mutates the report in place. Never raises to the caller's benefit —
    a degraded resolution stamps an explicit degradation note."""
    if report is None:
        return
    for u in report.units:
        if resolution is None:
            u.anchor_resolved = ("legacy fallback — placement resolution "
                                 "degraded (see run warnings)")
            continue
        if u.kind == "root_frame":
            u.anchor_resolved = f"conform target (layout={resolution.layout})"
        elif u.kind == "camera_scene":
            if u.comp_id in resolution.preserving_scene_cids:
                u.anchor_resolved = ("preserved — uniform scale + center, "
                                     "camera depth forced S")
            else:
                u.anchor_resolved = ("tag gravity — scene preserve off "
                                     f"(layout={resolution.layout})")
        elif u.kind == "precomp":
            if u.comp_id in resolution.sealed_precomp_cids:
                u.anchor_resolved = ("sealed — internals untouched, wrapper "
                                     "layer carries the transform")
            else:
                u.anchor_resolved = "recursive resize (legacy tags behavior)"
        else:  # group | singleton — root-partition units
            if u.dominant_tag in ("GUIDE", "PROTECT"):
                u.anchor_resolved = "protected — never moved"
            elif u.comp_id in resolution.preserving_scene_cids:
                u.anchor_resolved = ("preserved (scene) — uniform "
                                     "center-remap; FILL still covers")
            elif u.dominant_tag:
                u.anchor_resolved = f"tag gravity — {u.dominant_tag} zone"
            else:
                u.anchor_resolved = "center-remap (untagged)"
