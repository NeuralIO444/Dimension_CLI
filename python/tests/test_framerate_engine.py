# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_framerate_engine.py
Unit test suite for Framerate & Timebase Conformance Engine (TASK-P2-02 / #250).
"""

import math
import pytest
from core.framerate_engine import FramerateEngine, FrameratePlan


class TestFramerateEngineBasics:
    @pytest.mark.parametrize(
        "input_fps,expected_fps",
        [
            (23.976, 24000 / 1001),
            (23.98, 24000 / 1001),
            (24.0, 24.0),
            (25.0, 25.0),
            (29.97, 30000 / 1001),
            (30.0, 30.0),
            (50.0, 50.0),
            (59.94, 60000 / 1001),
            (60.0, 60.0),
            (0.0, 0.0),
        ],
    )
    def test_normalize_fps(self, input_fps: float, expected_fps: float):
        norm = FramerateEngine.normalize_fps(input_fps)
        assert math.isclose(norm, expected_fps, abs_tol=1e-5)

    def test_23976_to_5994_duration_snapping(self):
        """10.01001s clip at 23.976 (240 frames) conforming to 59.94 (600 frames)."""
        fps_23976 = 24000 / 1001
        fps_5994 = 60000 / 1001
        src_duration = 240 / fps_23976  # exactly 10.01001001...

        plan = FramerateEngine.plan_conformance(
            source_fps=fps_23976,
            source_duration_s=src_duration,
            target_fps=fps_5994,
        )

        assert isinstance(plan, FrameratePlan)
        assert plan.source_frame_count == 240
        assert plan.target_frame_count == 600
        assert math.isclose(plan.target_duration_s, 600 / fps_5994, abs_tol=1e-7)
        assert math.isclose(plan.duration_delta_s, 0.0, abs_tol=1e-5)
        # ADR 02 Invariant: No layer time stretching
        assert plan.layer_stretch_factor == 100.0

    def test_layer_stretch_invariant_always_100(self):
        """Conforming 24fps to 60fps must never alter layer stretch factor."""
        plan = FramerateEngine.plan_conformance(24.0, 15.0, 60.0)
        assert plan.source_frame_count == 360
        assert plan.target_frame_count == 900
        assert plan.layer_stretch_factor == 100.0

    def test_keyframe_time_quantization_raster(self):
        """Keyframe timestamps snap to target frame ticks."""
        target_fps = 30.0
        # 0.03333... is frame 1 at 30fps
        t1 = 0.034
        q1 = FramerateEngine.quantize_timestamp(t1, target_fps)
        assert math.isclose(q1, 1.0 / 30.0, abs_tol=1e-5)

    def test_quantize_keyframe_stream_monotonicity(self):
        """Keyframe stream quantization preserves order even when frames collide."""
        times = [0.0, 0.005, 0.009, 0.5, 1.0]
        # At 30fps, 0.005 and 0.009 both round to frame 0 (0.0s)
        quantized = FramerateEngine.quantize_keyframe_times(times, target_fps=30.0)

        assert len(quantized) == len(times)
        # Verify strictly increasing
        for i in range(1, len(quantized)):
            assert quantized[i] > quantized[i - 1], f"Keyframes out of order at index {i}"

    def test_static_still_asset_zero_fps(self):
        """Static still image preset (0.0 fps) produces 1 frame and preserves duration."""
        plan = FramerateEngine.plan_conformance(source_fps=24.0, source_duration_s=5.0, target_fps=0.0)
        assert plan.target_frame_count == 1
        assert plan.target_duration_s == 5.0
        assert plan.layer_stretch_factor == 100.0
