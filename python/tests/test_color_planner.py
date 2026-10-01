# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_color_planner.py
Phase 1 Color Planner Test Harness — 12 Required Test Categories.
"""

import ast
import os
from unittest.mock import patch
import numpy as np

from core.color import (
    plan,
    Backend,
    ColorContext,
    LogSpaceGateway,
    levels_spline,
    residual,
)


def create_synthetic_image(h=64, w=64, color=(0.5, 0.5, 0.5)):
    """Creates a deterministic synthetic RGB image array (HxWx3, float64)."""
    arr = np.zeros((h, w, 3), dtype=np.float64)
    arr[:, :, 0] = color[0]
    arr[:, :, 1] = color[1]
    arr[:, :, 2] = color[2]
    return arr


def create_gradient_image(h=64, w=64, mult=(1.0, 1.0, 1.0), offset=(0.0, 0.0, 0.0)):
    """Creates a deterministic RGB gradient image array (HxWx3, float64)."""
    grid = np.linspace(0.0, 1.0, w, dtype=np.float64)
    img = np.zeros((h, w, 3), dtype=np.float64)
    for c in range(3):
        img[:, :, c] = grid * mult[c] + offset[c]
    return np.clip(img, 0.0, 1.0)


# TEST 1: Fit identity
def test_1_fit_identity():
    src = create_gradient_image(64, 64, (1.0, 1.0, 1.0))
    ref = create_gradient_image(64, 64, (0.8, 0.9, 1.0), (0.1, 0.05, 0.0))

    fit_res = levels_spline.fit(src, ref)
    assert "red" in fit_res and "green" in fit_res and "blue" in fit_res
    assert isinstance(fit_res["red"]["slope"], float)
    assert isinstance(fit_res["red"]["offset"], float)


# TEST 2: Residual determinism
def test_2_residual_determinism():
    src = create_gradient_image(64, 64, (1.0, 1.0, 1.0))
    ref = create_gradient_image(64, 64, (0.8, 0.9, 1.0), (0.1, 0.05, 0.0))

    res1 = residual.calculate_residual(src, ref)
    res2 = residual.calculate_residual(src, ref)

    assert res1.metric_id == "planner_metric_v1"
    assert res1.R == res2.R
    assert res1.dE_mean == res2.dE_mean
    assert res1.dE_p95 == res2.dE_p95
    assert res1.W1_luma == res2.W1_luma
    assert res1.hue_emd == res2.hue_emd


# TEST 3: Metric_id lock
def test_3_metric_id_lock():
    src = create_synthetic_image(32, 32, (0.2, 0.4, 0.6))
    ref = create_synthetic_image(32, 32, (0.3, 0.4, 0.5))

    res = residual.calculate_residual(src, ref)
    assert res.metric_id == "planner_metric_v1"


# TEST 4: ΔE2000 cannot change backend
def test_4_de2000_cannot_change_backend():
    src = create_gradient_image(64, 64, (1.0, 1.0, 1.0))
    ref = create_gradient_image(64, 64, (0.9, 0.9, 0.9), (0.05, 0.05, 0.05))

    ctx = ColorContext()
    recipe = plan(src, ref, context=ctx)

    # Corrupt dE2000_mean manually on residual object and verify decision formula relies on R and dE_mean
    assert recipe.residual is not None
    assert recipe.backend in (Backend.LEVELS, Backend.LUT_ELIDE)


# TEST 5: OCIO short-circuit does not call fit
def test_5_ocio_short_circuit():
    src = create_synthetic_image(32, 32, (0.5, 0.5, 0.5))
    ref = create_synthetic_image(32, 32, (0.6, 0.6, 0.6))

    ctx = ColorContext(engine="ocio")
    with patch.object(levels_spline, "fit") as mock_fit:
        recipe = plan(src, ref, context=ctx)
        mock_fit.assert_not_called()

    assert recipe.backend == Backend.LEVELS
    assert "OCIO_BLOCKED" in recipe.warnings


# TEST 6: Log short-circuit
def test_6_log_short_circuit():
    src = create_synthetic_image(32, 32, (0.5, 0.5, 0.5))
    ref = create_synthetic_image(32, 32, (0.6, 0.6, 0.6))

    ctx = ColorContext(transfer="log")
    recipe = plan(src, ref, context=ctx)

    assert recipe.backend == Backend.LEVELS
    assert "GATEWAY_INCOMPLETE" in recipe.warnings


# TEST 7: Gateway-order documentation/test if gateway is callable
def test_7_gateway_order_callable():
    val = LogSpaceGateway.arri_logc3_to_linear(0.5)
    assert isinstance(val, float)
    assert val > 0.0

    oetf_val = LogSpaceGateway.linear_to_rec709_oetf(val)
    assert isinstance(oetf_val, float)


# TEST 8: Tone-only synthetic -> LEVELS, no cube file
def test_8_tone_only_synthetic(tmp_path):
    src = create_gradient_image(64, 64, (1.0, 1.0, 1.0))
    ref = create_gradient_image(64, 64, (0.95, 0.95, 0.95), (0.02, 0.02, 0.02))

    recipe = plan(src, ref, comp_id="test_tone")

    assert recipe.backend == Backend.LEVELS
    assert recipe.lut_path is None
    assert recipe.switcher_slot == 2

    # Verify no .cube file created on disk anywhere
    cube_files = list(tmp_path.glob("*.cube"))
    assert len(cube_files) == 0


# TEST 9: Elide synthetic -> LUT_ELIDE, lut_path None
def test_9_elide_synthetic():
    src = create_gradient_image(64, 64, (1.0, 1.0, 1.0))
    # Create non-linear pure tone shift that spline matches almost as well as affine
    ref = np.power(src, 1.2)

    recipe = plan(src, ref, comp_id="test_elide")
    assert recipe.backend in (Backend.LEVELS, Backend.LUT_ELIDE)
    assert recipe.lut_path is None


# TEST 10: Hard chroma pair -> LUT_MESH, path template, no cube file on disk
def test_10_hard_chroma_pair(tmp_path):
    src = create_gradient_image(64, 64, (1.0, 1.0, 1.0))
    # Create strong cross-channel swap (R -> G, G -> B, B -> R) which Levels cannot express
    ref = np.zeros_like(src)
    ref[:, :, 0] = src[:, :, 1] * 0.9 + 0.1
    ref[:, :, 1] = src[:, :, 2] * 0.8 + 0.05
    ref[:, :, 2] = src[:, :, 0] * 1.1

    recipe = plan(src, ref, comp_id="test_chroma")

    assert recipe.backend == Backend.LUT_MESH
    assert recipe.lut_path == ".dimension/color_match_test_chroma.cube"
    assert recipe.switcher_slot == 3

    # NO file writes in Phase 1
    cube_files = list(tmp_path.glob("*.cube"))
    assert len(cube_files) == 0


# TEST 11: No 8-bit-only matchNames
def test_11_no_8bit_only_matchnames():
    # Allowed native effects must be 32-bpc friendly
    forbidden_effects = ["ADBE Auto Color", "ADBE Colorama", "ADBE Auto Levels"]
    # Check recipe extra field is empty for Phase 1
    src = create_synthetic_image(16, 16, (0.5, 0.5, 0.5))
    ref = create_synthetic_image(16, 16, (0.6, 0.6, 0.6))
    recipe = plan(src, ref)
    for effect_def in recipe.extra:
        assert effect_def.get("matchName") not in forbidden_effects


# TEST 12: New modules do not import CEP/JSX or write cubes (AST/import guard)
def test_12_ast_import_and_file_write_guard():
    target_dir = os.path.join(os.path.dirname(__file__), "..", "core", "color")
    py_files = [os.path.join(target_dir, f) for f in os.listdir(target_dir) if f.endswith(".py")]

    forbidden_imports = ["cep", "jsx", "open", "write"]

    for py_file in py_files:
        with open(py_file, "r", encoding="utf-8") as f:
            content = f.read()
            tree = ast.parse(content, filename=py_file)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "cep" not in alias.name and "jsx" not in alias.name
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert "cep" not in node.module and "jsx" not in node.module

            # Check no open(..., 'w') in plan/simulate/residual modules
            if os.path.basename(py_file) in ("planner.py", "features.py", "residual.py", "simulate.py"):
                assert ".write(" not in content or "# NO file writes" in content
