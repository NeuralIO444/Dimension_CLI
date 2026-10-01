# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_spatial_math.py
Unit tests for spatial math: OBB polygons, 1D interval sweep-and-prune, and centroid calculation (TASK-SUB-01 / #277).
"""

import math
import time
from core.spatial_math import (
    is_rotated,
    obb_corners,
    aabb_of,
    polygon_area,
    find_overlapping_intervals_1d,
    polygon_centroid,
    local_point_to_world,
)


class TestLocalPointToWorld:
    """#440: shared local-to-world conversion, extracted from gravity.py's
    HP-01 block and occlusion/mask_solver.py::compute_world_bounds, which
    had to duplicate this formula by hand before this existed."""

    def test_identity_anchor_and_scale(self):
        # anchor at origin, 100% scale: world = position + local
        world = local_point_to_world((100.0, 200.0), (0.0, 0.0), (1.0, 1.0), (50.0, -30.0))
        assert world == (150.0, 170.0)

    def test_nonzero_anchor_offsets_the_local_point(self):
        world = local_point_to_world((0.0, 0.0), (10.0, 20.0), (1.0, 1.0), (10.0, 20.0))
        assert world == (0.0, 0.0)

    def test_layer_scale_applies_to_the_anchor_relative_offset(self):
        world = local_point_to_world((0.0, 0.0), (0.0, 0.0), (4.0, 4.0), (-100.0, -100.0))
        assert world == (-400.0, -400.0)

    def test_matches_mask_solver_compute_world_bounds_origin(self):
        # compute_world_bounds's x0,y0 is exactly local_point_to_world of
        # (source_rect left, top) -- pin that equivalence directly.
        from core.occlusion.mask_solver import compute_world_bounds

        position, anchor, scale = [400.0, 400.0], [10.0, 5.0], [150.0, 150.0]
        source_rect = [0.0, 0.0, 60.0, 40.0]
        bounds = compute_world_bounds(position, anchor, scale, source_rect)
        x0, y0 = local_point_to_world(position, anchor, (1.5, 1.5), (source_rect[0], source_rect[1]))
        assert bounds["l"] == x0
        assert bounds["t"] == y0

    def test_rotation_90_degrees(self):
        # Local offset (200, 0) rotated 90 deg clockwise should become (0, 200) in world space
        world = local_point_to_world((960.0, 540.0), (0.0, 0.0), (1.0, 1.0), (200.0, 0.0), rotation_deg=90.0)
        assert math.isclose(world[0], 960.0, abs_tol=1e-5)
        assert math.isclose(world[1], 740.0, abs_tol=1e-5)

    def test_rotation_180_degrees(self):
        # Local offset (200, 50) rotated 180 deg becomes (-200, -50)
        world = local_point_to_world((500.0, 500.0), (0.0, 0.0), (1.0, 1.0), (200.0, 50.0), rotation_deg=180.0)
        assert math.isclose(world[0], 300.0, abs_tol=1e-5)
        assert math.isclose(world[1], 450.0, abs_tol=1e-5)

    def test_rotation_270_degrees(self):
        # Local offset (200, 0) rotated 270 deg (or -90 deg) becomes (0, -200)
        world = local_point_to_world((960.0, 540.0), (0.0, 0.0), (1.0, 1.0), (200.0, 0.0), rotation_deg=270.0)
        assert math.isclose(world[0], 960.0, abs_tol=1e-5)
        assert math.isclose(world[1], 340.0, abs_tol=1e-5)

    def test_rotation_45_degrees_with_scale_and_anchor(self):
        # Local point (150, 50), anchor (50, 50) -> delta (100, 0)
        # Scaled 2x -> (200, 0)
        # Rotated 45 deg -> (200 * cos(45°), 200 * sin(45°)) = (100*sqrt(2), 100*sqrt(2))
        # Position (500, 500) -> (500 + 100*sqrt(2), 500 + 100*sqrt(2))
        world = local_point_to_world(
            (500.0, 500.0), (50.0, 50.0), (2.0, 2.0), (150.0, 50.0), rotation_deg=45.0
        )
        expected = 500.0 + 100.0 * math.sqrt(2)
        assert math.isclose(world[0], expected, abs_tol=1e-5)
        assert math.isclose(world[1], expected, abs_tol=1e-5)

    def test_compute_world_bounds_with_rotation(self):
        from core.occlusion.mask_solver import compute_world_bounds

        position = [500.0, 500.0]
        anchor = [50.0, 50.0]
        scale = [100.0, 100.0]
        source_rect = [0.0, 0.0, 100.0, 100.0]  # center is at (50, 50), corner at (0, 0) is 50px away from anchor

        # 45 deg rotation of 100x100 box centered at anchor
        bounds = compute_world_bounds(position, anchor, scale, source_rect, rotation_deg=45.0)
        diag = 100.0 * math.sqrt(2)
        half_diag = diag / 2.0
        assert math.isclose(bounds["l"], 500.0 - half_diag, abs_tol=1e-3)
        assert math.isclose(bounds["r"], 500.0 + half_diag, abs_tol=1e-3)
        assert math.isclose(bounds["t"], 500.0 - half_diag, abs_tol=1e-3)
        assert math.isclose(bounds["b"], 500.0 + half_diag, abs_tol=1e-3)


class TestSpatialMathBasics:
    def test_is_rotated_thresholds(self):
        assert not is_rotated(0.0)
        assert not is_rotated(360.0)
        assert not is_rotated(-720.0)
        assert is_rotated(45.0)
        assert is_rotated(0.001)

    def test_rotated_corners_and_bounding_box(self):
        # 100x100 square from l=50, r=150, t=50, b=150 rotated 45 degrees around center (100, 100)
        bounds = {"l": 50.0, "r": 150.0, "t": 50.0, "b": 150.0}
        corners = obb_corners(bounds, 45.0, (100.0, 100.0))
        assert len(corners) == 4
        bb = aabb_of(corners)
        # Expected diagonal length is 100 * sqrt(2) ~= 141.42
        diag = 100.0 * math.sqrt(2)
        assert math.isclose(bb["r"] - bb["l"], diag, abs_tol=1e-3)
        assert math.isclose(bb["b"] - bb["t"], diag, abs_tol=1e-3)

    def test_polygon_area_square(self):
        corners = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
        assert polygon_area(corners) == 10000.0


class TestIntervalSweepAndPrune:
    def test_disjoint_intervals(self):
        intervals = [
            (0.0, 10.0, 1),
            (20.0, 30.0, 2),
            (40.0, 50.0, 3),
        ]
        overlaps = find_overlapping_intervals_1d(intervals)
        assert overlaps == []

    def test_overlapping_intervals(self):
        intervals = [
            (0.0, 25.0, 1),
            (20.0, 45.0, 2),
            (40.0, 60.0, 3),
        ]
        overlaps = find_overlapping_intervals_1d(intervals)
        # 1 overlaps with 2 (20..25), 2 overlaps with 3 (40..45)
        assert (1, 2) in overlaps
        assert (2, 3) in overlaps
        assert (1, 3) not in overlaps

    def test_contained_intervals(self):
        intervals = [
            (0.0, 100.0, 1),
            (20.0, 40.0, 2),
            (60.0, 80.0, 3),
        ]
        overlaps = find_overlapping_intervals_1d(intervals)
        assert (1, 2) in overlaps
        assert (1, 3) in overlaps
        assert (2, 3) not in overlaps

    def test_sweep_and_prune_performance_200_items(self):
        """200 items should complete in under 5ms, avoiding O(N^2) bottlenecks."""
        intervals = [(float(i * 5), float(i * 5 + 15), i) for i in range(200)]
        start = time.perf_counter()
        overlaps = find_overlapping_intervals_1d(intervals)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        assert len(overlaps) > 0
        assert elapsed_ms < 15.0, f"Sweep-and-prune took {elapsed_ms:.2f}ms (expected < 15ms)"


class TestPolygonCentroid:
    def test_square_centroid(self):
        square = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
        cx, cy = polygon_centroid(square)
        assert math.isclose(cx, 50.0, abs_tol=1e-5)
        assert math.isclose(cy, 50.0, abs_tol=1e-5)

    def test_triangle_centroid(self):
        triangle = [(0.0, 0.0), (60.0, 0.0), (0.0, 60.0)]
        cx, cy = polygon_centroid(triangle)
        assert math.isclose(cx, 20.0, abs_tol=1e-5)
        assert math.isclose(cy, 20.0, abs_tol=1e-5)

    def test_degenerate_line_fallback(self):
        line = [(0.0, 0.0), (100.0, 100.0)]
        cx, cy = polygon_centroid(line)
        assert math.isclose(cx, 50.0, abs_tol=1e-5)
        assert math.isclose(cy, 50.0, abs_tol=1e-5)
