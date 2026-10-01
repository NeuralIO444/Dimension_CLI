# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_hall_of_horrors_math.py
'Hall of Horrors' Degenerate Mathematical Fixture Suite (TASK-MATH-02 / #272).

Tests singular, degenerate, and extreme boundary geometries against ScaleEngine and SOE.

Growing-suite convention (2026-09-02, autonomous-engineering initiative Phase
2): every real bug found from this point forward -- whether from manual
investigation, the full-catalog conform sweep, the PR-diff-aware generator,
or fuzzing -- gets a permanent regression test here (or in a sibling
`test_hall_of_horrors_*.py` file if the geometry class doesn't fit these),
cross-referenced to its GitHub issue number in the test name or docstring.
This is what turns a one-off fix into a fixture nothing can regress past
silently again. See `python/tools/telemetry_corpus.py` for how real-world
near-misses get surfaced as candidates for promotion into this file.
"""

import math
from core.scale_engine import ScaleEngine
from models.scrape_manifest import ScrapeManifest, ProjectInfo, LayerModel, CameraProperties


class TestHallOfHorrorsMath:
    def test_black_hole_zero_scale_layer(self):
        """Layers with scale = [0, 0, 0] must not cause division by zero or NaN values."""
        manifest = ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(width=1920, height=1080, frame_rate=24.0, duration=10.0, name="ZeroScale"),
            layers=[
                LayerModel(
                    index=1,
                    name="Zero_Scale_Layer",
                    layer_kind="av",
                    width=100,
                    height=100,
                    content_tag="TYPE",
                    position=[960.0, 540.0, 0.0],
                    scale=[0.0, 0.0, 0.0],
                    anchor=[50.0, 50.0, 0.0],
                    rotation=0.0,
                    opacity=100.0,
                    is_root=True,
                )
            ],
        )
        engine = ScaleEngine(manifest, 3840, 2160, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        scale = res["layers"][0]["conformed_transforms"]["scale"]
        assert all(math.isfinite(s) for s in scale)
        assert scale == [0.0, 0.0, 0.0]

    def test_gimbal_singularity_camera_pitch(self):
        """Camera pitched at exactly +90.0° (gimbal lock pitch) must conform cleanly."""
        manifest = ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(width=1920, height=1080, frame_rate=24.0, duration=10.0, name="Gimbal"),
            layers=[
                LayerModel(
                    index=1,
                    name="Camera_90_Deg",
                    layer_kind="camera",
                    position=[960.0, 540.0, -1500.0],
                    rotation=0.0,
                    rotation_x=90.0,
                    rotation_y=0.0,
                    orientation=[90.0, 0.0, 0.0],
                    camera=CameraProperties(
                        zoom=1500.0,
                        focusDistance=1500.0,
                        pointOfInterest=[960.0, 540.0, 0.0],
                    ),
                    is_root=True,
                )
            ],
        )
        engine = ScaleEngine(manifest, 1080, 1920, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        cam_tf = res["layers"][0]["conformed_transforms"]
        assert math.isfinite(cam_tf["position"][0])
        assert math.isfinite(cam_tf["position"][1])
        assert math.isfinite(cam_tf["position"][2])
        assert cam_tf["camera"]["zoom"] > 0.0

    def test_microscopic_and_giant_layer_dimensions(self):
        """Microscopic (0.0001px) and Giant (100,000px) layers must remain finite."""
        manifest = ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(width=1920, height=1080, frame_rate=24.0, duration=10.0, name="ExtremeSize"),
            layers=[
                LayerModel(
                    index=1,
                    name="Microscopic",
                    layer_kind="av",
                    width=0.0001,
                    height=0.0001,
                    position=[960.0, 540.0, 0.0],
                    scale=[100.0, 100.0, 100.0],
                    is_root=True,
                ),
                LayerModel(
                    index=2,
                    name="Giant",
                    layer_kind="av",
                    width=100000,
                    height=100000,
                    position=[960.0, 540.0, 0.0],
                    scale=[100.0, 100.0, 100.0],
                    is_root=True,
                ),
            ],
        )
        engine = ScaleEngine(manifest, 3840, 2160, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        for lyr in res["layers"]:
            pos = lyr["conformed_transforms"]["position"]
            scale = lyr["conformed_transforms"]["scale"]
            assert all(math.isfinite(p) for p in pos)
            assert all(math.isfinite(s) for s in scale)

    def test_collinear_identical_coordinate_stack(self):
        """Multiple layers at identical coordinates [960, 540] must conform without collision exceptions."""
        manifest = ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(width=1920, height=1080, frame_rate=24.0, duration=10.0, name="Collinear"),
            layers=[
                LayerModel(
                    index=i + 1,
                    name=f"Clone_{i}",
                    layer_kind="av",
                    width=200,
                    height=50,
                    content_tag="TYPE",
                    position=[960.0, 540.0, 0.0],
                    scale=[100.0, 100.0, 100.0],
                    is_root=True,
                )
                for i in range(5)
            ],
        )
        engine = ScaleEngine(manifest, 1080, 1920, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        assert len(res["layers"]) == 5
        for lyr in res["layers"]:
            pos = lyr["conformed_transforms"]["position"]
            assert all(math.isfinite(p) for p in pos)
