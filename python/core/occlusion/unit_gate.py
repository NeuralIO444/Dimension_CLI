# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/occlusion/unit_gate.py
U3 Phase A unit-aware placement gates: sealed precomps, preserving camera scenes,
and rigid translatable clusters.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Set, Tuple
import numpy as np

from core.layer_utils import canon_tag as _canon
from core.occlusion.constants import SOECorrection, STRUCTURAL_TAGS, TRANSLATABLE_TAGS
from core.occlusion.mask_solver import OcclusionMask


def unit_gate(resolution) -> bool:
    """U3 Scope Decision: unit-aware behavior activates only when
    the resolution carries a preserving scene or a sealed precomp."""
    if resolution is None:
        return False
    return bool(getattr(resolution, "any_preserving_scene_unit", False)
                or getattr(resolution, "sealed_precomp_cids", None))


def is_position_keyed(layer: dict) -> bool:
    is_keyed = layer.get("is_keyed") or {}
    return bool(is_keyed.get("position") or is_keyed.get("x_position")
                or is_keyed.get("y_position"))


def process_sealed_precomps(
    mask: OcclusionMask,
    layers: List[dict],
    resolution,
    scene_warnings: List[str],
    handled_keys: Set[Tuple[Optional[int], int]],
    layer_key_fn: Callable[[dict], Tuple[Optional[int], int]],
    get_position_fn: Callable[[dict], List[float]],
    conformed_bounds_fn: Callable[[dict, List[float]], Optional[Dict[str, float]]],
    zone_label_fn: Callable[[dict], str],
) -> None:
    sealed_cids = getattr(resolution, "sealed_precomp_cids", None) or set()
    if not sealed_cids:
        return
    by_key = {layer_key_fn(l): l for l in layers}
    wrapper_keys = getattr(resolution, "wrapper_keys", None) or {}
    for cid in sealed_cids:
        for layer in layers:
            if layer.get("containing_comp_id") == cid:
                handled_keys.add(layer_key_fn(layer))
        wkey = wrapper_keys.get(cid)
        if wkey is None:
            continue
        wrapper = by_key.get(wkey)
        if wrapper is None:
            continue
        handled_keys.add(layer_key_fn(wrapper))
        if wkey[0] in sealed_cids:
            continue
        original_pos = get_position_fn(wrapper)
        bounds = conformed_bounds_fn(wrapper, original_pos)
        if bounds is None:
            continue
        metrics = mask.classify_aabb(
            bounds["l"], bounds["t"], bounds["r"], bounds["b"]
        )
        if metrics["centroid_zone"] == "GO" and metrics["overlap_cutoff_px"] == 0:
            continue
        name = str(wrapper.get("name") or f"precomp {cid}")
        scene_warnings.append(
            f"{name} (sealed precomp) overlaps "
            f"{zone_label_fn(metrics)} — not moved (scene-preserve)"
        )


def process_preserving_root(
    mask: OcclusionMask,
    layers: List[dict],
    resolution,
    scene_warnings: List[str],
    handled_keys: Set[Tuple[Optional[int], int]],
    layer_key_fn: Callable[[dict], Tuple[Optional[int], int]],
    get_position_fn: Callable[[dict], List[float]],
    conformed_bounds_fn: Callable[[dict, List[float]], Optional[Dict[str, float]]],
    zone_label_fn: Callable[[dict], str],
) -> None:
    preserving_cids = getattr(resolution, "preserving_scene_cids", None) or set()
    if not preserving_cids:
        return
    root_members = []
    for layer in layers:
        if layer.get("containing_comp_id") not in preserving_cids:
            continue
        kind = (layer.get("layer_kind") or "av").lower()
        if kind in ("camera", "light"):
            continue
        if _canon(layer.get("content_tag")) in STRUCTURAL_TAGS:
            continue
        root_members.append(layer)
        handled_keys.add(layer_key_fn(layer))
    if not root_members:
        return

    l = t = float("inf")
    r = b = float("-inf")
    any_bounds = False
    for layer in root_members:
        bounds = conformed_bounds_fn(layer, get_position_fn(layer))
        if bounds is None:
            continue
        any_bounds = True
        l = min(l, bounds["l"])
        t = min(t, bounds["t"])
        r = max(r, bounds["r"])
        b = max(b, bounds["b"])
    if not any_bounds:
        return
    metrics = mask.classify_aabb(l, t, r, b)
    if metrics["centroid_zone"] == "GO" and metrics["overlap_cutoff_px"] == 0:
        return
    root_cid = getattr(resolution, "root_cid", None)
    label = f"Root comp {root_cid}" if root_cid is not None else "Root comp"
    scene_warnings.append(
        f"{label} (camera scene) overlaps "
        f"{zone_label_fn(metrics)} — not moved (scene-preserve)"
    )


def process_translatable_units(
    mask: OcclusionMask,
    layers: List[dict],
    resolution,
    corrections: List[SOECorrection],
    handled_keys: Set[Tuple[Optional[int], int]],
    layer_key_fn: Callable[[dict], Tuple[Optional[int], int]],
    get_position_fn: Callable[[dict], List[float]],
    set_position_fn: Callable[[dict, List[float]], None],
    conformed_bounds_fn: Callable[[dict, List[float]], Optional[Dict[str, float]]],
    record_fn: Callable[..., SOECorrection],
) -> None:
    centroids = getattr(resolution, "layer_centroids", None) or {}
    sizes = getattr(resolution, "layer_cluster_sizes", None) or {}
    buckets: Dict[tuple, list] = {}
    for layer in layers:
        key = layer_key_fn(layer)
        if key in handled_keys:
            continue
        tag_canon = _canon(layer.get("content_tag"))
        if tag_canon not in TRANSLATABLE_TAGS:
            continue
        if sizes.get(key, 1) <= 1:
            continue
        centroid = centroids.get(key)
        if centroid is None:
            continue
        bkey = (layer.get("containing_comp_id"), tag_canon, centroid)
        buckets.setdefault(bkey, []).append(layer)

    for members in buckets.values():
        if len(members) <= 1:
            continue
        for m in members:
            handled_keys.add(layer_key_fn(m))
        corrections.extend(process_unit(
            mask, members, get_position_fn, set_position_fn,
            conformed_bounds_fn, record_fn
        ))


def process_unit(
    mask: OcclusionMask,
    members: List[dict],
    get_position_fn: Callable[[dict], List[float]],
    set_position_fn: Callable[[dict, List[float]], None],
    conformed_bounds_fn: Callable[[dict, List[float]], Optional[Dict[str, float]]],
    record_fn: Callable[..., SOECorrection],
) -> List[SOECorrection]:
    if any(is_position_keyed(m) for m in members):
        out_corrections: List[SOECorrection] = []
        for m in members:
            pos = get_position_fn(m)
            if is_position_keyed(m):
                out_corrections.append(record_fn(
                    m, pos, pos, "UNKNOWN", "SKIPPED_KEYED", 0.0,
                    original_zone=None,
                    notes="position is keyframed — manual review required",
                ))
            else:
                out_corrections.append(record_fn(
                    m, pos, pos, "UNKNOWN", "SKIPPED_UNIT_KEYED", 0.0,
                    original_zone=None,
                    notes=("sibling in a group with a keyed member — "
                           "the group moves rigidly or not at all; "
                           "manual review required"),
                ))
        return out_corrections

    original_positions = {id(m): get_position_fn(m) for m in members}
    l = t = float("inf")
    r = b = float("-inf")
    any_bounds = False
    for m in members:
        bounds = conformed_bounds_fn(m, original_positions[id(m)])
        if bounds is None:
            continue
        any_bounds = True
        l = min(l, bounds["l"])
        t = min(t, bounds["t"])
        r = max(r, bounds["r"])
        b = max(b, bounds["b"])
    if not any_bounds:
        return []
    metrics = mask.classify_aabb(l, t, r, b)
    if metrics["centroid_zone"] == "GO" and metrics["overlap_cutoff_px"] == 0:
        return []

    tv = metrics.get("translation_vector")
    if tv and not (tv[0] == 0 and tv[1] == 0):
        dy, dx = tv
        m_after = mask.classify_aabb(l + dx, t + dy, r + dx, b + dy)
        if m_after["centroid_zone"] == "GO" and m_after["overlap_cutoff_px"] == 0:
            move_dist = float(np.hypot(dx, dy))
            out_corrections = []
            for m in members:
                original_pos = original_positions[id(m)]
                candidate_pos = list(original_pos)
                candidate_pos[0] = float(original_pos[0] + dx)
                candidate_pos[1] = float(original_pos[1] + dy)
                set_position_fn(m, candidate_pos)
                out_corrections.append(record_fn(
                    m, original_pos, candidate_pos,
                    m_after["centroid_zone"], "TRANSLATE", move_dist,
                    original_zone=metrics["centroid_zone"],
                    notes=(f"unit translated by ({dx},{dy})px "
                           f"({len(members)}-member group, shared offset)"),
                ))
            return out_corrections

    out_corrections = []
    for m in members:
        pos = original_positions[id(m)]
        out_corrections.append(record_fn(
            m, pos, pos, metrics["centroid_zone"], "SOE_FAILED", 0.0,
            original_zone=metrics["centroid_zone"],
            notes="no GO placement reachable for group — flag for human review",
        ))
    return out_corrections
