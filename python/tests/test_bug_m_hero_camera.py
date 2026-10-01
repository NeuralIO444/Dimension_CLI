# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Bug M — only the hero camera receives full intrinsics conform."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.scale_engine import ScaleEngine
from models.scrape_manifest import (
    CameraProperties,
    LayerModel,
    ProjectInfo,
    ScrapeManifest,
)


def _two_camera_manifest():
    return ScrapeManifest(
        status="OK",
        project_info=ProjectInfo(name="MultiCam", width=1920, height=1080),
        layers=[
            LayerModel(
                index=1,
                name="Camera 1",
                uid="hero_cam",
                layer_kind="camera",
                position=[960.0, 540.0, -1500.0],
                camera=CameraProperties(
                    zoom=1000.0,
                    pointOfInterest=[960.0, 540.0, 0.0],
                    focusDistance=1500.0,
                ),
            ),
            LayerModel(
                index=2,
                name="Camera 2",
                uid="ref_cam",
                layer_kind="camera",
                position=[960.0, 540.0, -2000.0],
                camera=CameraProperties(
                    zoom=2000.0,
                    pointOfInterest=[960.0, 540.0, 0.0],
                    focusDistance=2000.0,
                ),
            ),
        ],
    )


def _conform(manifest):
    engine = ScaleEngine(
        manifest=manifest,
        target_width=1080,
        target_height=1920,
        scale_mode="Fit",
        bleed_pct=0.0,
    )
    return engine.conform()


def _cam_by_name(result, name):
    return next(
        l for l in result["layers"]
        if l.get("name") == name and l.get("layer_kind") == "camera"
    )


class TestHeroCameraSelection:
    def test_is_hero_camera_name_match(self):
        manifest = _two_camera_manifest()
        engine = ScaleEngine(
            manifest=manifest,
            target_width=1080,
            target_height=1920,
            scale_mode="Fit",
            bleed_pct=0.0,
        )
        hero = manifest.layers[0]
        ref = manifest.layers[1]
        assert engine._is_hero_camera(hero) is True
        assert engine._is_hero_camera(ref) is False

    def test_secondary_camera_intrinsics_pass_through(self):
        manifest = _two_camera_manifest()
        result = _conform(manifest)
        hero = _cam_by_name(result, "Camera 1")
        ref = _cam_by_name(result, "Camera 2")

        hero_zoom = hero["conformed_transforms"]["camera"]["zoom"]
        ref_zoom = ref["conformed_transforms"]["camera"]["zoom"]

        assert hero_zoom != 1000.0
        assert ref_zoom == 2000.0
        assert ref["conformed_transforms"]["camera"]["focusDistance"] == 2000.0