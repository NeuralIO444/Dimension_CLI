# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/framerate_engine.py
TASK-P2-02 (Issue #250) — Framerate & Timebase Conformance Engine.

Implements non-destructive framerate and timebase conformance per ADR 02:
  1. Computes target composition duration snapped to exact integer frames:
     total_frames = round(duration_s * target_fps)
     snapped_duration = total_frames / target_fps
  2. Preserves keyframe absolute timestamps or quantizes them to integer raster.
  3. Strict Invariant: Layer time-stretch is NEVER applied (stretch = 100.0%).
     Animation plays at natural real-world speed with increased/decreased sample density.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, List, Sequence, Tuple


# Standard Industry Framerates & NTSC Rational Constants
STANDARD_TIMELINES: Dict[str, Fraction] = {
    "23.976": Fraction(24000, 1001),
    "23.98": Fraction(24000, 1001),
    "24.0": Fraction(24, 1),
    "24": Fraction(24, 1),
    "25.0": Fraction(25, 1),
    "25": Fraction(25, 1),
    "29.97": Fraction(30000, 1001),
    "30.0": Fraction(30, 1),
    "30": Fraction(30, 1),
    "48.0": Fraction(48, 1),
    "50.0": Fraction(50, 1),
    "59.94": Fraction(60000, 1001),
    "60.0": Fraction(60, 1),
    "60": Fraction(60, 1),
    "120.0": Fraction(120, 1),
}


@dataclass(frozen=True)
class FrameratePlan:
    source_fps: float
    target_fps: float
    source_duration_s: float
    target_duration_s: float
    source_frame_count: int
    target_frame_count: int
    frame_count_delta: int
    duration_delta_s: float
    layer_stretch_factor: float = 100.0  # Invariant: Never fractional stretch
    is_drop_frame: bool = False


class FramerateEngine:
    """Pure mathematical engine for timebase and framerate conformance."""

    @staticmethod
    def normalize_fps(fps: float, tolerance: float = 0.02) -> float:
        """Resolves float fps to standard rational NTSC or integer timebase."""
        if fps <= 0.0:
            return 0.0

        # Exact integer timelines (24, 25, 30, 48, 50, 60, 120)
        round_int = round(fps)
        if abs(fps - round_int) < 1e-4:
            return float(round_int)

        # Fractional NTSC timelines (23.976 -> 24000/1001, 29.97 -> 30000/1001, 59.94 -> 60000/1001)
        best_diff = float("inf")
        best_val = float(fps)
        for frac in STANDARD_TIMELINES.values():
            diff = abs(fps - float(frac))
            if diff < best_diff and diff <= tolerance:
                best_diff = diff
                best_val = float(frac)

        return best_val

    @classmethod
    def snap_duration(
        cls,
        duration_s: float,
        target_fps: float,
        min_frames: int = 1,
    ) -> Tuple[float, int]:
        """Calculates exact integer frame count and snaps duration to frame raster.

        Formula:
          total_frames = max(min_frames, round(duration_s * target_fps))
          snapped_duration = total_frames / target_fps

        Returns:
          (snapped_duration_seconds, total_integer_frames)
        """
        if duration_s <= 0.0:
            return (0.0, 0)
        norm_fps = cls.normalize_fps(target_fps)
        if norm_fps <= 0.0:
            # Static still / non-temporal asset
            return (duration_s, 1)

        total_frames = max(min_frames, int(round(duration_s * norm_fps)))
        snapped_duration = float(total_frames / norm_fps)
        return (snapped_duration, total_frames)

    @classmethod
    def quantize_timestamp(cls, timestamp_s: float, target_fps: float) -> float:
        """Snaps an individual timestamp to the nearest target frame boundary."""
        norm_fps = cls.normalize_fps(target_fps)
        if norm_fps <= 0.0 or timestamp_s < 0.0:
            return timestamp_s
        frame_idx = round(timestamp_s * norm_fps)
        return float(frame_idx / norm_fps)

    @classmethod
    def quantize_keyframe_times(
        cls,
        times: Sequence[float],
        target_fps: float,
    ) -> List[float]:
        """Quantizes an array of keyframe timestamps to target frame boundaries.

        Monotonicity is strictly preserved: if two distinct source keyframes
        collapse onto the same target frame tick, a sub-frame epsilon (1e-4s)
        separation is enforced so keyframe order and holds are preserved.
        """
        if not times:
            return []
        norm_fps = cls.normalize_fps(target_fps)
        if norm_fps <= 0.0:
            return list(times)

        quantized: List[float] = []
        min_delta = 1.0 / (norm_fps * 100.0)  # Sub-frame guard

        for i, t in enumerate(times):
            q_t = cls.quantize_timestamp(t, norm_fps)
            if quantized and q_t <= quantized[-1]:
                # Preserve strictly non-decreasing keyframe ordering
                q_t = quantized[-1] + min_delta
            quantized.append(q_t)

        return quantized

    @classmethod
    def plan_conformance(
        cls,
        source_fps: float,
        source_duration_s: float,
        target_fps: float,
    ) -> FrameratePlan:
        """Generates a complete framerate conformance plan.

        Verifies ADR 02 Invariant:
          - Layer stretch factor remains 100.0 (no temporal warping).
          - Comp duration snaps to exact target integer frames.
        """
        norm_src_fps = cls.normalize_fps(source_fps)
        norm_tgt_fps = cls.normalize_fps(target_fps)

        src_frames = int(round(source_duration_s * norm_src_fps)) if norm_src_fps > 0 else 1
        tgt_duration, tgt_frames = cls.snap_duration(source_duration_s, norm_tgt_fps)

        is_drop_frame = any(
            abs(norm_tgt_fps - float(frac)) < 1e-4
            for frac in (Fraction(30000, 1001), Fraction(60000, 1001))
        )

        return FrameratePlan(
            source_fps=norm_src_fps,
            target_fps=norm_tgt_fps,
            source_duration_s=source_duration_s,
            target_duration_s=tgt_duration,
            source_frame_count=src_frames,
            target_frame_count=tgt_frames,
            frame_count_delta=tgt_frames - src_frames,
            duration_delta_s=tgt_duration - source_duration_s,
            layer_stretch_factor=100.0,
            is_drop_frame=is_drop_frame,
        )
