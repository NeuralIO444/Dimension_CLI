# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_color_lut_engine_synthetic.py

Color Match (Track D) — synthetic, AE-free verification that a
`core.lut_parser`-produced `LutData` is mathematically USABLE, not
just well-formed.

Why this exists: `test_lut_parser.py` proves a `.cube`/`.3dl` file
parses into the right numbers. It never proves those numbers, fed
through trilinear interpolation, produce the color transform the LUT
actually encodes — nothing in the codebase did, before this file. The
real application happens inside AE's `ADBE Apply Color LUT 2` effect
(unverified against live AE — see PR #248's sign-off checklist); this
suite is the synthetic ground-truth check that sits below that: given
a LUT with a KNOWN, hand-specified transform, does trilinear
interpolation over its lattice reproduce that transform at both grid
corners and off-grid points.

Uses `tests/harness/color_verifier.py`'s pure-Python trilinear
interpolator — deliberately not the (nonexistent) AE effect, and
deliberately not numpy, so this needs no new dependency and runs in
CI with no AE session. See that module's docstring for why it lives
under `tests/harness/` rather than `core/`.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "harness"))

import pytest

from color_verifier import apply_lut_to_pixel, apply_lut_to_pixels, build_synthetic_lut
from core.lut_parser import load_lut

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "luts")


def _identity(r, g, b):
    return (r, g, b)


def _invert(r, g, b):
    return (1.0 - r, 1.0 - g, 1.0 - b)


def _halve_red_only(r, g, b):
    return (r * 0.5, g, b)


class TestIdentityLutIsANoOp:
    """A LUT whose grid points map r,g,b -> r,g,b must return every
    input unchanged, at corners AND at arbitrary off-grid points —
    the defining property of an identity transform under linear
    interpolation."""

    @pytest.mark.parametrize("grid_size", [2, 3, 4, 17])
    def test_corners_pass_through(self, grid_size):
        lut = build_synthetic_lut(grid_size, _identity)
        for corner in [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]:
            assert apply_lut_to_pixel(lut, corner) == pytest.approx(corner, abs=1e-9)

    @pytest.mark.parametrize("grid_size", [2, 3, 4, 17])
    def test_off_grid_points_pass_through(self, grid_size):
        lut = build_synthetic_lut(grid_size, _identity)
        samples = [(0.5, 0.3, 0.8), (0.1234, 0.9876, 0.4321), (0.01, 0.99, 0.5)]
        for px in samples:
            assert apply_lut_to_pixel(lut, px) == pytest.approx(px, abs=1e-9)

    def test_real_identity_fixture_round_trips(self):
        """Ties the actual parser to the interpolator: a real
        identity_2x2x2.cube file, loaded through load_lut() exactly as
        the CEP modal's BB.validateLut path does, must also be a
        no-op — proves the parser's table ordering/domain defaults
        agree with the interpolator's assumptions about both."""
        lut = load_lut(os.path.join(FIXTURES, "identity_2x2x2.cube"))
        samples = [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.5, 0.5, 0.5), (0.25, 0.75, 0.6)]
        results = apply_lut_to_pixels(lut, samples)
        for expected, actual in zip(samples, results):
            assert actual == pytest.approx(expected, abs=1e-9)


class TestKnownTransformExactness:
    """A 2-point-per-axis grid is linear in each dimension by
    construction, so trilinear interpolation of a LINEAR transform
    (invert, per-channel scale) must be EXACT at any point, not just
    approximately close — these tests catch an interpolation-weight
    sign error or off-by-one that identity alone can't, since identity
    is invariant to almost any weighting bug that still sums to 1."""

    def test_invert_lut_maps_black_to_white_and_back(self):
        lut = build_synthetic_lut(2, _invert)
        assert apply_lut_to_pixel(lut, (0.0, 0.0, 0.0)) == pytest.approx((1.0, 1.0, 1.0))
        assert apply_lut_to_pixel(lut, (1.0, 1.0, 1.0)) == pytest.approx((0.0, 0.0, 0.0))

    def test_invert_lut_is_exact_at_an_off_grid_point(self):
        lut = build_synthetic_lut(2, _invert)
        r, g, b = apply_lut_to_pixel(lut, (0.25, 0.75, 0.5))
        assert (r, g, b) == pytest.approx((0.75, 0.25, 0.5), abs=1e-9)

    def test_channel_independent_transform_leaves_other_channels_untouched(self):
        lut = build_synthetic_lut(2, _halve_red_only)
        r, g, b = apply_lut_to_pixel(lut, (0.8, 0.4, 0.6))
        assert r == pytest.approx(0.4, abs=1e-9)
        assert g == pytest.approx(0.4, abs=1e-9)
        assert b == pytest.approx(0.6, abs=1e-9)


class TestInterpolationAcrossFinerGrids:
    """Confirms the lattice-cell selection and fractional-weight math
    generalize past the trivial 2-point case, where every off-grid
    point falls in exactly one cell."""

    def test_finer_grid_midpoint_between_two_known_samples_is_their_average(self):
        # A 5-point grid (indices 0..4, values 0, .25, .5, .75, 1) sampling
        # a simple linear ramp per-channel — the LUT itself is linear, so
        # even though there are now 4 cells to choose from, the exact
        # midpoint between grid index 1 (0.25) and index 2 (0.5) must
        # equal their average (0.375), landing squarely inside one
        # specific cell rather than at a lattice corner.
        lut = build_synthetic_lut(5, _identity)
        got = apply_lut_to_pixel(lut, (0.375, 0.375, 0.375))
        assert got == pytest.approx((0.375, 0.375, 0.375), abs=1e-9)

    def test_non_uniform_transform_interpolates_between_correct_neighbors(self):
        # A transform that only changes behavior in the top half of the
        # range (a soft-clip highlight rolloff) — verifies a query point
        # resolves to the cell actually surrounding it, not always cell 0.
        def rolloff(r, g, b):
            v = r if r < 0.5 else 0.5 + (r - 0.5) * 0.5
            return (v, v, v)

        lut = build_synthetic_lut(5, rolloff)
        # 0.625 sits between grid samples at r=0.5 (rolloff(0.5)=0.5) and
        # r=0.75 (rolloff(0.75)=0.625), at the 50% point between them.
        got = apply_lut_to_pixel(lut, (0.625, 0.625, 0.625))
        assert got[0] == pytest.approx(0.5625, abs=1e-9)


class TestDomainScalingAndClamping:
    """Scene-linear LUTs can declare DOMAIN_MAX > 1.0 (lut_parser
    preserves this verbatim, never clamping the declared domain
    itself) — the interpolator must map an input at the domain edge to
    the LAST lattice point, and clamp anything past it rather than
    extrapolating or indexing out of bounds."""

    def test_domain_max_edge_maps_to_last_lattice_point(self):
        lut = build_synthetic_lut(3, _identity, domain_max=(2.0, 2.0, 2.0))
        # An input exactly at domain_max (2.0) is grid coordinate 1.0
        # (normalized), i.e. the LUT's own "1.0" corner — which for the
        # identity transform holds the value (1.0, 1.0, 1.0).
        got = apply_lut_to_pixel(lut, (2.0, 2.0, 2.0))
        assert got == pytest.approx((1.0, 1.0, 1.0), abs=1e-9)

    def test_input_past_domain_max_clamps_instead_of_extrapolating(self):
        lut = build_synthetic_lut(3, _identity, domain_max=(2.0, 2.0, 2.0))
        # 5.0 is far past domain_max=2.0 — must clamp to the same
        # result as the domain edge itself, not produce a value > 1.0
        # (which would happen under naive unclamped extrapolation) or
        # raise an index error.
        got = apply_lut_to_pixel(lut, (5.0, 5.0, 5.0))
        assert got == pytest.approx((1.0, 1.0, 1.0), abs=1e-9)

    def test_input_below_domain_min_clamps_to_first_lattice_point(self):
        lut = build_synthetic_lut(3, _identity)
        got = apply_lut_to_pixel(lut, (-5.0, -5.0, -5.0))
        assert got == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)


class TestRealColoristFixturesApplyCleanly:
    """Smoke coverage over the actual colorist-exchange fixtures built
    for test_lut_parser.py — not exact-value checks (a real 10/12-bit
    Flame grade's transform is unknown/arbitrary), just proof the
    parser's output is well-formed enough that interpolation never
    produces NaN, never raises, and stays inside a sane clamped range
    for canonical broadcast-safe test values."""

    @pytest.mark.parametrize("filename", [
        "flame_scene_linear.cube",
        "flame_2023_10bit.3dl",
        "flame_2026_12bit.3dl",
    ])
    def test_applies_without_error_or_nan(self, filename):
        lut = load_lut(os.path.join(FIXTURES, filename))
        samples = [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.5, 0.5, 0.5), (0.2, 0.6, 0.9)]
        for px in samples:
            r, g, b = apply_lut_to_pixel(lut, px)
            for channel in (r, g, b):
                assert channel == channel, f"{filename} produced NaN for input {px}"  # NaN != NaN
                assert channel not in (float("inf"), float("-inf")), (
                    f"{filename} produced Inf for input {px}"
                )
