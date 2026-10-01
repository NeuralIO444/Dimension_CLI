# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/color/simulate.py
Simulation module for native AE effect node evaluation (Pro Levels2, Photo Filter stub).
"""

from __future__ import annotations

from typing import Dict, Any
import numpy as np


def simulate_pro_levels2(img: np.ndarray, levels_params: Dict[str, Dict[str, float]]) -> np.ndarray:
    """Simulates ADBE Pro Levels2 effect evaluation in 32-bpc float64 (0..1+ HDR).

    Wraps the existing spline apply/domain logic:
    For each channel:
      1. Normalize input relative to (input_black, input_white).
      2. Apply gamma curvature (x^(1/gamma)).
      3. Scale to (output_black, output_white).
    """
    out = np.empty_like(img, dtype=np.float64)
    channel_keys = ("red", "green", "blue")

    for i, ch_name in enumerate(channel_keys):
        params = levels_params.get(ch_name, {})
        in_black = params.get("input_black", 0.0) / 255.0
        in_white = params.get("input_white", 255.0) / 255.0
        gamma = params.get("gamma", 1.0)
        out_black = params.get("output_black", 0.0) / 255.0
        out_white = params.get("output_white", 255.0) / 255.0

        ch_data = img[..., i]
        denom = max(in_white - in_black, 1e-9)
        norm = np.clip((ch_data - in_black) / denom, 0.0, 1.0)

        if abs(gamma - 1.0) > 1e-6 and gamma > 0.0:
            gamma_applied = norm ** (1.0 / gamma)
        else:
            gamma_applied = norm

        out[..., i] = gamma_applied * (out_white - out_black) + out_black

    return out


def simulate_photo_filter(*args: Any, **kwargs: Any) -> np.ndarray:
    """Stub for Photo Filter simulation — Phase 1 does NOT implement Photo Filter."""
    raise NotImplementedError("Phase 1: Photo Filter simulation not enabled")
