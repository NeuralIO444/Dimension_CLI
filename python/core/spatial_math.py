# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Dimension Engine — Spatial Math (TASK-ENG-04 / Pre-Mortem Scenario 4)

Oriented-bounding-box geometry for the Spatial Occlusion Engine.

WHY THIS EXISTS: `occlusion_engine._compute_world_bounds()` builds an
axis-aligned box and ignores `rotation` completely. For an unrotated layer
that box IS the footprint. For a rotated one it is neither the footprint
nor even the footprint's enclosing box — it is the *unrotated* rect at the
layer's position, which is simply the wrong region of the mask to sample.

Two distinct errors follow from that, and they pull in opposite directions:

  * A rotated layer whose real pixels sit entirely inside the GO zone can
    still be reported as violating, because the axis-aligned box it was
    measured through pokes into CUTOFF. That is the false positive
    TASK-ENG-04 is about.
  * A rotated layer really can be clipped while its unrotated box looks
    clean, which is a false NEGATIVE — worse, because nothing flags it.

Everything here is pure: no I/O, no mask, no engine state, no cv2. That is
deliberate — the geometry is the part worth testing exhaustively, and
keeping it out of `occlusion_engine.py` means it can be exercised without
constructing a mask.

DEPENDENCY NOTE: Pre-Mortem Scenario 4 names Shapely and
`cv2.intersectConvexConvex` as the slow path it wants avoided. Neither is
used. The narrowphase is four vectorised half-plane comparisons over the
candidate slice — numpy only, no new dependency to pin or freeze.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np  # type: ignore

Point = Tuple[float, float]

# Below this many degrees a layer is treated as unrotated. AE stores
# rotation as a float, so a comp nudged to 0.0000001° must not silently
# switch onto the (slower, and behaviourally different) rotated path.
ROTATION_EPSILON_DEG = 1e-6

# Half-plane tolerance in pixels. Points exactly on an edge count as
# inside; without the slack, floating-point error drops boundary pixels
# and the narrowphase under-reports overlap by a sliver.
_EDGE_TOLERANCE_PX = 1e-9


def is_rotated(rotation_deg: Optional[float]) -> bool:
    """True when rotation is far enough from a multiple of 360° to matter.

    Normalising first means 360° and -720° are correctly treated as
    unrotated — a layer spun a whole turn has an identical footprint, and
    sending it down the narrowphase would burn time for no difference.
    """
    if rotation_deg is None:
        return False
    try:
        normalized = float(rotation_deg) % 360.0
    except (TypeError, ValueError):
        return False
    return min(normalized, 360.0 - normalized) > ROTATION_EPSILON_DEG


def local_point_to_world(position: Sequence[float],
                         anchor: Sequence[float],
                         scale: Sequence[float],
                         local_point: Sequence[float],
                         rotation_deg: float = 0.0) -> Point:
    """Convert a point from a layer's LOCAL coordinate space to world space.

    `local_point` must be in the same frame `anchorPoint` and
    `sourceRectAtTime` report in -- pre-transform, relative to the layer's
    own origin. `scale` is a fraction (1.0 = 100%), already divided down
    from AE's percent convention; callers own that conversion since some
    (gravity.py) also need the raw percent value elsewhere. `rotation_deg`
    is in degrees clockwise on screen (matching AE convention).

    Formula:
      1. Offset by anchor: (lx - ax, ly - ay)
      2. Scale along local axes: (dx * sx, dy * sy)
      3. Rotate by rotation_deg clockwise: (sx_val * cos(θ) - sy_val * sin(θ), sx_val * sin(θ) + sy_val * cos(θ))
      4. Translate by position: position + rotated_vector

    Confirmed against live AE round-trips (issues #436, #439, 2026-09-04/05):
    `sourceRectAtTime` and `anchorPoint` share one local frame, independent of
    anchor, scale, and rotation.
    """
    px, py = float(position[0]), float(position[1])
    ax, ay = float(anchor[0]), float(anchor[1])
    sx, sy = float(scale[0]), float(scale[1])
    lx, ly = float(local_point[0]), float(local_point[1])
    dx = (lx - ax) * sx
    dy = (ly - ay) * sy
    if is_rotated(rotation_deg):
        rad = math.radians(float(rotation_deg))
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        rx = dx * cos_r - dy * sin_r
        ry = dx * sin_r + dy * cos_r
    else:
        rx, ry = dx, dy
    return (px + rx, py + ry)


def obb_corners(bounds: Dict[str, float],
                rotation_deg: float,
                pivot: Point) -> List[Point]:
    """The four corners of `bounds` rotated `rotation_deg` about `pivot`.

    Returned in the order TL, TR, BR, BL of the ORIGINAL rect, so the
    winding stays consistent and callers can rely on the polygon being
    convex and non-self-intersecting.

    `pivot` is the layer's anchor in world space, which is exactly its
    `position`: `_compute_world_bounds` places the rect such that the
    anchor point lands on `position`, and AE rotates a layer about its
    anchor. Passing the box centre instead would rotate the right shape
    around the wrong point — correct-looking geometry in the wrong place.

    Screen space has y pointing DOWN, so a positive angle here reads as
    clockwise on screen, matching AE's rotation UI.
    """
    l, t = float(bounds["l"]), float(bounds["t"])
    r, b = float(bounds["r"]), float(bounds["b"])
    px, py = float(pivot[0]), float(pivot[1])

    rad = math.radians(float(rotation_deg))
    cos_r, sin_r = math.cos(rad), math.sin(rad)

    corners: List[Point] = []
    for x, y in ((l, t), (r, t), (r, b), (l, b)):
        dx, dy = x - px, y - py
        corners.append((px + dx * cos_r - dy * sin_r,
                        py + dx * sin_r + dy * cos_r))
    return corners


def aabb_of(points: Sequence[Point]) -> Dict[str, float]:
    """Smallest axis-aligned box containing `points`.

    This is the BROADPHASE box for a rotated layer — the region that must
    be sampled to be sure nothing is missed. It is deliberately an
    over-estimate of the true footprint; narrowing it back down is exactly
    what `points_inside_convex_polygon` is for.
    """
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return {"l": min(xs), "t": min(ys), "r": max(xs), "b": max(ys)}


def polygon_area(corners: Sequence[Point]) -> float:
    """Absolute area via the shoelace formula. Used to sanity-check that a
    rotation preserved the footprint's size (it must)."""
    n = len(corners)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x0, y0 = corners[i]
        x1, y1 = corners[(i + 1) % n]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def points_inside_convex_polygon(xs: np.ndarray,
                                 ys: np.ndarray,
                                 corners: Sequence[Point]) -> np.ndarray:
    """Boolean mask of which (xs, ys) fall inside the convex `corners`.

    Half-plane test: for a convex polygon with consistent winding, a point
    is inside when it sits on the same side of every edge. The winding is
    derived from the signed area rather than assumed, so the caller cannot
    silently invert the result by passing corners the other way round —
    which would return the polygon's complement, i.e. mask exactly the
    pixels it meant to keep.

    Vectorised over the whole array: this runs once per rotated layer that
    survives broadphase, not once per pixel.
    """
    inside = np.ones(np.shape(xs), dtype=bool)
    n = len(corners)
    if n < 3:
        return np.zeros(np.shape(xs), dtype=bool)

    signed_area2 = 0.0
    for i in range(n):
        x0, y0 = corners[i]
        x1, y1 = corners[(i + 1) % n]
        signed_area2 += x0 * y1 - x1 * y0
    if signed_area2 == 0.0:
        # Degenerate (zero-area) polygon — nothing is inside it.
        return np.zeros(np.shape(xs), dtype=bool)
    winding = 1.0 if signed_area2 > 0 else -1.0

    for i in range(n):
        x0, y0 = corners[i]
        x1, y1 = corners[(i + 1) % n]
        cross = (x1 - x0) * (ys - y0) - (y1 - y0) * (xs - x0)
        inside &= (cross * winding) >= -_EDGE_TOLERANCE_PX
    return inside


def footprint_mask(corners: Sequence[Point],
                   l: int, t: int, r: int, b: int) -> np.ndarray:
    """Boolean mask, shaped (b-t, r-l), of the polygon over that slice.

    Pixel centres are sampled (+0.5), not corners: a pixel counts as
    covered when the polygon actually covers its middle. Testing the
    top-left corner instead biases every footprint half a pixel up-left,
    which is invisible on a big layer and material on a thin one.
    """
    if r <= l or b <= t:
        return np.zeros((0, 0), dtype=bool)
    ys, xs = np.mgrid[t:b, l:r]
    return points_inside_convex_polygon(xs + 0.5, ys + 0.5, corners)


def find_overlapping_intervals_1d(
    intervals: Sequence[Tuple[float, float, int]]
) -> List[Tuple[int, int]]:
    """TASK-SUB-01 (#277): 1D Sweep-and-Prune interval overlap detector.
    
    Given a sequence of (start, end, id) intervals, returns all unique
    overlapping pairs (id_a, id_b) in O(N log N + K) time.
    """
    sorted_intervals = sorted(intervals, key=lambda it: it[0])
    overlaps = []
    n = len(sorted_intervals)
    for i in range(n):
        x_min_i, x_max_i, id_i = sorted_intervals[i]
        for j in range(i + 1, n):
            x_min_j, x_max_j, id_j = sorted_intervals[j]
            if x_min_j >= x_max_i:
                # Pruning invariant: subsequent intervals start after current interval ends
                break
            if x_max_j > x_min_i:
                pair = (id_i, id_j) if id_i < id_j else (id_j, id_i)
                overlaps.append(pair)
    return sorted(list(set(overlaps)))


def polygon_centroid(corners: Sequence[Point]) -> Point:
    """TASK-SUB-01 (#277): Centroid of a 2D polygon.
    
    Uses Green's theorem / shoelace formula for non-degenerate polygons.
    Falls back to arithmetic mean of vertices for degenerate/zero-area shapes.
    """
    n = len(corners)
    if n == 0:
        return (0.0, 0.0)
    if n < 3:
        return (sum(p[0] for p in corners) / float(n), sum(p[1] for p in corners) / float(n))

    area_term = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        x0, y0 = corners[i]
        x1, y1 = corners[(i + 1) % n]
        cross = (x0 * y1 - x1 * y0)
        area_term += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross

    area = area_term / 2.0
    if abs(area) < 1e-9:
        # Degenerate: fall back to arithmetic mean
        return (sum(p[0] for p in corners) / float(n), sum(p[1] for p in corners) / float(n))

    cx = cx / (6.0 * area)
    cy = cy / (6.0 * area)
    return (float(cx), float(cy))
