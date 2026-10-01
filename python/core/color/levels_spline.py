# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/color/levels_spline.py
FROZEN Spline Module — 3-Point Pro Levels2 closed-form fit (ADBE Pro Levels2).
"""

from __future__ import annotations

import math
from typing import Dict, Union
import numpy as np


def fit_arrays(ref_flat: np.ndarray, grad_flat: np.ndarray) -> Dict[str, Dict[str, float]]:
    """Fits 3-point Pro Levels2 parameters from flat float64 0-1 RGB arrays (Nx3)."""
    channel_names = ("red", "green", "blue")
    channels: Dict[str, Dict[str, float]] = {}

    for i, name in enumerate(channel_names):
        ref_channel = ref_flat[:, i]
        grad_channel = grad_flat[:, i]

        if np.ptp(ref_channel) < 1e-9:
            slope = 1.0
            offset = float(np.mean(grad_channel) - np.mean(ref_channel))
            in_black = 0.0
            in_white = 255.0
            gamma = 1.0
            out_black = float(np.clip(255.0 * offset, 0.0, 255.0))
            out_white = float(np.clip(255.0 * (slope + offset), 0.0, 255.0))
        else:
            fit_slope, fit_offset = np.polyfit(ref_channel, grad_channel, 1)
            slope = float(fit_slope)
            offset = float(fit_offset)

            in_black = 0.0
            in_white = 255.0
            out_black = float(np.clip(255.0 * offset, 0.0, 255.0))
            out_white = float(np.clip(255.0 * (slope + offset), 0.0, 255.0))

            ref_med = float(np.median(ref_channel))
            grad_med = float(np.median(grad_channel))

            if 0.02 < ref_med < 0.98 and 0.02 < grad_med < 0.98:
                try:
                    raw_gamma = math.log(ref_med) / math.log(grad_med)
                    gamma = float(np.clip(raw_gamma, 0.4, 2.5))
                except (ValueError, ZeroDivisionError):
                    gamma = 1.0
            else:
                gamma = 1.0

        channels[name] = {
            "slope": slope,
            "offset": offset,
            "input_black": in_black,
            "input_white": in_white,
            "gamma": gamma,
            "output_black": out_black,
            "output_white": out_white,
        }

    return channels


def fit(ref_input: Union[str, np.ndarray], graded_input: Union[str, np.ndarray]) -> Dict[str, Dict[str, float]]:
    """Fits 3-point Pro Levels2 parameters from either file paths or float64 RGB arrays."""
    if isinstance(ref_input, str) and isinstance(graded_input, str):
        from PIL import Image
        ref_img = Image.open(ref_input).convert("RGB")
        grad_img = Image.open(graded_input).convert("RGB")
        sample_size = (256, 256)
        ref_sample = np.array(ref_img.resize(sample_size, Image.Resampling.BILINEAR), dtype=np.float64) / 255.0
        grad_sample = np.array(grad_img.resize(sample_size, Image.Resampling.BILINEAR), dtype=np.float64) / 255.0
        ref_flat = ref_sample.reshape(-1, 3)
        grad_flat = grad_sample.reshape(-1, 3)
    else:
        ref_arr = np.asarray(ref_input, dtype=np.float64)
        grad_arr = np.asarray(graded_input, dtype=np.float64)
        ref_flat = ref_arr.reshape(-1, 3)
        grad_flat = grad_arr.reshape(-1, 3)

    return fit_arrays(ref_flat, grad_flat)
