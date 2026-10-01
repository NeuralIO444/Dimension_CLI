# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_auditor_dynamic_tolerance.py
Tests the dynamic resolution-scaled tolerance formulation for Auditor.jsx
across standard broadcast, cinematic, and extreme out-of-home ribbon targets (TASK-SUB-03 / #279).
"""

import math


def calculate_dynamic_tolerance(width: int, height: int, base_tolerance: float = 0.01) -> float:
    """Mirrors Auditor.jsx getDynamicTolerance logic."""
    max_dim = max(width, height)
    return max(base_tolerance, 0.00001 * max_dim)


class TestAuditorDynamicTolerance:
    def test_standard_hd_1080p_tolerance(self):
        """Standard 1920x1080 comp stays at baseline tolerance."""
        tol = calculate_dynamic_tolerance(1920, 1080, base_tolerance=0.01)
        # 0.00001 * 1920 = 0.0192, so max(0.01, 0.0192) = 0.0192
        assert math.isclose(tol, 0.0192, abs_tol=1e-5)

    def test_uhd_4k_tolerance(self):
        """4K UHD 3840x2160 comp scales tolerance proportionally."""
        tol = calculate_dynamic_tolerance(3840, 2160, base_tolerance=0.01)
        # 0.00001 * 3840 = 0.0384
        assert math.isclose(tol, 0.0384, abs_tol=1e-5)

    def test_cinema_8k_tolerance(self):
        """8K 7680x4320 comp scales tolerance to ~0.0768px."""
        tol = calculate_dynamic_tolerance(7680, 4320, base_tolerance=0.01)
        assert math.isclose(tol, 0.0768, abs_tol=1e-5)

    def test_extreme_stadium_ribbon_tolerance(self):
        """Massive 21,360x48 stadium ribbon scales tolerance to ~0.2136px,
        preventing false-positive floating-point drift alerts."""
        tol = calculate_dynamic_tolerance(21360, 48, base_tolerance=0.01)
        assert math.isclose(tol, 0.2136, abs_tol=1e-5)

    def test_small_social_banner_retains_baseline(self):
        """Small 300x250 web banner retains base tolerance floor."""
        tol = calculate_dynamic_tolerance(300, 250, base_tolerance=0.01)
        # 0.00001 * 300 = 0.003 < 0.01 floor
        assert tol == 0.01
