# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_phase1_audit_remediations.py
Unit tests verifying Phase 1 audit remediations (MATH-02, MATH-03, CEP safety).
"""

from core.occlusion.mask_solver import compute_world_bounds
from core.scale_engine import ScaleEngine
from models.scrape_manifest import LayerModel, ProjectInfo, ScrapeManifest


class TestFlippedLayerBoundingBox:
    def test_horizontal_flip_produces_valid_aabb(self):
        """MATH-02: scale[-100, 100] must produce left < right and positive width."""
        bounds = compute_world_bounds(
            position=[500.0, 500.0],
            anchor=[100.0, 50.0],
            scale=[-100.0, 100.0],
            source_rect=[0.0, 0.0, 200.0, 100.0],
        )
        assert bounds is not None
        assert bounds["l"] < bounds["r"]
        assert bounds["t"] < bounds["b"]
        assert bounds["w"] > 0
        assert bounds["h"] > 0
        assert bounds["l"] == 400.0
        assert bounds["r"] == 600.0
        assert bounds["w"] == 200.0

    def test_vertical_flip_produces_valid_aabb(self):
        """MATH-02: scale[100, -100] must produce top < bottom and positive height."""
        bounds = compute_world_bounds(
            position=[500.0, 500.0],
            anchor=[100.0, 50.0],
            scale=[100.0, -100.0],
            source_rect=[0.0, 0.0, 200.0, 100.0],
        )
        assert bounds is not None
        assert bounds["l"] < bounds["r"]
        assert bounds["t"] < bounds["b"]
        assert bounds["w"] > 0
        assert bounds["h"] > 0
        assert bounds["t"] == 450.0
        assert bounds["b"] == 550.0
        assert bounds["h"] == 100.0


class TestCameraDepthModeDetection:
    def test_standard_3d_position_animation_engages_mode_k(self):
        """MATH-03: unseparated 3D position keyframes on camera must trigger Mode K."""
        camera_layer = LayerModel(
            index=1,
            name="Camera 1",
            match_name="ADBE Camera Layer",
            layer_kind="camera",
            position=[960.0, 540.0, -1000.0],
            temporal_data={
                "position": {
                    "times": [0.0, 2.0],
                    "values": [
                        [960.0, 540.0, -1000.0],
                        [960.0, 540.0, -500.0],
                    ],
                }
            },
        )
        manifest = ScrapeManifest(
            schema_version="5.2.0",
            status="ok",
            project_info=ProjectInfo(name="Test", width=1920, height=1080, fps=24.0, duration=5.0),
            layers=[camera_layer],
        )
        engine = ScaleEngine(manifest, 1080, 1920, "Fit", 0.0)
        assert engine.camera_depth_mode == "K"

    def test_static_camera_retains_mode_s(self):
        """MATH-03: static camera with no keyframes must retain uniform Mode S."""
        camera_layer = LayerModel(
            index=1,
            name="Camera 1",
            match_name="ADBE Camera Layer",
            layer_kind="camera",
            position=[960.0, 540.0, -1000.0],
        )
        manifest = ScrapeManifest(
            schema_version="5.2.0",
            status="ok",
            project_info=ProjectInfo(name="Test", width=1920, height=1080, fps=24.0, duration=5.0),
            layers=[camera_layer],
        )
        engine = ScaleEngine(manifest, 1080, 1920, "Fit", 0.0)
        assert engine.camera_depth_mode == "S"
