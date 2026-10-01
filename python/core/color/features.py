# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/color/features.py
Feature extraction and LookReport calculation module.
"""

from __future__ import annotations

from typing import Tuple, List, Union
import numpy as np

from core.color.recipe import LookReport


def rgb_to_xyz(rgb: np.ndarray) -> np.ndarray:
    """Converts linear Rec.709 RGB (0..1 float) to XYZ D65."""
    M = np.array([
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041]
    ], dtype=np.float64)
    return rgb @ M.T


def xyz_to_cielab(xyz: np.ndarray) -> np.ndarray:
    """Converts XYZ D65 to CIELAB (L*, a*, b*)."""
    # D65 reference white
    Xn, Yn, Zn = 0.95047, 1.00000, 1.08883
    xyz_ref = np.array([Xn, Yn, Zn], dtype=np.float64)
    scaled = xyz / xyz_ref

    delta = 6.0 / 29.0
    factor = 1.0 / (3.0 * delta * delta)
    offset = 4.0 / 29.0

    # f(t) = t^(1/3) if t > delta^3 else factor * t + offset
    mask = scaled > (delta ** 3)
    f = np.where(mask, np.cbrt(np.maximum(scaled, 1e-12)), factor * scaled + offset)

    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])

    return np.stack([L, a, b], axis=-1)


def rgb_to_cielab(rgb: np.ndarray) -> np.ndarray:
    """Converts float RGB (HxWx3, 0..1) to CIELAB."""
    xyz = rgb_to_xyz(rgb)
    return xyz_to_cielab(xyz)


def estimate_cct_tint(rgb: np.ndarray) -> Tuple[float, float]:
    """Estimates CCT (Kelvin) and Tint from average RGB via McCamy approximation."""
    mean_rgb = np.mean(rgb.reshape(-1, 3), axis=0)
    mean_rgb = np.clip(mean_rgb, 1e-6, None)
    xyz = rgb_to_xyz(mean_rgb)
    total = np.sum(xyz)
    if total < 1e-9:
        return 6500.0, 0.0
    x = xyz[0] / total
    y = xyz[1] / total

    # McCamy formula
    n = (x - 0.3320) / (0.1858 - y + 1e-9)
    cct = 449.0 * (n ** 3) + 3525.0 * (n ** 2) + 6823.3 * n + 5520.33
    cct = float(np.clip(cct, 1000.0, 25000.0))

    # Tint estimation relative to D65 y-coordinate
    y_expected = -3.000 * (x ** 2) + 2.870 * x - 0.275
    tint = float(np.clip((y - y_expected) * 100.0, -10.0, 10.0))

    return cct, tint


def downsample_image(img: np.ndarray, max_edge: int = 512) -> np.ndarray:
    """Area-average downsampling to ensure max edge <= max_edge deterministically."""
    h, w = img.shape[:2]
    if max(h, w) <= max_edge:
        return img.astype(np.float64)

    scale = max_edge / float(max(h, w))
    new_h = max(1, int(round(h * scale)))
    new_w = max(1, int(round(w * scale)))

    # Use Pillow for bilinear/box area-average resampling
    from PIL import Image
    # Normalize img to uint8 for Pillow or perform block mean
    clipped = np.clip(img * 255.0, 0, 255).astype(np.uint8)
    pil_img = Image.fromarray(clipped, mode="RGB")
    resized = pil_img.resize((new_w, new_h), Image.Resampling.BILINEAR)
    return np.array(resized, dtype=np.float64) / 255.0


def extract_report(
    img_input: Union[str, np.ndarray],
    luma_space: str = "rec709"
) -> LookReport:
    """Extracts a LookReport from an image (array or file path)."""
    if isinstance(img_input, str):
        from PIL import Image
        pil_img = Image.open(img_input).convert("RGB")
        img = np.array(pil_img, dtype=np.float64) / 255.0
    else:
        img = np.asarray(img_input, dtype=np.float64)

    ds_img = downsample_image(img, max_edge=512)
    h, w = ds_img.shape[:2]
    flat_rgb = ds_img.reshape(-1, 3)
    n_pixels = flat_rgb.shape[0]

    # Calculate Luma
    if luma_space == "ap1":
        luma = 0.2722287 * flat_rgb[:, 0] + 0.6740818 * flat_rgb[:, 1] + 0.0536895 * flat_rgb[:, 2]
    else:
        luma = 0.2126 * flat_rgb[:, 0] + 0.7152 * flat_rgb[:, 1] + 0.0722 * flat_rgb[:, 2]

    # Percentiles
    p1, p5, p50, p95, p99 = np.percentile(luma, [1, 5, 50, 95, 99])
    luma_p = {
        1: float(p1),
        5: float(p5),
        50: float(p50),
        95: float(p95),
        99: float(p99),
    }

    # CIELAB stats
    lab = rgb_to_cielab(flat_rgb)
    lab_mu = tuple(float(x) for x in np.mean(lab, axis=0))
    lab_sigma = tuple(float(x) for x in np.std(lab, axis=0))

    # Hasler & Süsstrunk Colorfulness
    R = flat_rgb[:, 0]
    G = flat_rgb[:, 1]
    B = flat_rgb[:, 2]
    rg = R - G
    yb = 0.5 * (R + G) - B
    std_rg, std_yb = np.std(rg), np.std(yb)
    mean_rg, mean_yb = np.mean(rg), np.mean(yb)
    colorfulness = float(np.sqrt(std_rg ** 2 + std_yb ** 2) + 0.3 * np.sqrt(mean_rg ** 2 + mean_yb ** 2))

    # CCT & Tint
    cct, tint = estimate_cct_tint(flat_rgb)

    # Zone RGB (shadows, mids, highlights)
    p25, p75 = np.percentile(luma, [25, 75])
    shadow_mask = luma <= p25
    mids_mask = (luma > p25) & (luma < p75)
    high_mask = luma >= p75

    shadows_rgb = tuple(float(x) for x in np.mean(flat_rgb[shadow_mask], axis=0)) if np.any(shadow_mask) else (0.0, 0.0, 0.0)
    mids_rgb = tuple(float(x) for x in np.mean(flat_rgb[mids_mask], axis=0)) if np.any(mids_mask) else (0.5, 0.5, 0.5)
    highlights_rgb = tuple(float(x) for x in np.mean(flat_rgb[high_mask], axis=0)) if np.any(high_mask) else (1.0, 1.0, 1.0)

    zone_rgb = {
        "shadows": shadows_rgb,
        "mids": mids_rgb,
        "highlights": highlights_rgb,
    }

    # 36-bin Saturation-Weighted Hue Histogram
    max_c = np.maximum(R, np.maximum(G, B))
    min_c = np.minimum(R, np.minimum(G, B))
    delta_c = max_c - min_c
    sat = np.where(max_c > 1e-6, delta_c / (max_c + 1e-6), 0.0)

    # Hue in degrees 0..360
    hue = np.zeros_like(max_c)
    mask_r = (max_c == R) & (delta_c > 1e-6)
    mask_g = (max_c == G) & (delta_c > 1e-6)
    mask_b = (max_c == B) & (delta_c > 1e-6)

    hue[mask_r] = ((G[mask_r] - B[mask_r]) / delta_c[mask_r]) % 6.0
    hue[mask_g] = ((B[mask_g] - R[mask_g]) / delta_c[mask_g]) + 2.0
    hue[mask_b] = ((R[mask_b] - G[mask_b]) / delta_c[mask_b]) + 4.0
    hue = (hue * 60.0) % 360.0

    hue_bins = np.floor(hue / 10.0).astype(int) % 36
    hue_hist_raw = np.zeros(36, dtype=np.float64)
    np.add.at(hue_hist_raw, hue_bins, sat)

    hist_sum = float(np.sum(hue_hist_raw))
    if hist_sum > 1e-9:
        hue_hist = (hue_hist_raw / hist_sum).tolist()
    else:
        hue_hist = [0.0] * 36

    # Image Signatures
    signatures: List[str] = []
    if luma_p[99] > 0.95 and luma_p[1] < 0.05:
        signatures.append("full_dynamic_range")
    if colorfulness > 0.35:
        signatures.append("high_saturation")
    elif colorfulness < 0.08:
        signatures.append("monochromatic")

    return LookReport(
        luma_p=luma_p,
        lab_mu=lab_mu,
        lab_sigma=lab_sigma,
        colorfulness=colorfulness,
        cct=cct,
        tint=tint,
        zone_rgb=zone_rgb,
        hue_hist=hue_hist,
        signatures=signatures,
        n_pixels=n_pixels,
        luma_space=luma_space,
        downsample_shape=(h, w),
    )
