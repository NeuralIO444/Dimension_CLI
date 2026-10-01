# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_gpu_16k_allocation_guard.py
Unit tests for TASK-P2-04 (#252) — GPU 16K Texture Allocation Guard & Extreme Ribbon Scaling Profile.
"""

from pathlib import Path
from core.scale_engine import ScaleEngine
from models.scrape_manifest import ScrapeManifest, ProjectInfo, LayerModel
from logic.studio_profile_registry import StudioProfileRegistry


def _make_dummy_manifest(w=1920, h=1080) -> ScrapeManifest:
    return ScrapeManifest(
        schema_version="5.0",
        status="OK",
        project_info=ProjectInfo(
            name="Test",
            path="/tmp/test.aep",
            active_comp_id=1,
            width=w,
            height=h,
            fps=24.0,
            duration=5.0,
            linear_color=False,
        ),
        layers=[
            LayerModel(
                index=1,
                name="HERO_Title",
                layer_kind="text",
                content_tag="HERO",
                position=[960.0, 540.0, 0.0],
                scale=[100.0, 100.0, 100.0],
            )
        ],
    )


class TestGPU16KAllocationGuard:
    def test_standard_canvas_under_16k_does_not_trigger_guard(self):
        """Standard 4K and 8K canvases do not exceed 16K GPU limit."""
        manifest = _make_dummy_manifest()
        engine_4k = ScaleEngine(manifest, 3840, 2160, "Fit", 0.0)
        assert engine_4k.gpu_16k_limit_exceeded is False

        res_4k = engine_4k.conform()
        assert res_4k["gpu_16k_limit_exceeded"] is False
        assert res_4k["warnings"]["gpu_16k_limit"] is False

        engine_16k_exact = ScaleEngine(manifest, 16384, 1080, "Fit", 0.0)
        assert engine_16k_exact.gpu_16k_limit_exceeded is False

    def test_massive_canvas_over_16k_triggers_guard(self):
        """Ribbon screen (21,360px) or tall ribbon (18,000px) triggers 16K guard."""
        manifest = _make_dummy_manifest()
        # 21,360px stadium ribbon
        engine_ribbon = ScaleEngine(manifest, 21360, 48, "Fit", 0.0)
        assert engine_ribbon.gpu_16k_limit_exceeded is True

        res_ribbon = engine_ribbon.conform()
        assert res_ribbon["gpu_16k_limit_exceeded"] is True
        assert res_ribbon["warnings"]["gpu_16k_limit"] is True

        # Tall vertical banner
        engine_tall = ScaleEngine(manifest, 1080, 18000, "Fit", 0.0)
        assert engine_tall.gpu_16k_limit_exceeded is True


class TestExtremeRibbonProfile:
    def test_ooh_profile_contains_extreme_ribbon_configuration(self):
        """OOH profile must have extreme_ribbon config for AR >= 10.0."""
        import tempfile
        profiles_dir = Path(__file__).resolve().parents[2] / "config" / "profiles"
        with tempfile.TemporaryDirectory() as tmp:
            reg = StudioProfileRegistry(user_dir=Path(tmp) / "profiles", baselines_dir=profiles_dir)
            ooh_profile = reg.get("ooh")
            assert ooh_profile is not None
            assert ooh_profile.extreme_ribbon is not None

            cfg = ooh_profile.extreme_ribbon
            assert cfg["aspect_ratio_threshold"] == 10.0
            assert "HERO" in cfg["height_fit_tags"]
            assert "LOGO" in cfg["height_fit_tags"]
            assert "TT" in cfg["height_fit_tags"]
            assert "TYPE" in cfg["height_fit_tags"]
            assert cfg["horizontal_gravity"] == "pin_center"


class TestBabysitter16KResolutionFactor:
    def test_babysitter_jsx_contains_resolution_factor_downsample(self):
        """Babysitter JSX bundle must contain resolutionFactor = [2, 2] downsampling."""
        jsx_path = Path(__file__).resolve().parents[2] / "Scripts" / "Dimension_Assets" / "Babysitter.jsx"
        content = jsx_path.read_text(encoding="utf-8")
        assert "resolutionFactor = [2, 2]" in content
        assert "16384" in content
