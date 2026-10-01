# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/color/lut_mesh.py
FROZEN LUT Mesh Module — 3x3 Affine color matrix fit & 17^3 / 33^3 .cube synthesis.
"""

from __future__ import annotations

from typing import Union
import numpy as np


def fit_affine_matrix(ref_flat: np.ndarray, grad_flat: np.ndarray) -> np.ndarray:
    """Estimates affine 3x4 color transformation matrix from float64 RGB flat arrays (Nx3)."""
    ones = np.ones((ref_flat.shape[0], 1), dtype=np.float64)
    A = np.hstack([ref_flat, ones])
    M_affine, _, _, _ = np.linalg.lstsq(A, grad_flat, rcond=None)
    return M_affine


def apply_affine_matrix(img: np.ndarray, M_affine: np.ndarray) -> np.ndarray:
    """Applies 3x4 affine color transform matrix to an RGB image array (HxWx3)."""
    shape = img.shape
    flat = img.reshape(-1, 3)
    ones = np.ones((flat.shape[0], 1), dtype=np.float64)
    A = np.hstack([flat, ones])
    out_flat = A @ M_affine
    out_flat = np.clip(out_flat, 0.0, None)  # Preserve HDR or clip lower bound at 0
    return out_flat.reshape(shape)


def generate_cube_text(M_affine: np.ndarray, size: int = 33, title: str = "Dimension_Derived_Color_Grade") -> str:
    """Generates 3D .cube file content string in memory without writing to disk."""
    lines = [
        "# Dimension Color Grade Synthesizer",
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

                in_vec = np.array([r_in, g_in, b_in, 1.0], dtype=np.float64)
                out_vec = in_vec @ M_affine
                r_out = float(np.clip(out_vec[0], 0.0, 1.0))
                g_out = float(np.clip(out_vec[1], 0.0, 1.0))
                b_out = float(np.clip(out_vec[2], 0.0, 1.0))

                lines.append(f"{r_out:.6f} {g_out:.6f} {b_out:.6f}")

    return "\n".join(lines) + "\n"


def fit(ref_input: Union[str, np.ndarray], graded_input: Union[str, np.ndarray]) -> np.ndarray:
    """Fits in-memory 3x4 affine color matrix from file paths or float64 RGB arrays. NO I/O."""
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

    return fit_affine_matrix(ref_flat, grad_flat)
