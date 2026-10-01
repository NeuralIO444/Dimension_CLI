# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/horizon_color.py
TASK-CM-TOOLKIT-01 (Issue #242) — Horizon: ACEScg / Log 3D VFX Normalization Gateway.

Provides mathematical color transformations and 3D LUT generation between
scene-linear (ACEScg / Linear Rec.709) and camera log encodings (ARRI LogC3/LogC4,
RED Log3G10, Sony S-Log3) and display encodings (Rec.709 / sRGB).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

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


@dataclass(frozen=True)
class ParametricMatchResult:
    """Per-channel (slope, offset) linear fit — the "no LUT" quick-match
    path's output. Unlike ColorTransformResult, this never becomes a
    .cube/.3dl file: cep/jsx/host.jsx's injectParametricColorMatch()
    applies red/green/blue directly onto AE's native Levels effect."""

    ref_path: str
    graded_path: str
    red: dict
    green: dict
    blue: dict


class HorizonColorGateway:
    """Color management and 3D LUT normalization gateway for Horizon VFX finishing."""

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
        # ARRI LogC3 constants for EI 800
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
            f"TITLE \"{title}\"",
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

    @classmethod
    def derive_3d_lut_from_images(
        cls,
        ref_path: str,
        graded_path: str,
        output_cube_path: Optional[str] = None,
        size: int = 33,
        title: str = "Dimension_Derived_Color_Grade",
    ) -> ColorTransformResult:
        """Derives a 3D .cube LUT mapping colors from a reference frame to a colorist graded still."""
        import os
        from PIL import Image

        ref_img = Image.open(ref_path).convert("RGB")
        grad_img = Image.open(graded_path).convert("RGB")

        # Resize both to uniform dimensions for point-to-point correspondence
        sample_size = (256, 256)
        ref_sample = np.array(ref_img.resize(sample_size, Image.Resampling.BILINEAR), dtype=np.float64) / 255.0
        grad_sample = np.array(grad_img.resize(sample_size, Image.Resampling.BILINEAR), dtype=np.float64) / 255.0

        ref_flat = ref_sample.reshape(-1, 3)
        grad_flat = grad_sample.reshape(-1, 3)

        # Estimate affine color transformation matrix: ref_flat @ M = grad_flat
        ones = np.ones((ref_flat.shape[0], 1), dtype=np.float64)
        A = np.hstack([ref_flat, ones])
        M_affine, _, _, _ = np.linalg.lstsq(A, grad_flat, rcond=None)

        lines = [
            "# Dimension Color Grade Synthesizer",
            f"# Derived from: {os.path.basename(ref_path)} -> {os.path.basename(graded_path)}",
            f"TITLE \"{title}\"",
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

                    in_vec = np.array([r_in, g_in, b_in, 1.0], dtype=np.float64)
                    out_vec = in_vec @ M_affine
                    r_out = float(np.clip(out_vec[0], 0.0, 1.0))
                    g_out = float(np.clip(out_vec[1], 0.0, 1.0))
                    b_out = float(np.clip(out_vec[2], 0.0, 1.0))

                    lines.append(f"{r_out:.6f} {g_out:.6f} {b_out:.6f}")

        cube_text = "\n".join(lines) + "\n"
        if output_cube_path:
            os.makedirs(os.path.dirname(os.path.abspath(output_cube_path)), exist_ok=True)
            with open(output_cube_path, "w", encoding="utf-8") as f:
                f.write(cube_text)

        return ColorTransformResult(
            source_space=ref_path,
            target_space=graded_path,
            lut_size=size,
            is_clamped=True,
            cube_content=cube_text,
        )

    @classmethod
    def derive_parametric_match_from_images(
        cls,
        ref_path: str,
        graded_path: str,
    ) -> ParametricMatchResult:
        """Fits an independent per-channel linear (slope, offset) match:
        graded_channel ~= slope * ref_channel + offset, sampled and
        normalized the same way derive_3d_lut_from_images is.

        Unlike derive_3d_lut_from_images, this never writes (or needs)
        a LUT file: injectParametricColorMatch() in cep/jsx/host.jsx
        applies the (slope, offset) pairs directly onto AE's native
        Levels (Individual Controls) effect's per-channel Output
        Black/White sliders. This is a forward fit from two stills you
        provide, not a reverse-engineer of an arbitrary LUT/grade back
        into parametric knobs — see the "CM5 parametric refit" note in
        docs/roadmap/color-match-workflow-2026-08-27-scope-doc.md for
        why that different (harder, deferred) problem is out of scope
        here.

        Each channel is fit independently — one straight line per
        channel, not the 3x3 affine matrix derive_3d_lut_from_images
        solves — because the target representation (Levels' per-channel
        Output Black/White) has no cross-channel term to receive an
        off-diagonal coefficient even if a joint fit found one.
        """
        from PIL import Image

        ref_img = Image.open(ref_path).convert("RGB")
        grad_img = Image.open(graded_path).convert("RGB")

        sample_size = (256, 256)
        ref_sample = np.array(ref_img.resize(sample_size, Image.Resampling.BILINEAR), dtype=np.float64) / 255.0
        grad_sample = np.array(grad_img.resize(sample_size, Image.Resampling.BILINEAR), dtype=np.float64) / 255.0

        ref_flat = ref_sample.reshape(-1, 3)
        grad_flat = grad_sample.reshape(-1, 3)

        channel_names = ("red", "green", "blue")
        channels = {}
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
                
                # Input/Output black and white mapping
                in_black = 0.0
                in_white = 255.0
                out_black = float(np.clip(255.0 * offset, 0.0, 255.0))
                out_white = float(np.clip(255.0 * (slope + offset), 0.0, 255.0))

                # Midtone Gamma curve fit (ratio of log-medians)
                ref_med = float(np.median(ref_channel))
                grad_med = float(np.median(grad_channel))
                if 0.02 < ref_med < 0.98 and 0.02 < grad_med < 0.98:
                    # In AE Levels, Gamma > 1.0 brightens midtones (output = input^(1/gamma))
                    # log(grad_med) = (1/gamma) * log(ref_med) => gamma = log(ref_med) / log(grad_med)
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

        return ParametricMatchResult(
            ref_path=ref_path,
            graded_path=graded_path,
            red=channels["red"],
            green=channels["green"],
            blue=channels["blue"],
        )

    @staticmethod
    def convert_image_to_png(input_path: str, output_png_path: str) -> str:
        """Converts any image (TIFF, TGA, EXR, JPG) to a standard PNG web preview."""
        import os
        from PIL import Image

        img = Image.open(input_path)
        os.makedirs(os.path.dirname(os.path.abspath(output_png_path)), exist_ok=True)
        img.save(output_png_path, format="PNG")
        return output_png_path
