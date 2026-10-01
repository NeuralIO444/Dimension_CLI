# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/color/gateway.py
FROZEN GATEWAY MODULE — LogC3/C4, S-Log3, Log3G10, and ACEScg decoders & transfer functions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


# Standard Rec.709 / sRGB Matrix Constants
AP1_TO_REC709_MATRIX = np.array([
    [ 1.70485868, -0.62171448, -0.08329944],
    [-0.13007682,  1.14073577, -0.01058988],
    [-0.02396407, -0.12897545,  1.15286202],
], dtype=np.float64)

REC709_TO_AP1_MATRIX = np.linalg.inv(AP1_TO_REC709_MATRIX)


@dataclass(frozen=True)
class ColorTransformResult:
    source_space: str
    target_space: str
    lut_size: int
    is_clamped: bool
    cube_content: str


class LogSpaceGateway:
    """Color management and 3D LUT normalization gateway for VFX finishing.

    Named distinctly from `core.horizon_color.HorizonColorGateway` (the
    shipped parametric-match/LUT-derivation class) to avoid a same-name
    collision now that both are reachable in production — before this
    rename, `core.color`'s own package `__init__.py` re-exported a
    second, unrelated `HorizonColorGateway`, and any future code doing
    `from core.color import HorizonColorGateway` alongside
    `from core.horizon_color import HorizonColorGateway` would have
    silently shadowed one with the other. This class is presently
    UNCALLED by `planner.plan()` — camera-log/gamut decoding is a
    deliberate out-of-scope carve-out for this wiring pass (`plan()`'s
    `context.transfer == "log"` short-circuit still just returns a
    `GATEWAY_INCOMPLETE` warning); most real client stills are already
    Rec.709/sRGB, not raw camera log. Wiring this in is a real, separate
    follow-up, not a rename-only task.
    """

    @staticmethod
    def linear_to_rec709_oetf(linear_val: float) -> float:
        """Standard BT.709 Opto-Electronic Transfer Function."""
        if linear_val <= 0.0:
            return 0.0
        if linear_val < 0.018:
            return 4.5 * linear_val
        return 1.099 * (linear_val ** 0.45) - 0.099

    @staticmethod
    def arri_logc3_to_linear(log_val: float) -> float:
        """ARRI LogC3 decoding to scene-linear reflection."""
        cut = 0.010591
        a = 5.555556
        b = 0.052272
        c = 0.247190
        d = 0.385537
        e = 5.367655
        f = 0.092809

        if log_val > e * cut + f:
            return (math.pow(10.0, (log_val - d) / c) - b) / a
        return (log_val - f) / e

    @staticmethod
    def sony_slog3_to_linear(log_val: float) -> float:
        """Sony S-Log3 decoding to scene-linear reflection."""
        y = float(np.clip(log_val, 0.0, 1.0))
        cut = 171.210594151052 / 1023.0
        if y >= cut:
            return float(math.pow(10.0, ((y * 1023.0 - 420.0) / 261.5)) * (0.18 + 0.01) - 0.01)
        return float((y * 1023.0 - 95.0) * 0.01125000 / (171.210594151052 - 95.0))

    @staticmethod
    def red_log3g10_to_linear(log_val: float) -> float:
        """RED Log3G10 decoding to scene-linear reflection."""
        y = float(log_val)
        a = 0.224282
        b = 155.975327
        if y >= 0.0:
            return float((math.pow(10.0, (y / a)) - 1.0) / b)
        return float(y * 0.01)

    @classmethod
    def generate_normalization_cube(
        cls,
        source_space: str = "ACEScg",
        target_space: str = "Rec.709",
        size: int = 17,
        title: str = "Horizon_ACEScg_to_Rec709",
    ) -> ColorTransformResult:
        """Generates a 3D .cube LUT string normalizing scene-linear/ACEScg or camera logs to display Rec.709."""
        lines = [
            "# Horizon VFX Normalization Gateway",
            f'TITLE "{title}"',
            f"LUT_3D_SIZE {size}",
            "DOMAIN_MIN 0.0 0.0 0.0",
            "DOMAIN_MAX 1.0 1.0 1.0",
            "",
        ]

        step = 1.0 / (size - 1) if size > 1 else 1.0

        for b_idx in range(size):
            b_in = b_idx * step
            for g_idx in range(size):
                g_in = g_idx * step
                for r_idx in range(size):
                    r_in = r_idx * step

                    if source_space == "ACEScg" and target_space == "Rec.709":
                        in_vec = np.array([r_in, g_in, b_in], dtype=np.float64)
                        out_lin = AP1_TO_REC709_MATRIX @ in_vec
                        r_out = cls.linear_to_rec709_oetf(float(np.clip(out_lin[0], 0.0, 1.0)))
                        g_out = cls.linear_to_rec709_oetf(float(np.clip(out_lin[1], 0.0, 1.0)))
                        b_out = cls.linear_to_rec709_oetf(float(np.clip(out_lin[2], 0.0, 1.0)))
                    elif source_space == "ARRI_LogC3" and target_space == "Rec.709":
                        r_lin = cls.arri_logc3_to_linear(r_in)
                        g_lin = cls.arri_logc3_to_linear(g_in)
                        b_lin = cls.arri_logc3_to_linear(b_in)
                        r_out = cls.linear_to_rec709_oetf(float(np.clip(r_lin, 0.0, 1.0)))
                        g_out = cls.linear_to_rec709_oetf(float(np.clip(g_lin, 0.0, 1.0)))
                        b_out = cls.linear_to_rec709_oetf(float(np.clip(b_lin, 0.0, 1.0)))
                    elif source_space == "Sony_SLog3" and target_space == "Rec.709":
                        r_lin = cls.sony_slog3_to_linear(r_in)
                        g_lin = cls.sony_slog3_to_linear(g_in)
                        b_lin = cls.sony_slog3_to_linear(b_in)
                        r_out = cls.linear_to_rec709_oetf(float(np.clip(r_lin, 0.0, 1.0)))
                        g_out = cls.linear_to_rec709_oetf(float(np.clip(g_lin, 0.0, 1.0)))
                        b_out = cls.linear_to_rec709_oetf(float(np.clip(b_lin, 0.0, 1.0)))
                    elif source_space == "RED_Log3G10" and target_space == "Rec.709":
                        r_lin = cls.red_log3g10_to_linear(r_in)
                        g_lin = cls.red_log3g10_to_linear(g_in)
                        b_lin = cls.red_log3g10_to_linear(b_in)
                        r_out = cls.linear_to_rec709_oetf(float(np.clip(r_lin, 0.0, 1.0)))
                        g_out = cls.linear_to_rec709_oetf(float(np.clip(g_lin, 0.0, 1.0)))
                        b_out = cls.linear_to_rec709_oetf(float(np.clip(b_lin, 0.0, 1.0)))
                    else:
                        r_out, g_out, b_out = r_in, g_in, b_in

                    lines.append(f"{r_out:.6f} {g_out:.6f} {b_out:.6f}")

        cube_text = "\n".join(lines) + "\n"
        return ColorTransformResult(
            source_space=source_space,
            target_space=target_space,
            lut_size=size,
            is_clamped=True,
            cube_content=cube_text,
        )
