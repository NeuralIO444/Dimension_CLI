# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/color/residual.py
Residual metric calculation module — planner_metric_v1 locked formula.
"""

from __future__ import annotations

from typing import List, Union
import numpy as np

from core.color.recipe import Residual
from core.color.features import rgb_to_cielab, extract_report, downsample_image


def compute_cie76_delta_e(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """Computes CIE76 Delta E between two Lab arrays."""
    diff = lab1 - lab2
    return np.sqrt(np.sum(diff ** 2, axis=-1))


def compute_cie2000_delta_e(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """Computes CIE2000 Delta E (audit metric)."""
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]

    C1 = np.sqrt(a1 ** 2 + b1 ** 2)
    C2 = np.sqrt(a2 ** 2 + b2 ** 2)
    C_bar = 0.5 * (C1 + C2)

    G = 0.5 * (1.0 - np.sqrt((C_bar ** 7) / (C_bar ** 7 + 25.0 ** 7 + 1e-12)))
    a1_p = (1.0 + G) * a1
    a2_p = (1.0 + G) * a2

    C1_p = np.sqrt(a1_p ** 2 + b1 ** 2)
    C2_p = np.sqrt(a2_p ** 2 + b2 ** 2)

    h1_p = np.degrees(np.arctan2(b1, a1_p)) % 360.0
    h2_p = np.degrees(np.arctan2(b2, a2_p)) % 360.0

    dL_p = L2 - L1
    dC_p = C2_p - C1_p

    dh_p = h2_p - h1_p
    dh_p = np.where(dh_p > 180.0, dh_p - 360.0, dh_p)
    dh_p = np.where(dh_p < -180.0, dh_p + 360.0, dh_p)

    dH_p = 2.0 * np.sqrt(np.maximum(C1_p * C2_p, 0.0)) * np.sin(np.radians(0.5 * dh_p))

    L_bar_p = 0.5 * (L1 + L2)
    C_bar_p = 0.5 * (C1_p + C2_p)

    h_bar_diff = np.abs(h1_p - h2_p)
    h_bar_p = np.where(
        h_bar_diff > 180.0,
        (h1_p + h2_p + 360.0) * 0.5,
        (h1_p + h2_p) * 0.5
    )

    T = (1.0
         - 0.17 * np.cos(np.radians(h_bar_p - 30.0))
         + 0.24 * np.cos(np.radians(2.0 * h_bar_p))
         + 0.32 * np.cos(np.radians(3.0 * h_bar_p + 6.0))
         - 0.20 * np.cos(np.radians(4.0 * h_bar_p - 63.0)))

    SL = 1.0 + (0.015 * ((L_bar_p - 50.0) ** 2)) / np.sqrt(20.0 + (L_bar_p - 50.0) ** 2 + 1e-12)
    SC = 1.0 + 0.045 * C_bar_p
    SH = 1.0 + 0.015 * C_bar_p * T

    dTheta = 30.0 * np.exp(-(((h_bar_p - 275.0) / 25.0) ** 2))
    RC = 2.0 * np.sqrt((C_bar_p ** 7) / (C_bar_p ** 7 + 25.0 ** 7 + 1e-12))
    RT = -np.sin(np.radians(2.0 * dTheta)) * RC

    dE2000 = np.sqrt(
        (dL_p / SL) ** 2 +
        (dC_p / SC) ** 2 +
        (dH_p / SH) ** 2 +
        RT * (dC_p / SC) * (dH_p / SH)
    )
    return dE2000


def calculate_wasserstein_1d(h1: np.ndarray, h2: np.ndarray) -> float:
    """Computes 1D Wasserstein-1 distance between two 256-bin luma histograms."""
    p1 = h1.astype(np.float64) / (np.sum(h1) + 1e-12)
    p2 = h2.astype(np.float64) / (np.sum(h2) + 1e-12)
    cdf1 = np.cumsum(p1)
    cdf2 = np.cumsum(p2)
    return float(np.mean(np.abs(cdf1 - cdf2)))


def calculate_circular_emd(hist1: List[float], hist2: List[float]) -> float:
    """Computes 1D Circular Earth Mover's Distance between two 36-bin hue histograms."""
    h1 = np.array(hist1, dtype=np.float64)
    h2 = np.array(hist2, dtype=np.float64)
    s1, s2 = np.sum(h1), np.sum(h2)
    if s1 > 1e-12:
        h1 /= s1
    if s2 > 1e-12:
        h2 /= s2

    diff = h1 - h2
    n = len(diff)
    min_work = float("inf")

    # Shift circle to find minimal work
    for shift in range(n):
        shifted_diff = np.roll(diff, -shift)
        work = np.sum(np.abs(np.cumsum(shifted_diff)))
        if work < min_work:
            min_work = work

    return float(min_work / float(n))


def calculate_residual(
    img_sim: Union[str, np.ndarray],
    img_ref: Union[str, np.ndarray]
) -> Residual:
    """Calculates locked planner_metric_v1 residual metrics between simulated image and reference."""
    if isinstance(img_sim, str):
        from PIL import Image
        img_sim_arr = np.array(Image.open(img_sim).convert("RGB"), dtype=np.float64) / 255.0
    else:
        img_sim_arr = np.asarray(img_sim, dtype=np.float64)

    if isinstance(img_ref, str):
        from PIL import Image
        img_ref_arr = np.array(Image.open(img_ref).convert("RGB"), dtype=np.float64) / 255.0
    else:
        img_ref_arr = np.asarray(img_ref, dtype=np.float64)

    ds_sim = downsample_image(img_sim_arr, max_edge=512)
    ds_ref = downsample_image(img_ref_arr, max_edge=512)

    # 1. CIE76 on 4096 stratified samples with RNG seed 1337
    flat_sim = ds_sim.reshape(-1, 3)
    flat_ref = ds_ref.reshape(-1, 3)
    n = min(flat_sim.shape[0], flat_ref.shape[0])

    rng = np.random.RandomState(1337)
    sample_size = min(4096, n)
    indices = rng.choice(n, size=sample_size, replace=False)

    lab_sim_samples = rgb_to_cielab(flat_sim[indices])
    lab_ref_samples = rgb_to_cielab(flat_ref[indices])

    dE76_samples = compute_cie76_delta_e(lab_sim_samples, lab_ref_samples)
    dE_mean = float(np.mean(dE76_samples))
    dE_p95 = float(np.percentile(dE76_samples, 95))

    # Audit CIE2000
    dE2000_samples = compute_cie2000_delta_e(lab_sim_samples, lab_ref_samples)
    dE2000_mean = float(np.mean(dE2000_samples))

    # 2. Wasserstein-1 on 256-bin luma histograms
    luma_sim = 0.2126 * ds_sim[..., 0] + 0.7152 * ds_sim[..., 1] + 0.0722 * ds_sim[..., 2]
    luma_ref = 0.2126 * ds_ref[..., 0] + 0.7152 * ds_ref[..., 1] + 0.0722 * ds_ref[..., 2]

    h_sim, _ = np.histogram(np.clip(luma_sim, 0.0, 1.0), bins=256, range=(0.0, 1.0))
    h_ref, _ = np.histogram(np.clip(luma_ref, 0.0, 1.0), bins=256, range=(0.0, 1.0))
    w1_luma = calculate_wasserstein_1d(h_sim, h_ref)

    # 3. Circular EMD on 36-bin sat-weighted hue histograms
    report_sim = extract_report(ds_sim)
    report_ref = extract_report(ds_ref)
    hue_emd = calculate_circular_emd(report_sim.hue_hist, report_ref.hue_hist)

    # 4. Lab mean and std L2 distances
    lab_mu_sim = np.array(report_sim.lab_mu, dtype=np.float64)
    lab_mu_ref = np.array(report_ref.lab_mu, dtype=np.float64)
    lab_mu_l2 = float(np.linalg.norm(lab_mu_sim - lab_mu_ref))

    lab_sigma_sim = np.array(report_sim.lab_sigma, dtype=np.float64)
    lab_sigma_ref = np.array(report_ref.lab_sigma, dtype=np.float64)
    lab_sigma_l2 = float(np.linalg.norm(lab_sigma_sim - lab_sigma_ref))

    # Locked formula planner_metric_v1:
    # R = (dE_mean/10) + 0.5*W1_luma + 0.25*hue_emd + 0.15*(lab_mu_l2/10)
    R = (dE_mean / 10.0) + (0.5 * w1_luma) + (0.25 * hue_emd) + (0.15 * (lab_mu_l2 / 10.0))

    return Residual(
        metric_id="planner_metric_v1",
        R=float(R),
        dE_mean=dE_mean,
        dE_p95=dE_p95,
        dE2000_mean=dE2000_mean,
        W1_luma=w1_luma,
        hue_emd=hue_emd,
        lab_mu_l2=lab_mu_l2,
        lab_sigma_l2=lab_sigma_l2,
    )
