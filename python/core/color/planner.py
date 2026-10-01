# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/color/planner.py
Horizon Color Planner observe & decide engine (Phase 1 Pure Python).
"""

from __future__ import annotations

from typing import List, Union, Optional, Dict
import numpy as np

from core.color.recipe import (
    ColorContext,
    LookReport,
    Backend,
    ColorRecipe,
)
from core.color.features import extract_report, downsample_image
from core.color import levels_spline
from core.color import lut_mesh
from core.color import simulate
from core.color import residual


# CONSTANTS BLOCK — Locked Thresholds & Weights
T_QUICK_R = 0.45
T_QUICK_DE = 4.0
T_ELIDE_R_DIFF = 0.02
T_ELIDE_DE_DIFF = 0.3
HIST_3D_SHOT_MATCH_THRESHOLD = 0.55


def calculate_3d_hist_intersection(img1: np.ndarray, img2: np.ndarray) -> float:
    """Calculates 8x8x8 3D RGB histogram intersection ratio."""
    ds1 = downsample_image(img1, max_edge=256)
    ds2 = downsample_image(img2, max_edge=256)

    flat1 = np.clip(ds1.reshape(-1, 3), 0.0, 0.9999)
    flat2 = np.clip(ds2.reshape(-1, 3), 0.0, 0.9999)

    bins1 = (flat1 * 8).astype(int)
    bins2 = (flat2 * 8).astype(int)

    idx1 = bins1[:, 0] * 64 + bins1[:, 1] * 8 + bins1[:, 2]
    idx2 = bins2[:, 0] * 64 + bins2[:, 1] * 8 + bins2[:, 2]

    h1, _ = np.histogram(idx1, bins=512, range=(0, 512))
    h2, _ = np.histogram(idx2, bins=512, range=(0, 512))

    p1 = h1.astype(np.float64) / (np.sum(h1) + 1e-12)
    p2 = h2.astype(np.float64) / (np.sum(h2) + 1e-12)

    return float(np.sum(np.minimum(p1, p2)))


def tag_pair(src_report: LookReport, ref_report: LookReport) -> List[str]:
    """Identifies color pair signatures as hints for audit/telemetry."""
    sigs: List[str] = []

    if ref_report.luma_p[1] > src_report.luma_p[1] + 0.05:
        sigs.append("lifted_black")
    elif ref_report.luma_p[1] < src_report.luma_p[1] - 0.05:
        sigs.append("crushed_black")

    if ref_report.luma_p[99] < src_report.luma_p[99] - 0.05:
        sigs.append("soft_highlight")

    if abs(ref_report.cct - src_report.cct) > 1000.0 or abs(ref_report.tint - src_report.tint) > 0.05:
        sigs.append("global_cast")

    # Split tone detection: shadow vs highlight a* b* movement
    sh_src_a, sh_src_b = src_report.zone_rgb["shadows"][0] - src_report.zone_rgb["shadows"][1], src_report.zone_rgb["shadows"][2]
    sh_ref_a, sh_ref_b = ref_report.zone_rgb["shadows"][0] - ref_report.zone_rgb["shadows"][1], ref_report.zone_rgb["shadows"][2]

    hi_src_a, hi_src_b = src_report.zone_rgb["highlights"][0] - src_report.zone_rgb["highlights"][1], src_report.zone_rgb["highlights"][2]
    hi_ref_a, hi_ref_b = ref_report.zone_rgb["highlights"][0] - ref_report.zone_rgb["highlights"][1], ref_report.zone_rgb["highlights"][2]

    sh_delta = (sh_ref_a - sh_src_a, sh_ref_b - sh_src_b)
    hi_delta = (hi_ref_a - hi_src_a, hi_ref_b - hi_src_b)

    if (sh_delta[0] * hi_delta[0] < -0.01) or (sh_delta[1] * hi_delta[1] < -0.01):
        sigs.append("split_tone")

    if abs(ref_report.colorfulness - src_report.colorfulness) > 0.15:
        sigs.append("sat_gap")

    return sigs


def plan(
    src_input: Union[str, np.ndarray],
    ref_input: Union[str, np.ndarray],
    context: Optional[ColorContext] = None,
    comp_id: str = "comp",
) -> ColorRecipe:
    """Phase 1 Pure Python Color Planner — observe & decide backend choice."""
    if context is None:
        context = ColorContext()

    default_levels: Dict[str, Dict[str, float]] = {
        "red": {"slope": 1.0, "offset": 0.0, "input_black": 0.0, "input_white": 255.0, "gamma": 1.0, "output_black": 0.0, "output_white": 255.0},
        "green": {"slope": 1.0, "offset": 0.0, "input_black": 0.0, "input_white": 255.0, "gamma": 1.0, "output_black": 0.0, "output_white": 255.0},
        "blue": {"slope": 1.0, "offset": 0.0, "input_black": 0.0, "input_white": 255.0, "gamma": 1.0, "output_black": 0.0, "output_white": 255.0},
    }

    # SHORT CIRCUITS
    if context.engine == "ocio":
        return ColorRecipe(
            backend=Backend.LEVELS,
            mode="look_transfer",
            levels=default_levels,
            lut_path=None,
            switcher_slot=2,
            extra=[],
            warnings=["OCIO_BLOCKED"],
            context=context,
        )

    if context.transfer == "log":
        return ColorRecipe(
            backend=Backend.LEVELS,
            mode="look_transfer",
            levels=default_levels,
            lut_path=None,
            switcher_slot=2,
            extra=[],
            warnings=["GATEWAY_INCOMPLETE"],
            context=context,
        )

    # Convert inputs to float64 numpy arrays
    if isinstance(src_input, str):
        from PIL import Image
        src_img = np.array(Image.open(src_input).convert("RGB"), dtype=np.float64) / 255.0
    else:
        src_img = np.asarray(src_input, dtype=np.float64)

    if isinstance(ref_input, str):
        from PIL import Image
        ref_img = np.array(Image.open(ref_input).convert("RGB"), dtype=np.float64) / 255.0
    else:
        ref_img = np.asarray(ref_input, dtype=np.float64)

    # Extract LookReports
    luma_space = "ap1" if context.working_space == "ACEScg" else "rec709"
    report_src = extract_report(src_img, luma_space=luma_space)
    report_ref = extract_report(ref_img, luma_space=luma_space)

    # Classify Mode
    intersection = calculate_3d_hist_intersection(src_img, ref_img)
    mode = "shot_match" if intersection > HIST_3D_SHOT_MATCH_THRESHOLD else "look_transfer"

    signatures = tag_pair(report_src, report_ref)

    # Fit Levels Spline
    levels_dict = levels_spline.fit(src_img, ref_img)

    # Simulate Levels
    img_sim_levels = simulate.simulate_pro_levels2(src_img, levels_dict)

    # Compute Residual
    res_levels = residual.calculate_residual(img_sim_levels, ref_img)

    # Check Quick Pass
    pass_quick = (res_levels.R <= T_QUICK_R) and (res_levels.dE_mean <= T_QUICK_DE)

    if pass_quick:
        return ColorRecipe(
            backend=Backend.LEVELS,
            mode=mode,
            levels=levels_dict,
            lut_path=None,
            switcher_slot=2,
            extra=[],
            metrics={
                "R": res_levels.R,
                "dE_mean": res_levels.dE_mean,
                "dE_p95": res_levels.dE_p95,
                "W1_luma": res_levels.W1_luma,
                "hue_emd": res_levels.hue_emd,
                "hist_intersection": intersection,
            },
            warnings=[],
            signatures=signatures,
            context=context,
            report_src=report_src,
            report_ref=report_ref,
            residual=res_levels,
        )

    # If Quick fails, fit in-memory affine mesh (NO write)
    M_affine = lut_mesh.fit(src_img, ref_img)
    img_sim_lut = lut_mesh.apply_affine_matrix(src_img, M_affine)
    res_lut = residual.calculate_residual(img_sim_lut, ref_img)

    # Check Elide
    elide = (res_levels.R - res_lut.R <= T_ELIDE_R_DIFF) and (res_levels.dE_mean - res_lut.dE_mean <= T_ELIDE_DE_DIFF)

    if elide:
        return ColorRecipe(
            backend=Backend.LUT_ELIDE,
            mode=mode,
            levels=levels_dict,
            lut_path=None,
            switcher_slot=2,
            extra=[],
            metrics={
                "R": res_levels.R,
                "dE_mean": res_levels.dE_mean,
                "R_lut": res_lut.R,
                "dE_mean_lut": res_lut.dE_mean,
                "hist_intersection": intersection,
                "lut_elided": True,
            },
            warnings=[],
            signatures=signatures,
            context=context,
            report_src=report_src,
            report_ref=report_ref,
            residual=res_levels,
        )

    # Fallback to LUT Mesh (Template String Path, NO file write)
    template_lut_path = f".dimension/color_match_{comp_id}.cube"
    return ColorRecipe(
        backend=Backend.LUT_MESH,
        mode=mode,
        levels=levels_dict,
        lut_path=template_lut_path,
        switcher_slot=3,
        # The fitted 3x4 affine matrix, plain-list-serialized so a caller
        # outside this package (which must never itself do any file I/O
        # per test_12_ast_import_and_file_write_guard) can pass it to
        # lut_mesh.generate_cube_text() and write the actual .cube file
        # to `lut_path` above. Not a new schema field -- `extra` already
        # existed for exactly this "attach extra data" purpose.
        extra=[{"lut_affine_matrix": M_affine.tolist()}],
        metrics={
            "R": res_lut.R,
            "dE_mean": res_lut.dE_mean,
            "dE_p95": res_lut.dE_p95,
            "R_levels": res_levels.R,
            "dE_mean_levels": res_levels.dE_mean,
            "hist_intersection": intersection,
        },
        warnings=[],
        signatures=signatures,
        context=context,
        report_src=report_src,
        report_ref=report_ref,
        residual=res_lut,
    )
