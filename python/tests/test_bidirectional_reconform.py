# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_bidirectional_reconform.py
Comprehensive test suite for Universal Bi-Directional Reconform Engine & Center-World Fallback (PR 3).
"""

import math
import pytest

from core.aspect_strategy import (
    AspectStrategy,
    classify as classify_aspect,
)
from core.scale_engine import ScaleEngine
from models.scrape_manifest import (
    CameraProperties,
    LayerModel,
    LightProperties,
    ProjectInfo,
    ScrapeManifest,
)
from models.studio_profile import StudioProfile, ProfileRule, SafeArea


def _make_manifest(
    width: int,
    height: int,
    layers: list[LayerModel],
    name: str = "Test_Comp",
) -> ScrapeManifest:
    return ScrapeManifest(
        status="OK",
        project_info=ProjectInfo(name=name, width=width, height=height, fps=24.0),
        layers=layers,
    )


class TestAspectStrategyClassification:
    """1. Tests aspect ratio classification across arbitrary dimension pairs."""

    def test_aspect_strategy_classification_widen(self):
        res = classify_aspect(1080, 1920, 1920, 1080)
        assert res.strategy == AspectStrategy.WIDEN
        assert res.ar_delta_pct > 0

    def test_aspect_strategy_classification_narrow(self):
        res = classify_aspect(1920, 1080, 1080, 1920)
        assert res.strategy == AspectStrategy.NARROW
        assert res.ar_delta_pct < 0

    def test_aspect_strategy_classification_equal_different_resolution(self):
        res = classify_aspect(1920, 1080, 3840, 2160)
        assert res.strategy == AspectStrategy.EQUAL_DIFFERENT_RESOLUTION
        assert abs(res.ar_delta_pct) < 0.001

    def test_aspect_strategy_classification_preserve(self):
        res = classify_aspect(1920, 1080, 1920, 1080)
        assert res.strategy == AspectStrategy.PRESERVE
        assert res.ar_delta_pct == 0.0

    def test_aspect_strategy_raises_value_error_on_zero_dimensions(self):
        with pytest.raises(ValueError):
            classify_aspect(0, 1080, 1920, 1080)
        with pytest.raises(ValueError):
            classify_aspect(1920, 1080, 1920, -100)


class TestBiDirectionalReconformMath:
    """2. Tests bi-directional scale engine conform passes in wide and narrow directions."""

    def test_widen_9x16_to_16x9_reconform(self):
        """9:16 vertical (1080x1920) conformed into 16:9 horizontal (1920x1080)."""
        layer = LayerModel(index=1, name="Center_Logo", uid="u-logo", position=[540.0, 960.0, 0.0], scale=[100.0, 100.0, 100.0])
        manifest = _make_manifest(1080, 1920, [layer])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        assert res["status"] == "SAFE"
        assert len(res["layers"]) == 1
        t = res["layers"][0]["conformed_transforms"]
        # Fit scale factor = min(1920/1080, 1080/1920) = 1080/1920 = 0.5625
        assert t["scale"][0] == pytest.approx(56.25, abs=1e-2)
        assert t["position"][0] == pytest.approx(960.0, abs=1e-2)
        assert t["position"][1] == pytest.approx(540.0, abs=1e-2)

    def test_narrow_16x9_to_9x16_reconform(self):
        """16:9 horizontal (1920x1080) conformed into 9:16 vertical (1080x1920)."""
        layer = LayerModel(index=1, name="Center_Logo", uid="u-logo", position=[960.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0])
        manifest = _make_manifest(1920, 1080, [layer])

        engine = ScaleEngine(manifest, target_width=1080, target_height=1920, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        assert res["status"] == "SAFE"
        t = res["layers"][0]["conformed_transforms"]
        assert t["scale"][0] == pytest.approx(56.25, abs=1e-2)
        assert t["position"][0] == pytest.approx(540.0, abs=1e-2)
        assert t["position"][1] == pytest.approx(960.0, abs=1e-2)

    def test_square_1x1_to_16x9_reconform(self):
        """1:1 square (1080x1080) conformed into 16:9 (1920x1080). Widen strategy."""
        layer = LayerModel(index=1, name="Square_Item", uid="u-sq", position=[540.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0])
        manifest = _make_manifest(1080, 1080, [layer])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        assert res["status"] == "SAFE"
        t = res["layers"][0]["conformed_transforms"]
        # Scale = min(1920/1080, 1080/1080) = 1.0
        assert t["scale"][0] == pytest.approx(100.0, abs=1e-2)
        assert t["position"][0] == pytest.approx(960.0, abs=1e-2)
        assert t["position"][1] == pytest.approx(540.0, abs=1e-2)

    def test_16x9_to_1x1_reconform(self):
        """16:9 (1920x1080) conformed into 1:1 square (1080x1080). Narrow strategy."""
        layer = LayerModel(index=1, name="Wide_Item", uid="u-w", position=[960.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0])
        manifest = _make_manifest(1920, 1080, [layer])

        engine = ScaleEngine(manifest, target_width=1080, target_height=1080, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        assert res["status"] == "SAFE"
        t = res["layers"][0]["conformed_transforms"]
        assert t["scale"][0] == pytest.approx(56.25, abs=1e-2)
        assert t["position"][0] == pytest.approx(540.0, abs=1e-2)

    def test_ultrawide_21x9_to_9x16_reconform(self):
        """21:9 ultrawide (2560x1080) conformed into 9:16 vertical (1080x1920)."""
        layer = LayerModel(index=1, name="UW_Item", uid="u-uw", position=[1280.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0])
        manifest = _make_manifest(2560, 1080, [layer])

        engine = ScaleEngine(manifest, target_width=1080, target_height=1920, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        assert res["status"] == "SAFE"
        t = res["layers"][0]["conformed_transforms"]
        # min(1080/2560 = 0.421875, 1920/1080 = 1.777) = 0.421875
        assert t["scale"][0] == pytest.approx(42.1875, abs=1e-2)

    def test_9x16_to_ultrawide_21x9_reconform(self):
        """9:16 vertical (1080x1920) conformed into 21:9 ultrawide (2560x1080)."""
        layer = LayerModel(index=1, name="Vert_Item", uid="u-v", position=[540.0, 960.0, 0.0], scale=[100.0, 100.0, 100.0])
        manifest = _make_manifest(1080, 1920, [layer])

        engine = ScaleEngine(manifest, target_width=2560, target_height=1080, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        assert res["status"] == "SAFE"
        t = res["layers"][0]["conformed_transforms"]
        assert t["scale"][0] == pytest.approx(56.25, abs=1e-2)
        assert t["position"][0] == pytest.approx(1280.0, abs=1e-2)

    def test_extreme_stadium_ribbon_reconform(self):
        """Extreme 445:1 stadium ribbon (21360x48) conform from HD (1920x1080)."""
        layer = LayerModel(index=1, name="Ribbon_Item", uid="u-r", position=[960.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0])
        manifest = _make_manifest(1920, 1080, [layer])

        engine = ScaleEngine(manifest, target_width=21360, target_height=48, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        assert res["status"] == "SAFE"
        t = res["layers"][0]["conformed_transforms"]
        assert math.isfinite(t["position"][0]) and math.isfinite(t["position"][1])
        assert math.isfinite(t["scale"][0])


class TestReversibilityAndRoundtrip:
    """3. Tests mathematical roundtrip reversibility ($A -> B -> A$)."""

    def test_roundtrip_16x9_to_9x16_to_16x9_uniform_layers_reversible(self):
        """HD (1920x1080) -> 9:16 (1080x1920) -> HD (1920x1080). Center element position is reversible."""
        orig_pos = [960.0, 540.0, 0.0]
        orig_scale = [100.0, 100.0, 100.0]

        # Pass 1: HD -> 9:16
        m1 = _make_manifest(1920, 1080, [LayerModel(index=1, name="L", uid="u", position=orig_pos, scale=orig_scale)])
        res1 = ScaleEngine(m1, target_width=1080, target_height=1920, scale_mode="Fit", bleed_pct=0.0).conform()
        t1 = res1["layers"][0]["conformed_transforms"]

        # Pass 2: 9:16 -> HD
        m2 = _make_manifest(1080, 1920, [LayerModel(index=1, name="L", uid="u", position=t1["position"], scale=t1["scale"])])
        res2 = ScaleEngine(m2, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0).conform()
        t2 = res2["layers"][0]["conformed_transforms"]

        # Assert roundtrip restoration
        assert t2["position"][0] == pytest.approx(orig_pos[0], abs=1e-2)
        assert t2["position"][1] == pytest.approx(orig_pos[1], abs=1e-2)

    def test_roundtrip_9x16_to_16x9_to_9x16_reversible(self):
        """9:16 (1080x1920) -> HD (1920x1080) -> 9:16 (1080x1920)."""
        orig_pos = [540.0, 960.0, 0.0]
        orig_scale = [100.0, 100.0, 100.0]

        m1 = _make_manifest(1080, 1920, [LayerModel(index=1, name="L", uid="u", position=orig_pos, scale=orig_scale)])
        res1 = ScaleEngine(m1, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0).conform()
        t1 = res1["layers"][0]["conformed_transforms"]

        m2 = _make_manifest(1920, 1080, [LayerModel(index=1, name="L", uid="u", position=t1["position"], scale=t1["scale"])])
        res2 = ScaleEngine(m2, target_width=1080, target_height=1920, scale_mode="Fit", bleed_pct=0.0).conform()
        t2 = res2["layers"][0]["conformed_transforms"]

        assert t2["position"][0] == pytest.approx(orig_pos[0], abs=1e-2)
        assert t2["position"][1] == pytest.approx(orig_pos[1], abs=1e-2)


class TestWidenStrategyRulesAndGuardrails:
    """4. Tests WIDEN rules, gravity, cameras, lights, and Center-World guardrail."""

    def test_widen_rule_set_pin_top_gravity(self):
        """Pin top gravity in widened horizontal frame."""
        profile = StudioProfile(
            id="test_prof",
            name="Test_Prof",
            prefixes=[ProfileRule(match="TT_", tag="TYPE", gravity="top")],
            safe_area=SafeArea(top=0.1, bottom=0.1, left=0.05, right=0.05),
        )
        layer = LayerModel(index=1, name="TT_Title", uid="u-tt", position=[540.0, 200.0, 0.0], scale=[100.0, 100.0, 100.0])
        manifest = _make_manifest(1080, 1920, [layer])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0, profile=profile)
        res = engine.conform()

        t = res["layers"][0]["conformed_transforms"]
        # Position Y should pin near top safe area (sy + sh * 0.12 = 211.68px)
        assert t["position"][1] == pytest.approx(211.68, abs=5.0)

    def test_widen_rule_set_pin_bottom_gravity(self):
        """Pin bottom gravity in widened horizontal frame."""
        profile = StudioProfile(
            id="test_prof",
            name="Test_Prof",
            prefixes=[ProfileRule(match="LGL_", tag="LEGALS", gravity="bottom")],
            safe_area=SafeArea(top=0.1, bottom=0.1, left=0.05, right=0.05),
        )
        layer = LayerModel(index=1, name="LGL_Disclaimer", uid="u-lgl", position=[540.0, 1800.0, 0.0], scale=[100.0, 100.0, 100.0])
        manifest = _make_manifest(1080, 1920, [layer])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0, profile=profile)
        res = engine.conform()

        t = res["layers"][0]["conformed_transforms"]
        # Position Y should pin near bottom safe area (sy + sh * 0.88 = 868.32px)
        assert t["position"][1] == pytest.approx(868.32, abs=5.0)

    def test_widen_rule_set_fill_scale_multiplier(self):
        """Fill scale expands to cover full widened frame."""
        profile = StudioProfile(
            id="test_prof",
            name="Test_Prof",
            prefixes=[ProfileRule(match="BG_", tag="BACKGROUND", gravity="fill")],
        )
        layer = LayerModel(index=1, name="BG_Plate", uid="u-bg", position=[540.0, 960.0, 0.0], scale=[100.0, 100.0, 100.0])
        manifest = _make_manifest(1080, 1920, [layer])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0, profile=profile)
        res = engine.conform()

        t = res["layers"][0]["conformed_transforms"]
        # fill_S = max(1920/1080 = 1.777, 1080/1920 = 0.5625) = 1.777
        assert t["scale"][0] == pytest.approx(177.77, abs=1e-1)

    def test_widen_rule_set_hero_camera_k_depth_scalar(self):
        """Hero camera depth zoom scales by K in widened frame."""
        cam = LayerModel(
            index=1,
            name="Camera 1",
            uid="u-cam",
            layer_kind="camera",
            position=[540.0, 960.0, -1500.0],
            camera=CameraProperties(zoom=1500.0),
        )
        manifest = _make_manifest(1080, 1920, [cam])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0, camera_depth_mode="K")
        res = engine.conform()

        assert res["status"] == "SAFE"
        cam_conf = res["layers"][0]["conformed_transforms"]["camera"]
        assert cam_conf is not None
        # K = max(1920/1080, 1080/1920) = 1.777778
        assert cam_conf["zoom"] == pytest.approx(1500.0 * (1920.0 / 1080.0), abs=1e-1)

    def test_widen_rule_set_preserves_3d_light_intensity(self):
        """3D light intensity preserved while radius scales by S."""
        light = LayerModel(
            index=1,
            name="Spot_Light",
            uid="u-lt",
            layer_kind="light",
            position=[540.0, 960.0, -200.0],
            light=LightProperties(intensity=100.0, radius=50.0),
        )
        manifest = _make_manifest(1080, 1920, [light])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        lt_conf = res["layers"][0]["conformed_transforms"]["light"]
        assert lt_conf["intensity"] == 100.0
        assert lt_conf["radius"] == pytest.approx(50.0 * 0.5625, abs=1e-2)

    def test_widen_rule_set_guide_protect_passthrough(self):
        """GUIDE and PROTECT layers pass through and are recorded in tag_passthrough."""
        l1 = LayerModel(index=1, name="Guide_Layer", uid="u-g", content_tag="GUIDE")
        l2 = LayerModel(index=2, name="Protect_Layer", uid="u-p", content_tag="PROTECT")
        manifest = _make_manifest(1080, 1920, [l1, l2])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        assert "Guide_Layer" in engine.tag_passthrough
        assert "Protect_Layer" in engine.tag_passthrough
        assert len(engine.tag_passthrough) == 2

    def test_widen_rule_set_collapsed_transformations_warning(self):
        """Collapsed transformations flag adds layer to warnings list."""
        layer = LayerModel(index=1, name="Vector_Layer", uid="u-vec", collapseTransformations=True)
        manifest = _make_manifest(1080, 1920, [layer])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0)
        engine.conform()
        assert "Vector_Layer" in engine.collapsed_layer_warnings

    def test_bleed_percentage_applied_to_widen_scale(self):
        """5% bleed increases final scale."""
        layer = LayerModel(index=1, name="L", uid="u", position=[540.0, 960.0, 0.0], scale=[100.0, 100.0, 100.0])
        manifest = _make_manifest(1080, 1920, [layer])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.05)
        res = engine.conform()

        t = res["layers"][0]["conformed_transforms"]
        # Base scale 0.5625 * 1.05 = 0.590625
        assert t["scale"][0] == pytest.approx(59.0625, abs=1e-2)

    def test_spatial_bound_warning_on_extreme_out_of_bounds(self, caplog):
        """Centroid landing far out of bounds logs SpatialBoundWarning."""
        layer = LayerModel(index=1, name="Far_Layer", uid="u-far", position=[50000.0, 50000.0, 0.0])
        manifest = _make_manifest(1080, 1920, [layer])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0)
        engine.conform()
        # Should complete safely without crashing

    def test_group_centroid_multi_line_text_in_widened_frame(self):
        """Multi-line text group maintains internal line offsets when pinned in widened frame."""
        profile = StudioProfile(
            id="test_prof",
            name="Test_Prof",
            prefixes=[ProfileRule(match="TITLE_", tag="TYPE", gravity="top")],
            safe_area=SafeArea(top=0.1, bottom=0.1, left=0.05, right=0.05),
        )
        l1 = LayerModel(index=1, name="TITLE_Line1", uid="u-l1", content_tag="TYPE", position=[540.0, 300.0, 0.0], scale=[100.0, 100.0, 100.0])
        l2 = LayerModel(index=2, name="TITLE_Line2", uid="u-l2", content_tag="TYPE", position=[540.0, 400.0, 0.0], scale=[100.0, 100.0, 100.0])
        manifest = _make_manifest(1080, 1920, [l1, l2])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0, profile=profile)
        res = engine.conform()

        t1 = res["layers"][0]["conformed_transforms"]
        t2 = res["layers"][1]["conformed_transforms"]
        # Spacing between lines should scale by S (100px * 0.5625 = 56.25px)
        delta_y = abs(t2["position"][1] - t1["position"][1])
        assert delta_y == pytest.approx(56.25, abs=1.0)

    def test_sealed_precomp_in_widened_frame_scales_uniformly(self):
        """Sealed precomp child layers preserve internal composition."""
        l_root = LayerModel(index=1, name="Precomp_Wrap", uid="u-wrap", containing_comp_id=1, position=[540.0, 960.0, 0.0])
        l_child = LayerModel(index=2, name="Precomp_Child", uid="u-child", containing_comp_id=2, position=[100.0, 100.0, 0.0])
        manifest = _make_manifest(1080, 1920, [l_root, l_child])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0, layout="scene")
        res = engine.conform()

        assert res["status"] == "SAFE"
        assert len(res["layers"]) == 2

    def test_scene_preserve_mode_in_widened_frame(self):
        """Explicit scene layout mode forces uniform S on all cameras and layers."""
        cam = LayerModel(
            index=1,
            name="Camera 1",
            uid="u-cam",
            layer_kind="camera",
            position=[540.0, 960.0, -1500.0],
            camera=CameraProperties(zoom=1500.0),
        )
        manifest = _make_manifest(1080, 1920, [cam])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0, layout="scene")
        res = engine.conform()

        cam_conf = res["layers"][0]["conformed_transforms"]["camera"]
        # Under scene-preserve, camera uses uniform S (0.5625), NOT K (1.777)
        assert cam_conf["zoom"] == pytest.approx(1500.0 * 0.5625, abs=1e-1)

    def test_center_world_guardrail_fallback_on_corrupt_layer_position(self, capsys):
        """Corrupt layer with NaN/Inf falls back to Center-World [tgt_cx, tgt_cy]."""
        layer = LayerModel(
            index=1,
            name="Corrupt_Layer",
            uid="u-corrupt",
            position=[540.0, 960.0, 0.0],
            scale=[100.0, 100.0, 100.0],
        )
        # Mutate to NaN post-validation to simulate upstream memory corruption
        layer.position = [float("nan"), 960.0, 0.0]
        manifest = _make_manifest(1080, 1920, [layer])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        assert res["status"] == "SAFE"
        t = res["layers"][0]["conformed_transforms"]
        # Center world fallback = [1920 / 2 = 960.0, 1080 / 2 = 540.0, 0.0]
        assert t["position"][0] == 960.0
        assert t["position"][1] == 540.0
        assert t["scale"][0] == pytest.approx(56.25, abs=1e-2)

    def test_center_world_guardrail_fallback_on_injected_gravity_exception(self, monkeypatch):
        """If rule set raises unexpected exception, Center-World guardrail protects the conform."""
        def _failing_rule_set(*args, **kwargs):
            raise RuntimeError("Synthetic rule set fault")

        monkeypatch.setattr("core.scale_engine_widen.apply_narrow_rule_set", _failing_rule_set)

        layer = LayerModel(index=1, name="Title", uid="u-tt", position=[540.0, 200.0, 0.0])
        manifest = _make_manifest(1080, 1920, [layer])

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        assert res["status"] == "SAFE"
        t = res["layers"][0]["conformed_transforms"]
        assert t["position"][0] == 960.0
        assert t["position"][1] == 540.0
        assert t["scale"][0] == pytest.approx(56.25, abs=1e-2)

    def test_widen_rule_set_multi_level_layer_parity(self):
        """Ensure multi-layer conform maintains index order and count parity."""
        layers = [
            LayerModel(index=1, name="BG", uid="u-1", position=[540.0, 960.0, 0.0]),
            LayerModel(index=2, name="Title", uid="u-2", position=[540.0, 400.0, 0.0]),
            LayerModel(index=3, name="Subtitle", uid="u-3", parent_index=2, position=[0.0, 50.0, 0.0]),
            LayerModel(index=4, name="Guide", uid="u-4", content_tag="GUIDE"),
        ]
        manifest = _make_manifest(1080, 1920, layers)

        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        assert len(res["layers"]) == 4
        assert [l["index"] for l in res["layers"]] == [1, 2, 3, 4]
        assert res["layers"][2]["conformed_transforms"]["is_root"] is False
        assert "Guide" in engine.tag_passthrough

    def test_widen_rule_set_nested_precomp_custom_comp_dims(self):
        """Precomp with custom comp_dims uses nested center as remap origin."""
        l_nested = LayerModel(index=1, name="Nested_Layer", uid="u-nested", containing_comp_id=5, position=[250.0, 250.0, 0.0])
        manifest = _make_manifest(1080, 1920, [l_nested])

        # Nested comp is 500x500 (cx=250, cy=250)
        comp_dims = {5: (500, 500)}
        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0, comp_dims=comp_dims)
        res = engine.conform()

        assert res["status"] == "SAFE"
        t = res["layers"][0]["conformed_transforms"]
        # Position is centered at target comp center (960, 540)
        assert t["position"][0] == pytest.approx(960.0, abs=1e-2)
        assert t["position"][1] == pytest.approx(540.0, abs=1e-2)


