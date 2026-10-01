# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/occlusion/repulsion_solver.py
Inter-layer spring-damper relaxation and vertical stack collision clearance solver.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Tuple
import numpy as np

from core.layer_utils import canon_tag as _canon
from core.occlusion.constants import (
    REPULSION_EPSILON_PX,
    REPULSION_MARGIN_FLOOR_PX,
    REPULSION_MARGIN_PX,
    REPULSION_MAX_SWEEPS,
    REPULSION_TAG_WEIGHTS,
    SOECorrection,
    SOE_OVERFLOW_WARNING,
    TRANSLATABLE_TAGS,
)
from core.occlusion.mask_solver import OcclusionMask


def vertical_go_band(mask: OcclusionMask, l: float, r: float) -> Optional[Tuple[float, float]]:
    """(ceiling_y, floor_y) of the GO band across the x-span [l, r)."""
    go = mask.zones.go
    h, w = go.shape
    li = max(0, int(math.floor(l)))
    ri = min(w, int(math.ceil(r)))
    if ri <= li:
        return None
    rows = np.flatnonzero(go[:, li:ri].any(axis=1))
    if rows.size == 0:
        return None
    return float(rows[0]), float(rows[-1] + 1)


def horizontally_overlaps(a: dict, b: dict) -> bool:
    """Two layers only collide if their x-spans meet."""
    return a["l"] < b["r"] and b["l"] < a["r"]


def solve_stack(items: List[dict], ceiling: float, floor: float) -> bool:
    """Relax one vertical stack in place. Returns True on overflow."""
    items.sort(key=lambda it: it["y"])
    n = len(items)
    total_h = sum(it["h"] for it in items)
    available = floor - ceiling

    margin = REPULSION_MARGIN_PX
    overflow = False
    if n > 1:
        needed = total_h + (n - 1) * REPULSION_MARGIN_PX
        if needed > available:
            margin = max(REPULSION_MARGIN_FLOOR_PX,
                         (available - total_h) / (n - 1))
            if total_h + (n - 1) * REPULSION_MARGIN_FLOOR_PX > available:
                margin = REPULSION_MARGIN_FLOOR_PX
                overflow = True

    for _ in range(REPULSION_MAX_SWEEPS):
        moved = False
        items.sort(key=lambda it: it["y"])
        for i in range(n):
            for j in range(i + 1, n):
                a, b = items[i], items[j]
                if not horizontally_overlaps(a, b):
                    continue
                d_min = (a["h"] + b["h"]) / 2.0 + margin
                delta = d_min - (b["y"] - a["y"])
                if delta <= REPULSION_EPSILON_PX:
                    continue
                if a["movable"] and b["movable"]:
                    # A heavier layer yields less.  The displacement is
                    # inversely proportional to its own importance, so the
                    # pair still separates by exactly ``delta``.  Equal
                    # weights reduce to the legacy 50/50 split.
                    total_weight = a["weight"] + b["weight"]
                    a["y"] -= delta * b["weight"] / total_weight
                    b["y"] += delta * a["weight"] / total_weight
                elif b["movable"]:
                    b["y"] += delta
                elif a["movable"]:
                    a["y"] -= delta
                else:
                    continue
                moved = True

        for it in items:
            if not it["movable"]:
                continue
            half = it["h"] / 2.0
            lo, hi = ceiling + half, floor - half
            if lo > hi:
                it["y"] = (ceiling + floor) / 2.0
                overflow = True
            else:
                it["y"] = min(max(it["y"], lo), hi)
        if not moved:
            break

    if not overflow:
        items.sort(key=lambda it: it["y"])
        for i in range(n):
            for j in range(i + 1, n):
                a, b = items[i], items[j]
                if not horizontally_overlaps(a, b):
                    continue
                d_min = (a["h"] + b["h"]) / 2.0 + margin
                if (b["y"] - a["y"]) < d_min - REPULSION_EPSILON_PX:
                    overflow = True
                    break
            if overflow:
                break
    return overflow


def resolve_inter_layer_repulsion(
    mask: OcclusionMask,
    layers: List[dict],
    corrections: List[SOECorrection],
    handled_keys: set,
    get_position_fn: Callable[[dict], List[float]],
    set_position_fn: Callable[[dict, List[float]], None],
    conformed_bounds_fn: Callable[[dict, List[float]], Optional[Dict[str, float]]],
    layer_key_fn: Callable[[dict], Tuple[Optional[int], int]],
    record_fn: Callable[..., SOECorrection],
    zone_label_fn: Callable[[dict], str],
) -> List[str]:
    """Separate stacked translatable layers. Returns overflow warning notes."""
    warnings: List[str] = []
    
    # Identify candidates
    candidates: List[dict] = []
    for layer in layers:
        if _canon(layer.get("content_tag")) not in TRANSLATABLE_TAGS:
            continue
        kind = (layer.get("layer_kind") or "av").lower()
        if kind in ("camera", "light"):
            continue
        pos = get_position_fn(layer)
        bounds = conformed_bounds_fn(layer, pos)
        if not bounds:
            continue
        height = float(bounds["b"]) - float(bounds["t"])
        if height <= 0:
            continue

        keyed = layer.get("is_keyed") or {}
        is_keyed = bool(keyed.get("position") or keyed.get("position_x")
                        or keyed.get("position_y"))
        movable = not is_keyed and layer_key_fn(layer) not in handled_keys

        candidates.append({
            "layer": layer,
            "y": (float(bounds["t"]) + float(bounds["b"])) / 2.0,
            "start_y": (float(bounds["t"]) + float(bounds["b"])) / 2.0,
            "h": height,
            "l": float(bounds["l"]),
            "r": float(bounds["r"]),
            "movable": movable,
            "weight": REPULSION_TAG_WEIGHTS.get(
                _canon(layer.get("content_tag")), 1.0
            ),
            "pos": pos,
        })

    if len(candidates) < 2:
        return warnings

    groups: Dict[Optional[int], List[dict]] = {}
    for item in candidates:
        cid = item["layer"].get("containing_comp_id")
        groups.setdefault(cid, []).append(item)

    by_key = {(c.layer_uid, c.layer_index): c for c in corrections}

    for items in groups.values():
        if len(items) < 2 or not any(it["movable"] for it in items):
            continue
        span_l = min(it["l"] for it in items)
        span_r = max(it["r"] for it in items)
        band = vertical_go_band(mask, span_l, span_r)
        if band is None:
            continue
        ceiling, floor = band
        if floor - ceiling <= 0:
            continue

        overflow = solve_stack(items, ceiling, floor)

        for it in items:
            dy = it["y"] - it["start_y"]
            if not it["movable"] or abs(dy) <= REPULSION_EPSILON_PX:
                continue
            layer = it["layer"]
            new_pos = list(it["pos"])
            new_pos[1] = float(new_pos[1]) + dy
            set_position_fn(layer, new_pos)

            strategy = "REPULSION_OVERFLOW" if overflow else "REPULSION"
            note = (
                "stack could not be separated at the "
                f"{REPULSION_MARGIN_FLOOR_PX:g}px margin floor — "
                "position is best-effort, needs human review"
                if overflow else
                f"separated from neighbour by {abs(dy):.1f}px"
            )
            existing = by_key.get((layer.get("uid"),
                                   int(layer.get("index", 0) or 0)))
            if existing is not None:
                existing.corrected_position = list(new_pos)
                existing.move_distance_px = float(
                    math.hypot(new_pos[0] - existing.original_position[0],
                               new_pos[1] - existing.original_position[1])
                )
                existing.notes = (existing.notes + " | " if existing.notes
                                  else "") + note
                if overflow:
                    existing.strategy = strategy
            else:
                corrections.append(record_fn(
                    layer, it["pos"], new_pos,
                    zone=zone_label_fn(mask.classify_aabb(
                        it["l"], it["y"] - it["h"] / 2.0,
                        it["r"], it["y"] + it["h"] / 2.0)),
                    strategy=strategy, move_distance_px=abs(dy),
                    original_zone=None, notes=note,
                ))

        if overflow:
            names = ", ".join(
                str(it["layer"].get("name") or "?") for it in items
            )
            warnings.append(
                f"{SOE_OVERFLOW_WARNING}: {len(items)} stacked layers "
                f"({names}) exceed the available vertical GO zone "
                f"({floor - ceiling:.0f}px) even at the "
                f"{REPULSION_MARGIN_FLOOR_PX:g}px margin floor"
            )

    return warnings
