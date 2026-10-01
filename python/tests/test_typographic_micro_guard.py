# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_typographic_micro_guard.py
Issue #339 (narrow slice) -- stroke-width floor clamp math.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.typographic_micro_guard import (
    MIN_VISIBLE_STROKE_PX,
    clamp_stroke_width_px,
)


class TestPassThroughWhenSafe:
    def test_large_scale_up_passes_through_unchanged(self):
        # 2px raw, scaling up by 3x -> 6px effective, well above the floor.
        assert clamp_stroke_width_px(2.0, 3.0) == 2.0

    def test_effective_width_exactly_at_floor_passes_through(self):
        # 2px raw * 0.5 scale = exactly 1.0px effective -- already safe,
        # must not be altered (boundary case, not "less than").
        assert clamp_stroke_width_px(2.0, 0.5) == 2.0

    def test_scale_factor_of_one_passes_through_already_safe_widths(self):
        # At 1:1 scale, effective == raw, so only raw values already at
        # or above the floor pass through unchanged. 0.5px is NOT one of
        # these -- it's sub-pixel-risk even with no scaling applied,
        # so it correctly gets boosted (covered separately below).
        for raw in (1.0, 2.0, 10.0):
            assert clamp_stroke_width_px(raw, 1.0) == raw


class TestFloorClampFires:
    def test_small_scale_boosts_raw_value(self):
        # 2px raw, scaling down by 0.1 -> 0.2px effective, below the
        # 1.0px floor. Must return 1.0 / 0.1 = 10.0, not 2.0 * 0.1.
        result = clamp_stroke_width_px(2.0, 0.1)
        assert result == 10.0
        # Round-trip proof: the boosted raw value, once AE's transform
        # applies the same scale factor, lands exactly on the floor.
        assert result * 0.1 == MIN_VISIBLE_STROKE_PX

    def test_boosted_value_never_produces_effective_width_below_floor(self):
        for raw, scale in [(0.5, 0.3), (1.0, 0.05), (3.0, 0.02), (0.1, 0.4)]:
            boosted = clamp_stroke_width_px(raw, scale)
            effective = boosted * scale
            assert effective >= MIN_VISIBLE_STROKE_PX - 1e-9

    def test_never_reduces_a_raw_value(self):
        """The floor clamp only ever pushes raw values UP to prevent
        disappearance -- it must never shrink a raw stroke width, since
        that would be the exact kind of unconditional S-scaling this
        module deliberately avoids."""
        for raw, scale in [(5.0, 0.01), (1.0, 0.1), (0.5, 0.5), (10.0, 0.001)]:
            assert clamp_stroke_width_px(raw, scale) >= raw


class TestDegenerateInputs:
    def test_zero_or_negative_scale_factor_passes_through(self):
        assert clamp_stroke_width_px(2.0, 0.0) == 2.0
        assert clamp_stroke_width_px(2.0, -1.0) == 2.0

    def test_zero_or_negative_raw_width_passes_through(self):
        assert clamp_stroke_width_px(0.0, 0.1) == 0.0
        assert clamp_stroke_width_px(-1.0, 0.1) == -1.0
