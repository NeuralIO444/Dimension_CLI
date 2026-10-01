# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_horizon_color_toolkit.py — TASK-CM-TOOLKIT-01 & 02 (Issues #242, #243)
Unit tests for ACEScg Normalization Gateway and A/B/C Grade Revision Switcher.
"""

from core.grade_switcher import GradeSwitcher, GradeSwitcherConfig
from core.horizon_color import ColorTransformResult, HorizonColorGateway, ParametricMatchResult


class TestHorizonColorGateway:
    def test_bt709_oetf_math(self):
        assert HorizonColorGateway.linear_to_rec709_oetf(0.0) == 0.0
        assert HorizonColorGateway.linear_to_rec709_oetf(0.01) == 0.045
        assert abs(HorizonColorGateway.linear_to_rec709_oetf(1.0) - 1.0) < 1e-4

    def test_arri_logc3_decoding(self):
        lin_zero = HorizonColorGateway.arri_logc3_to_linear(0.092809)
        assert abs(lin_zero - 0.0) < 1e-3

    def test_generate_acescg_to_rec709_cube(self):
        res = HorizonColorGateway.generate_normalization_cube(
            source_space="ACEScg", target_space="Rec.709", size=5
        )
        assert isinstance(res, ColorTransformResult)
        assert res.lut_size == 5
        assert "LUT_3D_SIZE 5" in res.cube_content
        assert "DOMAIN_MAX 1.0 1.0 1.0" in res.cube_content

    def test_derive_3d_lut_from_images(self, tmp_path):
        from PIL import Image

        # Create dummy green and purple images
        ref_img = Image.new("RGB", (64, 64), color=(0, 255, 0))
        grad_img = Image.new("RGB", (64, 64), color=(180, 0, 255))
        ref_path = str(tmp_path / "ref.png")
        grad_path = str(tmp_path / "grad.png")
        cube_path = str(tmp_path / "derived.cube")
        ref_img.save(ref_path)
        grad_img.save(grad_path)

        res = HorizonColorGateway.derive_3d_lut_from_images(
            ref_path=ref_path,
            graded_path=grad_path,
            output_cube_path=cube_path,
            size=5,
        )
        assert isinstance(res, ColorTransformResult)
        assert res.lut_size == 5
        assert "LUT_3D_SIZE 5" in res.cube_content
        assert (tmp_path / "derived.cube").exists()

    def test_derive_parametric_match_from_images_fits_slope_and_offset_per_channel(self, tmp_path):
        from PIL import Image

        # A uniform-color pair gives an exactly solvable per-channel
        # line: every sample point is identical, so slope must land on
        # (graded - offset_at_zero_domain)/1.0-normalized-ref and offset
        # must land exactly on the graded channel's normalized value —
        # ref is pure red (255,0,0), graded is a uniform shift of it.
        ref_img = Image.new("RGB", (64, 64), color=(200, 100, 50))
        grad_img = Image.new("RGB", (64, 64), color=(220, 80, 100))
        ref_path = str(tmp_path / "ref.png")
        grad_path = str(tmp_path / "grad.png")
        ref_img.save(ref_path)
        grad_img.save(grad_path)

        res = HorizonColorGateway.derive_parametric_match_from_images(
            ref_path=ref_path, graded_path=grad_path
        )
        assert isinstance(res, ParametricMatchResult)
        expected_keys = {"slope", "offset", "gamma", "input_black", "input_white", "output_black", "output_white"}
        assert set(res.red.keys()) == expected_keys
        assert set(res.green.keys()) == expected_keys
        assert set(res.blue.keys()) == expected_keys

        # Uniform ref/graded images collapse every sample to a single
        # point, so np.polyfit's slope is arbitrary (a degenerate fit —
        # any line through one point works) but the fitted line MUST
        # still pass through that point: slope*ref_norm + offset ==
        # graded_norm for each channel.
        ref_norm = (200 / 255.0, 100 / 255.0, 50 / 255.0)
        grad_norm = (220 / 255.0, 80 / 255.0, 100 / 255.0)
        for channel, ref_v, grad_v in zip((res.red, res.green, res.blue), ref_norm, grad_norm):
            predicted = channel["slope"] * ref_v + channel["offset"]
            assert abs(predicted - grad_v) < 1e-6

    def test_derive_parametric_match_from_images_never_writes_a_file(self, tmp_path):
        from PIL import Image

        ref_img = Image.new("RGB", (32, 32), color=(0, 255, 0))
        grad_img = Image.new("RGB", (32, 32), color=(180, 0, 255))
        ref_path = str(tmp_path / "ref.png")
        grad_path = str(tmp_path / "grad.png")
        ref_img.save(ref_path)
        grad_img.save(grad_path)

        before = set(tmp_path.iterdir())
        HorizonColorGateway.derive_parametric_match_from_images(ref_path=ref_path, graded_path=grad_path)
        after = set(tmp_path.iterdir())
        assert before == after, "the no-LUT quick-match path must never write a LUT (or any other) file"

    def test_convert_image_to_png(self, tmp_path):
        from PIL import Image

        tiff_img = Image.new("RGB", (32, 32), color=(255, 128, 0))
        tiff_path = str(tmp_path / "test.tif")
        png_path = str(tmp_path / "test.png")
        tiff_img.save(tiff_path, format="TIFF")

        out = HorizonColorGateway.convert_image_to_png(tiff_path, png_path)
        assert out == png_path
        assert (tmp_path / "test.png").exists()

    def test_gamma_calculation_brightened_and_darkened(self, tmp_path):
        import numpy as np
        from PIL import Image

        # Create gradient image (0..255)
        arr = np.linspace(0, 255, 64 * 64, dtype=np.uint8).reshape(64, 64)
        ref_img = Image.fromarray(np.stack([arr, arr, arr], axis=-1), mode="RGB")
        
        # Brightened still (gamma curve power 0.5 => higher median)
        bright_arr = np.clip(255 * (arr / 255.0) ** 0.5, 0, 255).astype(np.uint8)
        bright_img = Image.fromarray(np.stack([bright_arr, bright_arr, bright_arr], axis=-1), mode="RGB")

        # Darkened still (gamma curve power 2.0 => lower median)
        dark_arr = np.clip(255 * (arr / 255.0) ** 2.0, 0, 255).astype(np.uint8)
        dark_img = Image.fromarray(np.stack([dark_arr, dark_arr, dark_arr], axis=-1), mode="RGB")

        ref_path = str(tmp_path / "ref.png")
        bright_path = str(tmp_path / "bright.png")
        dark_path = str(tmp_path / "dark.png")
        ref_img.save(ref_path)
        bright_img.save(bright_path)
        dark_img.save(dark_path)

        res_bright = HorizonColorGateway.derive_parametric_match_from_images(ref_path, bright_path)
        res_dark = HorizonColorGateway.derive_parametric_match_from_images(ref_path, dark_path)

        # In AE Levels, Gamma > 1.0 brightens midtones, Gamma < 1.0 darkens midtones
        assert res_bright.red["gamma"] > 1.05, "Brightened grade must produce Gamma > 1.0 in AE Levels"
        assert res_dark.red["gamma"] < 0.95, "Darkened grade must produce Gamma < 1.0 in AE Levels"

    def test_extreme_bounds_and_non_square_stills(self, tmp_path):
        from PIL import Image

        # Non-square 16:9 4K-like ratio still
        ref_img = Image.new("RGB", (192, 108), color=(0, 0, 0)) # Pure black
        grad_img = Image.new("RGB", (192, 108), color=(255, 255, 255)) # Pure white
        ref_path = str(tmp_path / "black.png")
        grad_path = str(tmp_path / "white.png")
        ref_img.save(ref_path)
        grad_img.save(grad_path)

        res = HorizonColorGateway.derive_parametric_match_from_images(ref_path, grad_path)
        for ch in (res.red, res.green, res.blue):
            assert 0.0 <= ch["input_black"] <= 255.0
            assert 0.0 <= ch["input_white"] <= 255.0
            assert 0.0 <= ch["output_black"] <= 255.0
            assert 0.0 <= ch["output_white"] <= 255.0
            assert 0.4 <= ch["gamma"] <= 2.5



class TestGradeSwitcher:
    def test_build_grade_switcher_config(self):
        revisions = [
            {"id": "A", "label": "Warm Cinema", "lut_path": "/luts/grade_a.cube"},
            {"id": "B", "label": "Cool Broadcast", "lut_path": "/luts/grade_b.cube"},
            {"id": "C", "label": "Punchy Social", "lut_path": "/luts/grade_c.cube"},
        ]
        cfg = GradeSwitcher.build_config(revisions, active_id="A")
        assert isinstance(cfg, GradeSwitcherConfig)
        assert cfg.layer_name == "HORIZON_GRADE_SWITCHER"
        assert len(cfg.slots) == 3
        assert cfg.slots[0].is_active is True
        assert cfg.slots[1].is_active is False
        assert cfg.slider_max == 3
        assert "Revision Selector" in cfg.expression_binding
