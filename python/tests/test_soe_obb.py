# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_soe_obb.py — TASK-ENG-04 / Pre-Mortem Scenario 4 (#259).

Oriented bounding boxes for rotated layers, and the broadphase gate that
keeps them off the hot path.

The bar this file holds the implementation to:

  * A rotated layer whose real pixels are clear must NOT be reported as
    violating (the false positive #259 is about).
  * A rotated layer that really IS clipped must still be caught. Fixing
    false positives by loosening the test would trade a visible bug for an
    invisible one — a layer silently shipped over the caption bar.
  * An UNROTATED layer must come back byte-identical to `classify_aabb`.
    Every existing conform runs through this path; if that equivalence
    breaks, #259 stops being "rotated layers are measured correctly" and
    becomes "every conform changed".
  * The polygon work must stay off the common path. Scenario 4's 8s hang
    came from doing it unconditionally.

Synthetic masks, same helper shape as test_occlusion_engine.py.
"""

from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.occlusion_engine import OcclusionMask  # noqa: E402
from core.spatial_math import (  # noqa: E402
    ROTATION_EPSILON_DEG,
    aabb_of,
    footprint_mask,
    is_rotated,
    obb_corners,
    points_inside_convex_polygon,
    polygon_area,
)

COMP_W = 400
COMP_H = 400


def _mask(tmp_path: Path, cutoff_box=None, w=COMP_W, h=COMP_H) -> Path:
    """GO everywhere, CUTOFF painted inside `cutoff_box` (l, t, r, b)."""
    img = np.full((h, w), 255, dtype=np.uint8)
    if cutoff_box is not None:
        l, t, r, b = cutoff_box
        img[t:b, l:r] = 0
    out = tmp_path / "mask.png"
    cv2.imwrite(str(out), img)
    return out


def _bounds(l, t, r, b):
    return {"l": float(l), "t": float(t), "r": float(r), "b": float(b)}


# ── pure geometry ──────────────────────────────────────────────────

class TestIsRotated:
    def test_zero_is_not_rotated(self):
        assert not is_rotated(0.0)

    def test_none_is_not_rotated(self):
        assert not is_rotated(None)

    def test_full_turns_are_not_rotated(self):
        # A layer spun a whole turn has an identical footprint; sending it
        # down the narrowphase would cost time for no difference.
        for deg in (360.0, -360.0, 720.0, -720.0):
            assert not is_rotated(deg), deg

    def test_float_noise_is_not_rotated(self):
        # AE stores rotation as a float. A comp nudged to 1e-9 degrees must
        # not silently switch onto the slower, behaviourally different path.
        assert not is_rotated(ROTATION_EPSILON_DEG / 10.0)

    def test_real_angles_are_rotated(self):
        for deg in (0.5, 45.0, 90.0, 180.0, -45.0, 359.0):
            assert is_rotated(deg), deg

    def test_garbage_degrades_to_unrotated(self):
        # Safe direction: an unreadable value takes the fast, unchanged
        # path rather than raising mid-conform.
        assert not is_rotated("banana")


class TestObbCorners:
    def test_zero_rotation_returns_the_original_rect(self):
        c = obb_corners(_bounds(10, 20, 30, 50), 0.0, (20.0, 35.0))
        assert [(round(x, 6), round(y, 6)) for x, y in c] == [
            (10.0, 20.0), (30.0, 20.0), (30.0, 50.0), (10.0, 50.0)]

    def test_rotation_preserves_area(self):
        # Rotation is rigid: if area moved, the math is wrong.
        b = _bounds(0, 0, 100, 40)
        base = polygon_area(obb_corners(b, 0.0, (50.0, 20.0)))
        for deg in (17.0, 45.0, 90.0, 123.5, -60.0):
            got = polygon_area(obb_corners(b, deg, (50.0, 20.0)))
            assert abs(got - base) < 1e-6, deg

    def test_ninety_degrees_swaps_the_enclosing_extent(self):
        # A 100x40 box turned 90° must enclose 40x100.
        c = obb_corners(_bounds(0, 0, 100, 40), 90.0, (50.0, 20.0))
        box = aabb_of(c)
        assert abs((box["r"] - box["l"]) - 40.0) < 1e-6
        assert abs((box["b"] - box["t"]) - 100.0) < 1e-6

    def test_rotation_is_about_the_pivot_not_the_centre(self):
        # Rotating about a corner must MOVE the shape; rotating about the
        # centre would leave the enclosing box centred in place. Passing the
        # box centre when AE rotates about the anchor is a real failure mode
        # — correct shape, wrong location.
        b = _bounds(0, 0, 100, 40)
        about_centre = aabb_of(obb_corners(b, 90.0, (50.0, 20.0)))
        about_corner = aabb_of(obb_corners(b, 90.0, (0.0, 0.0)))
        assert about_centre != about_corner

    def test_positive_angle_is_clockwise_on_screen(self):
        # Screen space has y DOWN, so +90° must send +x toward +y.
        (x, y) = obb_corners(_bounds(0, 0, 10, 0), 90.0, (0.0, 0.0))[1]
        assert abs(x) < 1e-9 and y > 0

    def test_full_turn_is_identity(self):
        b = _bounds(3, 7, 33, 77)
        spun = obb_corners(b, 360.0, (10.0, 10.0))
        flat = obb_corners(b, 0.0, (10.0, 10.0))
        for (ax, ay), (bx, by) in zip(spun, flat):
            assert abs(ax - bx) < 1e-6 and abs(ay - by) < 1e-6


class TestPointsInsideConvexPolygon:
    SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]

    def test_interior_and_exterior(self):
        xs = np.array([5.0, -1.0, 11.0, 5.0])
        ys = np.array([5.0, 5.0, 5.0, -1.0])
        assert list(points_inside_convex_polygon(xs, ys, self.SQUARE)) == \
            [True, False, False, False]

    def test_points_on_the_edge_count_as_inside(self):
        xs = np.array([0.0, 10.0, 5.0])
        ys = np.array([5.0, 5.0, 0.0])
        assert points_inside_convex_polygon(xs, ys, self.SQUARE).all()

    def test_winding_is_derived_not_assumed(self):
        # Reversed corners describe the SAME square. A resolver that assumed
        # winding would return the polygon's complement here — masking
        # exactly the pixels it meant to keep.
        xs = np.array([5.0, -1.0])
        ys = np.array([5.0, 5.0])
        forward = points_inside_convex_polygon(xs, ys, self.SQUARE)
        reverse = points_inside_convex_polygon(xs, ys, list(reversed(self.SQUARE)))
        assert list(forward) == list(reverse) == [True, False]

    def test_degenerate_polygon_contains_nothing(self):
        xs = np.array([1.0]); ys = np.array([1.0])
        assert not points_inside_convex_polygon(
            xs, ys, [(0.0, 0.0), (0.0, 0.0), (0.0, 0.0)]).any()

    def test_diamond_excludes_its_bounding_box_corners(self):
        # The whole point of an OBB: a 45° quad leaves each corner of its
        # enclosing box empty. Those corners are exactly the pixels an AABB
        # wrongly attributes to the layer.
        diamond = [(5.0, 0.0), (10.0, 5.0), (5.0, 10.0), (0.0, 5.0)]
        xs = np.array([0.0, 10.0, 0.0, 10.0, 5.0])
        ys = np.array([0.0, 0.0, 10.0, 10.0, 5.0])
        assert list(points_inside_convex_polygon(xs, ys, diamond)) == \
            [False, False, False, False, True]


class TestFootprintMask:
    def test_axis_aligned_square_covers_its_pixels(self):
        m = footprint_mask([(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)],
                           0, 0, 4, 4)
        assert m.shape == (4, 4) and m.all()

    def test_empty_slice_is_empty(self):
        assert footprint_mask([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)],
                              5, 5, 5, 5).size == 0

    def test_rotated_square_covers_less_than_its_bounding_box(self):
        b = _bounds(10, 10, 50, 50)
        corners = obb_corners(b, 45.0, (30.0, 30.0))
        box = aabb_of(corners)
        l, t = int(box["l"]), int(box["t"])
        r, bo = int(math.ceil(box["r"])), int(math.ceil(box["b"]))
        covered = footprint_mask(corners, l, t, r, bo).sum()
        assert 0 < covered < (r - l) * (bo - t)


# ── the engine gate ────────────────────────────────────────────────

class TestClassifyObbEquivalence:
    """An unrotated layer must be measured exactly as before."""

    @pytest.mark.parametrize("box", [
        (50, 50, 150, 150),     # clear
        (0, 0, 400, 400),       # whole frame
        (180, 180, 260, 260),   # straddling the cutoff
        (-40, -40, 30, 30),     # partly off-screen
        (500, 500, 600, 600),   # entirely off-screen
    ])
    def test_zero_rotation_matches_classify_aabb_exactly(self, tmp_path, box):
        mask = OcclusionMask(_mask(tmp_path, (150, 150, 250, 250)), COMP_W, COMP_H)
        l, t, r, b = box
        assert mask.classify_obb(_bounds(l, t, r, b), 0.0) == \
            mask.classify_aabb(l, t, r, b)

    def test_full_turn_also_matches(self, tmp_path):
        mask = OcclusionMask(_mask(tmp_path, (150, 150, 250, 250)), COMP_W, COMP_H)
        assert mask.classify_obb(_bounds(140, 140, 260, 260), 360.0) == \
            mask.classify_aabb(140, 140, 260, 260)

    def test_default_rotation_argument_is_unrotated(self, tmp_path):
        mask = OcclusionMask(_mask(tmp_path, (150, 150, 250, 250)), COMP_W, COMP_H)
        assert mask.classify_obb(_bounds(140, 140, 260, 260)) == \
            mask.classify_aabb(140, 140, 260, 260)


class TestFalsePositiveElimination:
    """The bug #259 exists to fix."""

    def test_rotated_layer_clear_of_cutoff_is_not_reported_as_violating(self, tmp_path):
        # A square rotated 45° becomes a diamond, which leaves every CORNER
        # of its enclosing box empty. Putting CUTOFF in one of those corners
        # is the cleanest possible statement of the bug: the box overlaps it,
        # the layer's real pixels cannot.
        #
        # An earlier version of this fixture used a thin bar and was simply
        # wrong — the rotated bar never reached the cutoff square at all, so
        # the false-positive condition was never set up. The precondition
        # asserts below are what caught that; keep them.
        mask = OcclusionMask(_mask(tmp_path, (80, 80, 105, 105)), COMP_W, COMP_H)
        bounds = _bounds(100, 100, 200, 200)   # 100x100 square, centre (150,150)
        pivot = (150.0, 150.0)

        broad = aabb_of(obb_corners(bounds, 45.0, pivot))
        assert broad["l"] <= 80 and broad["t"] <= 80, \
            "fixture is vacuous — the enclosing box must reach the cutoff"
        assert mask.classify_aabb(
            broad["l"], broad["t"], broad["r"], broad["b"]
        )["overlap_cutoff_px"] > 0, "fixture is vacuous — AABB must false-positive"

        assert mask.classify_obb(bounds, 45.0, pivot)["overlap_cutoff_px"] == 0

    def test_rotated_layer_that_really_overlaps_is_still_caught(self, tmp_path):
        # The inverse guard. Fixing false positives by loosening the test
        # would trade a visible bug for an invisible one.
        mask = OcclusionMask(_mask(tmp_path, (150, 150, 250, 250)), COMP_W, COMP_H)
        bounds = _bounds(160, 160, 240, 240)
        assert mask.classify_obb(bounds, 45.0, (200.0, 200.0))["overlap_cutoff_px"] > 0

    def test_narrowphase_never_reports_more_overlap_than_broadphase(self, tmp_path):
        # The footprint is a subset of its enclosing box, so its overlap
        # can only shrink. Growing would mean the mask was sampled outside
        # the layer entirely.
        mask = OcclusionMask(_mask(tmp_path, (150, 150, 250, 250)), COMP_W, COMP_H)
        bounds = _bounds(120, 180, 280, 220)
        pivot = (200.0, 200.0)
        for deg in (10.0, 30.0, 45.0, 75.0, 100.0):
            corners = obb_corners(bounds, deg, pivot)
            box = aabb_of(corners)
            broad = mask.classify_aabb(box["l"], box["t"], box["r"], box["b"])
            narrow = mask.classify_obb(bounds, deg, pivot)
            assert narrow["overlap_cutoff_px"] <= broad["overlap_cutoff_px"], deg

    def test_centroid_comes_from_the_footprint_not_the_box(self, tmp_path):
        # A 45° quad leaves its box's centre... still inside, but the box
        # centre of an OFF-CENTRE pivot rotation can fall outside the layer
        # entirely. Classifying by a pixel the layer does not cover is how a
        # clean layer gets reported as sitting in CUTOFF.
        mask = OcclusionMask(_mask(tmp_path, (0, 0, 80, 400)), COMP_W, COMP_H)
        bounds = _bounds(200, 195, 360, 205)
        pivot = (360.0, 200.0)      # rotate about the far end
        got = mask.classify_obb(bounds, 80.0, pivot)
        assert got["centroid_zone"] in ("GO", "NUDGE", "CUTOFF")
        assert got["overlap_cutoff_px"] == 0

    def test_a_clear_rotated_layer_needs_no_translation(self, tmp_path):
        mask = OcclusionMask(_mask(tmp_path, (0, 0, 60, 60)), COMP_W, COMP_H)
        got = mask.classify_obb(_bounds(40, 95, 240, 105), 45.0, (140.0, 100.0))
        assert got["translation_vector"] is None


class TestBroadphaseGate:
    """Scenario 4's hang came from doing polygon work unconditionally."""

    def test_no_narrowphase_when_the_broadphase_is_clean(self, tmp_path, monkeypatch):
        mask = OcclusionMask(_mask(tmp_path, (300, 300, 400, 400)), COMP_W, COMP_H)
        called = []
        monkeypatch.setattr(mask, "_narrowphase",
                            lambda *a, **k: called.append(1) or {})
        mask.classify_obb(_bounds(20, 20, 120, 120), 45.0, (70.0, 70.0))
        assert called == [], "narrowphase ran for a layer that hit no CUTOFF"

    def test_no_narrowphase_for_an_unrotated_layer(self, tmp_path, monkeypatch):
        mask = OcclusionMask(_mask(tmp_path, (150, 150, 250, 250)), COMP_W, COMP_H)
        called = []
        monkeypatch.setattr(mask, "_narrowphase",
                            lambda *a, **k: called.append(1) or {})
        mask.classify_obb(_bounds(140, 140, 260, 260), 0.0)
        assert called == [], "narrowphase ran for an unrotated layer"

    def test_narrowphase_runs_only_on_a_rotated_broadphase_hit(self, tmp_path, monkeypatch):
        mask = OcclusionMask(_mask(tmp_path, (150, 150, 250, 250)), COMP_W, COMP_H)
        called = []
        real = mask._narrowphase
        monkeypatch.setattr(mask, "_narrowphase",
                            lambda *a, **k: (called.append(1), real(*a, **k))[1])
        mask.classify_obb(_bounds(160, 160, 240, 240), 45.0, (200.0, 200.0))
        assert called == [1]

    def test_unrotated_layers_stay_on_the_fast_path_at_scale(self, tmp_path):
        # Scenario 4's budget is about not regressing the common case. 200
        # unrotated layers must cost what they always did — no polygon work.
        mask = OcclusionMask(_mask(tmp_path, (150, 150, 250, 250)), COMP_W, COMP_H)
        boxes = [_bounds(140 + (i % 40), 140 + (i % 40),
                         260 + (i % 40), 260 + (i % 40)) for i in range(200)]
        t0 = time.monotonic()
        for b in boxes:
            mask.classify_obb(b, 0.0)
        obb_s = time.monotonic() - t0

        t0 = time.monotonic()
        for b in boxes:
            mask.classify_aabb(b["l"], b["t"], b["r"], b["b"])
        aabb_s = time.monotonic() - t0

        # Generous multiple — this is a "did we accidentally add polygon
        # work to every layer" guard, not a microbenchmark.
        assert obb_s < max(aabb_s * 4.0, 0.5), (
            f"unrotated path cost {obb_s:.3f}s vs {aabb_s:.3f}s for AABB")

    def test_many_rotated_colliding_layers_stay_well_under_the_hang_budget(self, tmp_path):
        # Scenario 4's failure was 150 layers taking 8s. Every layer here is
        # rotated AND overlapping CUTOFF, so all 150 hit the narrowphase —
        # the worst case that scenario describes.
        mask = OcclusionMask(_mask(tmp_path, (150, 150, 250, 250)), COMP_W, COMP_H)
        t0 = time.monotonic()
        for i in range(150):
            mask.classify_obb(_bounds(160, 160, 240, 240),
                              15.0 + (i % 60), (200.0, 200.0))
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0, f"150 rotated layers took {elapsed:.2f}s"


class TestRotationReadingFromLayers:
    """The conformed-vs-source-carryover split, applied to rotation."""

    def _engine(self, tmp_path):
        from core.occlusion_engine import OcclusionEngine
        return OcclusionEngine(
            OcclusionMask(_mask(tmp_path, (150, 150, 250, 250)), COMP_W, COMP_H),
            "p")

    def test_conformed_rotation_wins_over_source(self, tmp_path):
        eng = self._engine(tmp_path)
        assert eng._layer_rotation(
            {"rotation_z": 10.0, "conformed_transforms": {"rotation": 45.0}}) == 45.0

    def test_falls_back_to_source_rotation_z(self, tmp_path):
        eng = self._engine(tmp_path)
        assert eng._layer_rotation({"rotation_z": 30.0}) == 30.0

    def test_missing_rotation_is_zero(self, tmp_path):
        assert self._engine(tmp_path)._layer_rotation({}) == 0.0

    def test_unreadable_rotation_degrades_to_zero(self, tmp_path):
        # Safe direction: the fast, unchanged path rather than a mid-conform
        # exception.
        assert self._engine(tmp_path)._layer_rotation({"rotation_z": "tilted"}) == 0.0

    def test_pivot_is_the_position_not_the_box_centre(self, tmp_path):
        assert self._engine(tmp_path)._rotation_pivot([12.0, 34.0, 0.0]) == (12.0, 34.0)
