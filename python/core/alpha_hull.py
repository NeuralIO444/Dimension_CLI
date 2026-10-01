# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/alpha_hull.py
Next-Gen Relayout Architecture — Artwork Boundary & Alpha Hull Engine (PR 4).

Computes exact non-zero visual artwork boundaries (alpha hulls, vector path extrema,
and composite precomp visual contours) to eliminate the "empty canvas" drift problem
where logos, keyed cutouts, or nested elements are placed on oversized transparent artboards.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence, Tuple, Union

from models.scrape_manifest import ArtworkBounds, LayerArchetype, LayerModel


def compute_alpha_bounds(
    alpha_data: Union[Sequence[Sequence[float]], Any],
    threshold: float = 5.0 / 255.0,
    upscale_factor: float = 1.0,
) -> ArtworkBounds:
    """Compute tight non-zero visual boundary and weighted center of mass from alpha channel data.

    Args:
        alpha_data: 2D array-like structure representing alpha values in range [0, 1] or [0, 255].
        threshold: Alpha cutoff threshold (values below are considered transparent).
        upscale_factor: Scaling factor (e.g., 4.0 if sampled at 1/4 resolution probe).

    Returns:
        ArtworkBounds: left, top, width, height, and optical centroid [cx, cy].
    """
    if alpha_data is None:
        return ArtworkBounds()

    # Convert threshold if alpha is in [0, 255]
    th = threshold if threshold > 1.0 else threshold * 255.0

    min_x = float("inf")
    max_x = float("-inf")
    min_y = float("inf")
    max_y = float("-inf")

    total_weight = 0.0
    weighted_sum_x = 0.0
    weighted_sum_y = 0.0

    # Support numpy array, list of lists, or PIL alpha channel
    try:
        # Fast path if numpy is available
        import numpy as np
        if isinstance(alpha_data, np.ndarray):
            if alpha_data.ndim == 3 and alpha_data.shape[2] == 4:
                alpha = alpha_data[:, :, 3]
            else:
                alpha = alpha_data

            if alpha.max() <= 1.0:
                alpha = alpha * 255.0

            non_zero = np.argwhere(alpha > th)
            if non_zero.size == 0:
                return ArtworkBounds()

            y_min, x_min = non_zero.min(axis=0)
            y_max, x_max = non_zero.max(axis=0)

            # Center of mass
            y_indices, x_indices = np.indices(alpha.shape)
            weights = np.where(alpha > th, alpha, 0.0)
            tot_w = weights.sum()
            if tot_w > 0:
                cx = float((x_indices * weights).sum() / tot_w) * upscale_factor
                cy = float((y_indices * weights).sum() / tot_w) * upscale_factor
            else:
                cx = float(x_min + (x_max - x_min) / 2.0) * upscale_factor
                cy = float(y_min + (y_max - y_min) / 2.0) * upscale_factor

            return ArtworkBounds(
                left=float(x_min) * upscale_factor,
                top=float(y_min) * upscale_factor,
                width=float(x_max - x_min + 1) * upscale_factor,
                height=float(y_max - y_min + 1) * upscale_factor,
                alpha_threshold=threshold,
                centroid=[cx, cy],
            )
    except ImportError:
        pass

    # Pure Python fallback
    height = len(alpha_data)
    if height == 0:
        return ArtworkBounds()

    for y, row in enumerate(alpha_data):
        for x, val in enumerate(row):
            fval = float(val)
            if fval <= 1.0 and th > 1.0:
                fval *= 255.0
            if fval > th:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
                total_weight += fval
                weighted_sum_x += x * fval
                weighted_sum_y += y * fval

    if total_weight == 0.0 or min_x == float("inf"):
        return ArtworkBounds()

    cx = (weighted_sum_x / total_weight) * upscale_factor
    cy = (weighted_sum_y / total_weight) * upscale_factor

    return ArtworkBounds(
        left=float(min_x) * upscale_factor,
        top=float(min_y) * upscale_factor,
        width=float(max_x - min_x + 1) * upscale_factor,
        height=float(max_y - min_y + 1) * upscale_factor,
        alpha_threshold=threshold,
        centroid=[cx, cy],
    )


def compute_vector_bounds(
    points: Sequence[Sequence[float]],
) -> ArtworkBounds:
    """Compute Axis-Aligned Bounding Box (AABB) and centroid from vector/mask vertices."""
    if not points:
        return ArtworkBounds()

    min_x = float("inf")
    max_x = float("-inf")
    min_y = float("inf")
    max_y = float("-inf")

    sum_x = 0.0
    sum_y = 0.0
    count = 0

    for pt in points:
        if len(pt) < 2:
            continue
        x, y = float(pt[0]), float(pt[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        if x < min_x:
            min_x = x
        if x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        if y > max_y:
            max_y = y
        sum_x += x
        sum_y += y
        count += 1

    if count == 0 or min_x == float("inf"):
        return ArtworkBounds()

    return ArtworkBounds(
        left=min_x,
        top=min_y,
        width=max(0.0, max_x - min_x),
        height=max(0.0, max_y - min_y),
        centroid=[sum_x / count, sum_y / count],
    )


def classify_layer_archetype(layer: LayerModel) -> LayerArchetype:
    """Determine the spatial archetype of an After Effects layer."""
    # 1. 3D Layers / Cameras / Lights
    if getattr(layer, "threeD", False) or getattr(layer, "layer_kind", "av") in ("camera", "light"):
        return LayerArchetype.THREE_D

    # 2. Live Nested Precomps
    source_item = getattr(layer, "source_item", None)
    if source_item is not None and getattr(source_item, "kind", "") == "comp":
        return LayerArchetype.LIVE_PRECOMP

    # 3. Typography / Text
    if (
        getattr(layer, "typographic_info", None) is not None
        or getattr(layer, "content_tag", None) in ("TYPE", "TT", "BODY", "SUP", "CTA", "LGL", "LEGALS")
        or "txt" in layer.name.lower()
        or "title" in layer.name.lower()
        or "text" in layer.name.lower()
    ):
        return LayerArchetype.TYPE

    # 4. Keyed Alpha / Mattes / Masks
    masks = getattr(layer, "masks", None)
    effects = getattr(layer, "effects", None) or []
    has_keying_effect = False
    for eff in effects:
        m_name = (getattr(eff, "match_name", None) or getattr(eff, "matchName", None) or getattr(eff, "name", None) or getattr(eff, "display_name", "") or "").lower()
        if "key" in m_name or "matte" in m_name or "roto" in m_name:
            has_keying_effect = True
            break
    if (
        (masks is not None and len(masks) > 0)
        or getattr(layer, "track_matte", None) is not None
        or has_keying_effect
    ):
        return LayerArchetype.KEYED_ALPHA

    # 5. 2D Vector / Shape Layers / Illustrator Art
    if (
        (source_item is not None and getattr(source_item, "is_vector", False))
        or getattr(layer, "collapseTransformations", False)
        or any(layer.name.lower().endswith(ext) for ext in (".ai", ".eps", ".svg"))
        or "shape" in layer.name.lower()
        or "logo" in layer.name.lower()
        or "vector" in layer.name.lower()
    ):
        return LayerArchetype.VECTOR_2D

    return LayerArchetype.VECTOR_2D


def compute_edge_aware_offset(
    bounds: ArtworkBounds,
    canvas_w: float,
    canvas_h: float,
    safe_rect: Tuple[float, float, float, float],
    gravity: str = "center",
) -> Tuple[float, float]:
    """Compute (dx, dy) translation to pin the visual artwork contour to safe-zone edges.

    Args:
        bounds: Tight artwork boundary relative to the layer's local canvas.
        canvas_w: Total source layer canvas width.
        canvas_h: Total source layer canvas height.
        safe_rect: Target safe area (sx, sy, sw, sh).
        gravity: Placement gravity ('top', 'bottom', 'leftMid', 'rightMid', 'center', 'fill').

    Returns:
        (dx, dy): Adjustment offset to align the actual artwork edges to the safe area.
    """
    if bounds.width <= 0 or bounds.height <= 0:
        return (0.0, 0.0)

    sx, sy, sw, sh = safe_rect
    art_l = bounds.left
    art_r = bounds.left + bounds.width
    art_t = bounds.top
    art_b = bounds.top + bounds.height
    art_cx = bounds.centroid[0] if bounds.centroid else (art_l + bounds.width / 2.0)
    art_cy = bounds.centroid[1] if bounds.centroid else (art_t + bounds.height / 2.0)

    # Offset between naive canvas center and true artwork visual center
    canvas_cx = canvas_w / 2.0
    canvas_cy = canvas_h / 2.0
    opt_offset_x = canvas_cx - art_cx
    opt_offset_y = canvas_cy - art_cy

    g = (gravity or "center").strip()

    if g == "top":
        # Pin top edge of artwork to top safe boundary
        return (opt_offset_x, -art_t)
    elif g == "bottom":
        # Pin bottom edge of artwork to bottom safe boundary
        return (opt_offset_x, canvas_h - art_b)
    elif g == "leftMid":
        # Pin left edge of artwork to left safe boundary
        return (-art_l, opt_offset_y)
    elif g == "rightMid":
        # Pin right edge of artwork to right safe boundary
        return (canvas_w - art_r, opt_offset_y)
    elif g == "center":
        # Optical center alignment
        return (opt_offset_x, opt_offset_y)

    return (0.0, 0.0)


def compute_composite_precomp_hull(
    child_bounds: Sequence[ArtworkBounds],
    child_transforms: Optional[Sequence[Dict[str, Any]]] = None,
) -> ArtworkBounds:
    """Compute the unified convex bounding hull of all visible children inside a precomp."""
    if not child_bounds:
        return ArtworkBounds()

    min_x = float("inf")
    max_x = float("-inf")
    min_y = float("inf")
    max_y = float("-inf")

    total_weight = 0.0
    weighted_x = 0.0
    weighted_y = 0.0

    for i, b in enumerate(child_bounds):
        if b.width <= 0 or b.height <= 0:
            continue

        l = b.left
        r = b.left + b.width
        t = b.top
        bottom = b.top + b.height

        if child_transforms and i < len(child_transforms):
            tf = child_transforms[i]
            pos = tf.get("position", [0.0, 0.0])
            s = [x / 100.0 for x in tf.get("scale", [100.0, 100.0])]
            l = pos[0] + (l * s[0])
            r = pos[0] + (r * s[0])
            t = pos[1] + (t * s[1])
            bottom = pos[1] + (bottom * s[1])

        if l < min_x:
            min_x = l
        if r > max_x:
            max_x = r
        if t < min_y:
            min_y = t
        if bottom > max_y:
            max_y = bottom

        area = (r - l) * (bottom - t)
        cx = (l + r) / 2.0
        cy = (t + bottom) / 2.0
        weighted_x += cx * area
        weighted_y += cy * area
        total_weight += area

    if min_x == float("inf") or total_weight <= 0.0:
        return ArtworkBounds()

    return ArtworkBounds(
        left=min_x,
        top=min_y,
        width=max(0.0, max_x - min_x),
        height=max(0.0, max_y - min_y),
        centroid=[weighted_x / total_weight, weighted_y / total_weight],
    )
