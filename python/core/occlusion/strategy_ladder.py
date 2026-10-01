# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/occlusion/strategy_ladder.py
Per-layer heuristic resolution ladder: TRANSLATE, TIGHTEN_MARGIN, and ANCHOR_SHIFT.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional
import numpy as np

from core.occlusion.constants import SOECorrection
from core.occlusion.mask_solver import OcclusionMask


def try_translate(
    mask: OcclusionMask,
    layer: dict,
    original_pos: List[float],
    bounds: dict,
    metrics: dict,
    conformed_bounds_fn: Callable[[dict, List[float]], Optional[Dict[str, float]]],
    set_position_fn: Callable[[dict, List[float]], None],
    record_fn: Callable[..., SOECorrection],
) -> Optional[SOECorrection]:
    tv = metrics.get("translation_vector")
    if not tv:
        return None
    dy, dx = tv
    if dy == 0 and dx == 0:
        return None
    candidate_pos = list(original_pos)
    candidate_pos[0] = float(original_pos[0] + dx)
    candidate_pos[1] = float(original_pos[1] + dy)
    bounds_after = conformed_bounds_fn(layer, candidate_pos)
    if bounds_after is None:
        return None
    m_after = mask.classify_aabb(
        bounds_after["l"], bounds_after["t"],
        bounds_after["r"], bounds_after["b"],
    )
    if (m_after["centroid_zone"] == "GO"
            and m_after["overlap_cutoff_px"] == 0):
        move_dist = float(np.hypot(dx, dy))
        set_position_fn(layer, candidate_pos)
        return record_fn(
            layer, original_pos, candidate_pos,
            m_after["centroid_zone"], "TRANSLATE", move_dist,
            original_zone=metrics["centroid_zone"],
            notes=f"translated by ({dx},{dy})px",
        )
    return None


def try_tighten_margin(
    mask: OcclusionMask,
    layer: dict,
    original_pos: List[float],
    conformed_bounds_fn: Callable[[dict, List[float]], Optional[Dict[str, float]]],
    set_position_fn: Callable[[dict, List[float]], None],
    record_fn: Callable[..., SOECorrection],
) -> Optional[SOECorrection]:
    for margin in (0.17, 0.14, 0.11):
        candidate = [
            mask.comp_w / 2.0,
            mask.comp_h * (1.0 - margin),
        ]
        if len(original_pos) > 2:
            candidate.append(original_pos[2])
        bounds_after = conformed_bounds_fn(layer, candidate)
        if bounds_after is None:
            continue
        m_after = mask.classify_aabb(
            bounds_after["l"], bounds_after["t"],
            bounds_after["r"], bounds_after["b"],
        )
        if (m_after["centroid_zone"] == "GO"
                and m_after["overlap_cutoff_px"] == 0):
            move_dist = float(np.hypot(
                candidate[0] - original_pos[0],
                candidate[1] - original_pos[1],
            ))
            set_position_fn(layer, candidate)
            return record_fn(
                layer, original_pos, candidate,
                m_after["centroid_zone"], "TIGHTEN_MARGIN", move_dist,
                original_zone="CUTOFF",
                notes=f"legals margin tightened to {int(margin*100)}%",
            )
    return None


def try_anchor_shift(
    mask: OcclusionMask,
    layer: dict,
    original_pos: List[float],
    bounds: dict,
    conformed_bounds_fn: Callable[..., Optional[Dict[str, float]]],
    set_position_fn: Callable[[dict, List[float]], None],
    record_fn: Callable[..., SOECorrection],
) -> Optional[SOECorrection]:
    cf = layer.get("conformed_transforms") or {}
    anchor = list(cf.get("anchor") or layer.get("anchor") or [0.0, 0.0, 0.0])
    scale  = list(cf.get("scale")  or layer.get("scale")  or [100, 100, 100])
    rect   = layer.get("source_rect") or [0, 0, 0, 0]
    if len(rect) < 4 or len(anchor) < 2:
        return None

    rect_l, rect_t, rect_w, rect_h = rect[:4]
    cx = rect_l + rect_w / 2.0
    cy = rect_t + rect_h / 2.0
    new_anchor = list(anchor)
    new_anchor[0] = float(2 * cx - anchor[0])
    new_anchor[1] = float(2 * cy - anchor[1])

    sx = (scale[0] or 0.0) / 100.0
    sy = (scale[1] or 0.0) / 100.0
    delta_ax = new_anchor[0] - anchor[0]
    delta_ay = new_anchor[1] - anchor[1]
    new_position = list(original_pos)
    new_position[0] = float(original_pos[0] + delta_ax * sx)
    new_position[1] = float(original_pos[1] + delta_ay * sy)

    layer.setdefault("conformed_transforms", {})["anchor"] = new_anchor
    bounds_after = conformed_bounds_fn(layer, new_position,
                                       override_anchor=new_anchor)
    if bounds_after is None:
        layer["conformed_transforms"]["anchor"] = anchor
        return None
    m_after = mask.classify_aabb(
        bounds_after["l"], bounds_after["t"],
        bounds_after["r"], bounds_after["b"],
    )
    if (m_after["centroid_zone"] == "GO"
            and m_after["overlap_cutoff_px"] == 0):
        set_position_fn(layer, new_position)
        move_dist = float(np.hypot(new_position[0] - original_pos[0],
                                   new_position[1] - original_pos[1]))
        return record_fn(
            layer, original_pos, new_position,
            m_after["centroid_zone"], "ANCHOR_SHIFT", move_dist,
            original_zone="CUTOFF",
            notes=("anchor mirrored across AABB centroid; "
                   "position counter-moved to preserve visual"),
        )
    layer["conformed_transforms"]["anchor"] = anchor
    return None
