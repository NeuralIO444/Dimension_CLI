# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).

"""
test_camera_depth_mode.py

Tests for the K/S camera depth-axis mode selection:

  - Static camera (no depth animation) → S mode (uniform scale).
  - Animated camera (position Z keys) → K mode (perspective).
  - Animated zoom keys → K mode.
  - Animated focusDistance keys → K mode.
  - Explicit override via camera_depth_mode param wins over auto-detect.
  - conform_options.load/save round-trip.
  - Gardener prescan produces CameraDepthWarning with correct mode.
"""

from __future__ import annotations

import math
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


from core.scale_engine import ScaleEngine
from core.comment_gardener import prescan_comp, CameraDepthWarning
from core.conform_options import load_conform_options, save_conform_options, get_camera_depth_mode
from models.scrape_manifest import (
    ScrapeManifest,
    LayerModel,
    ProjectInfo,
    TemporalDataDict,
    TemporalDataMap,
)

# ── Fixture helpers ────────────────────────────────────────────────────────

TARGET_W, TARGET_H = 1080, 1920  # HD→TikTok
SRC_W, SRC_H = 1920, 1080


def _proj() -> ProjectInfo:
    return ProjectInfo(name="test", width=SRC_W, height=SRC_H, fps=24.0)


def _camera_layer(
    *,
    pos_z_keys: bool = False,
    zoom_keys: bool = False,
    focus_keys: bool = False,
    zoom_val: float = 1777.78,
    pos_z_val: float = -366.67,
    focus_val: float = 500.0,
) -> LayerModel:
    """Build a minimal camera LayerModel with optional depth keyframes."""

    def _key_stream(val: float) -> dict:
        return {"times": [0.0, 1.0], "values": [val, val * 1.1]}

    td_kwargs: dict = {}
    if pos_z_keys:
        td_kwargs["position_z"] = TemporalDataMap(**_key_stream(pos_z_val))
    if zoom_keys:
        td_kwargs["camera_zoom"] = TemporalDataMap(**_key_stream(zoom_val))
    if focus_keys:
        td_kwargs["camera_focusDistance"] = TemporalDataMap(**_key_stream(focus_val))

    return LayerModel.model_validate({
        "index": 1,
        "name": "Camera 1",
        "layer_kind": "camera",
        "position": [SRC_W / 2, SRC_H / 2, pos_z_val],
        "scale": [100, 100, 100],
        "anchor": [0, 0, 0],
        "rotation_z": 0.0,
        "camera": {
            "zoom": zoom_val,
            "pointOfInterest": [SRC_W / 2, SRC_H / 2, 0.0],
            "depthOfField": False,
            "focusDistance": focus_val,
            "aperture": 17.9,
            "blurLevel": 100.0,
        },
        "temporal_data": TemporalDataDict(**td_kwargs).model_dump() if td_kwargs else None,
    })


def _av_layer(name: str = "BG", tag: str = "FILL") -> LayerModel:
    return LayerModel.model_validate({
        "index": 2,
        "name": name,
        "layer_kind": "av",
        "position": [SRC_W / 2, SRC_H / 2, 0.0],
        "scale": [100, 100, 100],
        "anchor": [0, 0, 0],
        "rotation_z": 0.0,
        "content_tag": tag,
        "content_tag_source": "manual_comment",
    })


def _make_manifest(*layers) -> ScrapeManifest:
    return ScrapeManifest(
        status="OK",
        project_info=_proj(),
        layers=list(layers),
    )


def _engine(manifest: ScrapeManifest, *, mode=None) -> ScaleEngine:
    return ScaleEngine(
        manifest=manifest,
        target_width=TARGET_W,
        target_height=TARGET_H,
        scale_mode="Fit",
        bleed_pct=0.0,
        camera_depth_mode=mode,
    )


# ── Auto-detection tests ───────────────────────────────────────────────────

class TestAutoDetect:

    def test_static_camera_selects_S(self):
        """No depth keyframes → S mode."""
        m = _make_manifest(_camera_layer(), _av_layer())
        e = _engine(m)
        assert e.camera_depth_mode == "S"

    def test_position_z_keys_selects_K(self):
        """Camera with position Z keyframes → K mode."""
        m = _make_manifest(_camera_layer(pos_z_keys=True), _av_layer())
        e = _engine(m)
        assert e.camera_depth_mode == "K"

    def test_zoom_keys_selects_K(self):
        """Camera with zoom keyframes → K mode (push-in via focal length)."""
        m = _make_manifest(_camera_layer(zoom_keys=True), _av_layer())
        e = _engine(m)
        assert e.camera_depth_mode == "K"

    def test_focus_keys_selects_K(self):
        """Camera with focusDistance keyframes → K mode (rack focus)."""
        m = _make_manifest(_camera_layer(focus_keys=True), _av_layer())
        e = _engine(m)
        assert e.camera_depth_mode == "K"

    def test_no_camera_in_comp_defaults_to_S(self):
        """No camera layer → S (fallback, shouldn't affect non-camera comps)."""
        m = _make_manifest(_av_layer())
        e = _engine(m)
        assert e.camera_depth_mode == "S"


# ── Explicit override tests ────────────────────────────────────────────────

class TestExplicitOverride:

    def test_explicit_K_overrides_auto_S(self):
        """Static camera but user forced K → K wins."""
        m = _make_manifest(_camera_layer(), _av_layer())
        e = _engine(m, mode="K")
        assert e.camera_depth_mode == "K"

    def test_explicit_S_overrides_auto_K(self):
        """Animated camera but user forced S → S wins."""
        m = _make_manifest(_camera_layer(pos_z_keys=True), _av_layer())
        e = _engine(m, mode="S")
        assert e.camera_depth_mode == "S"

    def test_none_triggers_auto_detect(self):
        """None (the default) always goes through auto-detection."""
        m = _make_manifest(_camera_layer(zoom_keys=True), _av_layer())
        e = _engine(m, mode=None)
        assert e.camera_depth_mode == "K"


# ── Conform output tests ───────────────────────────────────────────────────

class TestConformOutput:
    """Verify that camera zoom output reflects the selected mode."""

    @staticmethod
    def _conform_zoom(mode):
        zoom = 1777.78
        m = _make_manifest(_camera_layer(zoom_val=zoom), _av_layer())
        result = _engine(m, mode=mode).conform()
        cam_layer = next(l for l in result["layers"] if l.get("layer_kind") == "camera")
        return cam_layer["conformed_transforms"]["camera"]["zoom"]

    def test_K_mode_zoom_scales_by_raw_K(self):
        """K mode: zoom × K (perspective, Bug L behavior)."""
        K = max(TARGET_W / SRC_W, TARGET_H / SRC_H)  # 1.778
        zoom_src = 1777.78
        zoom_out = self._conform_zoom("K")
        assert math.isclose(zoom_out, zoom_src * K, rel_tol=0.005)

    def test_S_mode_zoom_scales_by_S(self):
        """S mode: zoom × S (uniform, matches Scale Composition)."""
        zoom_src = 1777.78
        zoom_out = self._conform_zoom("S")
        S = min(TARGET_W / SRC_W, TARGET_H / SRC_H)  # 0.5625
        assert math.isclose(zoom_out, zoom_src * S, rel_tol=0.005)

    def test_K_and_S_modes_differ_for_narrow_conform(self):
        """For HD→TikTok (K/S diverge 3×), the two modes produce different zoom."""
        k_zoom = self._conform_zoom("K")
        s_zoom = self._conform_zoom("S")
        ratio = k_zoom / s_zoom
        # Should be approximately K/S = 1.778/0.5625 = 3.16
        assert ratio > 2.5, f"Expected 3×+ divergence, got {ratio:.2f}"


# ── conform_options.py tests ───────────────────────────────────────────────

class TestConformOptions:

    def test_save_and_load_round_trip(self, tmp_path):
        dim_dir = tmp_path / ".dimension"
        save_conform_options(dim_dir, {"camera_depth_mode": "S"})
        opts = load_conform_options(dim_dir)
        assert opts.get("camera_depth_mode") == "S"

    def test_load_missing_file_returns_empty_dict(self, tmp_path):
        opts = load_conform_options(tmp_path / ".dimension")
        assert opts == {}

    def test_get_camera_depth_mode_returns_saved_value(self, tmp_path):
        dim_dir = tmp_path / ".dimension"
        save_conform_options(dim_dir, {"camera_depth_mode": "K"})
        assert get_camera_depth_mode(dim_dir) == "K"

    def test_get_camera_depth_mode_returns_none_when_absent(self, tmp_path):
        assert get_camera_depth_mode(tmp_path / ".dimension") is None

    def test_invalid_mode_not_returned(self, tmp_path):
        dim_dir = tmp_path / ".dimension"
        save_conform_options(dim_dir, {"camera_depth_mode": "X"})
        assert get_camera_depth_mode(dim_dir) is None


# ── Gardener prescan tests ────────────────────────────────────────────────

class TestGardenerPrescan:

    def test_static_camera_warning_recommends_S(self):
        m = _make_manifest(_camera_layer(), _av_layer())
        report = prescan_comp(m)
        w: CameraDepthWarning = report.camera_depth_warning
        assert w is not None
        assert w.recommended_mode == "S"
        assert w.animated is False
        assert w.animated_fields == []

    def test_animated_z_warning_recommends_K(self):
        m = _make_manifest(_camera_layer(pos_z_keys=True), _av_layer())
        report = prescan_comp(m)
        w = report.camera_depth_warning
        assert w is not None
        assert w.recommended_mode == "K"
        assert w.animated is True
        assert "position_z" in w.animated_fields

    def test_animated_zoom_warning_recommends_K(self):
        m = _make_manifest(_camera_layer(zoom_keys=True), _av_layer())
        report = prescan_comp(m)
        w = report.camera_depth_warning
        assert w.recommended_mode == "K"
        assert "zoom" in w.animated_fields

    def test_no_camera_no_warning(self):
        m = _make_manifest(_av_layer())
        report = prescan_comp(m)
        assert report.camera_depth_warning is None

    def test_warning_copy_is_non_empty(self):
        m = _make_manifest(_camera_layer(), _av_layer())
        report = prescan_comp(m)
        assert report.camera_depth_warning.warning_copy
