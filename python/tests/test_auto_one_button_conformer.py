# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_auto_one_button_conformer.py
Automated test suite asserting autonomous One-Button Conformer (Auto mode) dual-zone behavior.
"""

import json
import os
import pytest

from core.scale_engine import ScaleEngine
from models.scrape_manifest import ScrapeManifest

_FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "session_2026_07_02", "87n_fresh_manifest.json"
)


@pytest.fixture
def fresh_87n_manifest():
    with open(_FIXTURE_PATH, "r", encoding="utf-8") as f:
        return ScrapeManifest.model_validate(json.load(f))


def test_auto_mode_dual_zone_conformance(fresh_87n_manifest):
    """
    Assert that Auto mode applies Fit (56.25%) to 3D typography/camera scene elements
    while simultaneously applying Fill (177.78%) to background/EFX plates.
    """
    engine = ScaleEngine(fresh_87n_manifest, 1080, 1920, scale_mode="Auto", bleed_pct=0.0, layout="auto")
    result = engine.conform()

    # Find content and background layers
    text_layer = next(l for l in result["layers"] if l["name"] == "EVERYTHING  Outlines")
    efx_layer = next(l for l in result["layers"] if l["name"] == "EFX")

    # Typography content fits the 9:16 frame cleanly
    scale_text = text_layer["conformed_transforms"]["scale"]
    assert pytest.approx(scale_text[0], 0.01) == 56.25
    assert pytest.approx(scale_text[1], 0.01) == 56.25

    # EFX Fill plate expands to cover full 1080x1920 frame
    scale_efx = efx_layer["conformed_transforms"]["scale"]
    assert pytest.approx(scale_efx[0], 0.01) == 177.78
    assert pytest.approx(scale_efx[1], 0.01) == 177.78


def test_fill_mode_camera_scene_preservation_guard(fresh_87n_manifest):
    """
    Assert that even if scale_mode='Fill' is passed, 3D camera scenes preserve framing
    and do not explode to 186.66% scale.
    """
    engine = ScaleEngine(fresh_87n_manifest, 1080, 1920, scale_mode="Fill", bleed_pct=0.05, layout="auto")
    result = engine.conform()

    text_layer = next(l for l in result["layers"] if l["name"] == "EVERYTHING  Outlines")
    scale_text = text_layer["conformed_transforms"]["scale"]

    # In Fill mode with bleed, camera scene content scales by fit_S * (1+bleed) = 59.06%
    assert scale_text[0] < 100.0
    assert pytest.approx(scale_text[0], 0.01) == 59.06


def test_fill_mode_camera_scene_preservation_guard_z_position(fresh_87n_manifest):
    """
    2026-09-02 regression: Z-POSITION for non-camera 3D-depth content must scale
    by the same fit_S*(1+bleed) the guard already applies to X/Y and to every
    layer's own `scale` property — not by the raw (pre-guard) fill scale.

    Before the fix, camera Z scaled by fit_S*(1+bleed) (~0.590625x) while a
    non-camera layer's Z-position scaled by the raw fill scale (~1.866667x) —
    a 3.16x camera/content depth split inside one supposedly-atomic camera-
    scene unit. Caught reading a real 87N production conform log; this
    asserts camera and content Z move by the identical ratio.
    """
    engine = ScaleEngine(fresh_87n_manifest, 1080, 1920, scale_mode="Fill", bleed_pct=0.05, layout="auto")
    result = engine.conform()

    camera = next(l for l in result["layers"] if l["name"] == "Camera 1")
    content = next(l for l in result["layers"] if l["name"] == "OTHER SIDE Outlines")

    src_camera_z = camera["position"][2]
    src_content_z = content["position"][2]
    dst_camera_z = camera["conformed_transforms"]["position"][2]
    dst_content_z = content["conformed_transforms"]["position"][2]

    assert src_camera_z != 0.0
    assert src_content_z != 0.0

    camera_z_ratio = dst_camera_z / src_camera_z
    content_z_ratio = dst_content_z / src_content_z

    # Both must land on the scene-safe fit scale, not the raw fill scale.
    assert pytest.approx(camera_z_ratio, abs=1e-4) == 0.590625
    assert pytest.approx(content_z_ratio, abs=1e-4) == 0.590625

    # And camera/content depth must agree — the invariant this guard exists
    # to protect: every member of a preserving camera-scene unit moves by
    # the same scalar.
    assert pytest.approx(camera_z_ratio, abs=1e-6) == pytest.approx(content_z_ratio, abs=1e-6)
