# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_full_catalog_conform_sweep.py
Phase 3 of the autonomous-engineering initiative (2026-09-02).

test_aspect_strategy.py already sweeps all 265 built-in targets, but only
through aspect-strategy *classification* -- nothing runs a real
ScaleEngine.conform() per target and checks the output. This closes that
gap: every built-in target, against real fixtures with real complexity
(3D camera scene, sealed precomps, gravity groups), asserting the output
is actually sane -- no layer dropped, no NaN/Inf anywhere, every conformed
scale/position finite.

Marked `preset_sweep` (registered in pytest.ini) so CI can place it
deliberately once its measured runtime is known, rather than guessing.
"""

from __future__ import annotations

import json
import math
import os

import pytest

from core.scale_engine import ScaleEngine
from data.target_catalog import BUILTIN_TARGETS
from models.scrape_manifest import ScrapeManifest

_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
_87N_FIXTURE = os.path.join(_FIXTURES_DIR, "bug_l", "87n_source_manifest.json")
_PARALLAX_FIXTURE = os.path.join(_FIXTURES_DIR, "session_2026_07_02", "parallax_manifest.json")


@pytest.fixture(scope="module")
def _87n_manifest() -> ScrapeManifest:
    with open(_87N_FIXTURE, "r", encoding="utf-8") as f:
        return ScrapeManifest.model_validate(json.load(f))


@pytest.fixture(scope="module")
def _parallax_manifest() -> ScrapeManifest:
    with open(_PARALLAX_FIXTURE, "r", encoding="utf-8") as f:
        return ScrapeManifest.model_validate(json.load(f))


def _assert_finite(value, where: str):
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _assert_finite(v, f"{where}[{i}]")
        return
    if isinstance(value, (int, float)):
        assert math.isfinite(value), f"non-finite value at {where}: {value!r}"


def _assert_conform_output_sane(manifest: ScrapeManifest, result: dict, target_id: str):
    assert result.get("status") == "SAFE", (
        f"{target_id}: conform status was {result.get('status')!r}, expected 'SAFE'"
    )
    layers = result.get("layers", [])
    assert len(layers) == len(manifest.layers), (
        f"{target_id}: layer count changed ({len(manifest.layers)} in -> {len(layers)} out) "
        "-- a layer was silently dropped or duplicated"
    )
    for layer in layers:
        tf = layer.get("conformed_transforms")
        if not tf:
            continue
        name = layer.get("name", "?")
        _assert_finite(tf.get("position"), f"{target_id}/{name}.position")
        _assert_finite(tf.get("scale"), f"{target_id}/{name}.scale")
        if tf.get("camera") is not None:
            cam = tf["camera"]
            _assert_finite(cam.get("pointOfInterest"), f"{target_id}/{name}.camera.pointOfInterest")
            _assert_finite(cam.get("zoom"), f"{target_id}/{name}.camera.zoom")


@pytest.mark.preset_sweep
@pytest.mark.parametrize("target", BUILTIN_TARGETS, ids=[t.id for t in BUILTIN_TARGETS])
def test_87n_conforms_cleanly_across_full_catalog(_87n_manifest, target):
    """Every one of the 265 built-in targets must conform the 87N fixture
    (3D camera scene + sealed precomps + gravity groups) without dropping a
    layer or producing a non-finite transform, regardless of how extreme the
    target's aspect ratio or resolution is."""
    engine = ScaleEngine(
        _87n_manifest,
        target.width,
        target.height,
        scale_mode="Auto",
        bleed_pct=0.0,
        layout="auto",
    )
    result = engine.conform()
    _assert_conform_output_sane(_87n_manifest, result, target.id)


@pytest.mark.preset_sweep
@pytest.mark.parametrize("target", BUILTIN_TARGETS, ids=[t.id for t in BUILTIN_TARGETS])
def test_parallax_conforms_cleanly_across_full_catalog(_parallax_manifest, target):
    """Same sweep against the much richer parallax fixture (195 layers /
    54 comps, 45 sealed precomps) -- the underused "real complexity" fixture
    this session's exploration flagged (3 test files use it vs. 18 for 87N).
    Deliberately the same assertion set as the 87N sweep, not a deeper check
    -- this test is about breadth (does conform survive the whole catalog on
    a much bigger real comp), not re-proving invariants test_unit_invariant.py
    already covers in depth on its own canonical fixture."""
    engine = ScaleEngine(
        _parallax_manifest,
        target.width,
        target.height,
        scale_mode="Auto",
        bleed_pct=0.0,
        layout="auto",
    )
    result = engine.conform()
    _assert_conform_output_sane(_parallax_manifest, result, target.id)
