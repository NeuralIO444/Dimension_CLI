# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_surveyor_archetype.py
Coverage for survey_manifest()'s archetype computation — wiring
core.alpha_hull.classify_layer_archetype() into the Tag stage as a
diagnostic-only field, mirroring the existing source_coverage pattern.

Scope: only the pure classify_layer_archetype() half of alpha_hull.py is
wired here (no new AE data needed — see the module docstring in
core/alpha_hull.py). compute_alpha_bounds()/artwork_bounds are NOT wired
(genuinely needs live-AE alpha-pixel data) — see issue #331.
"""

from core.surveyor import survey_manifest
from models.scrape_manifest import (
    LayerArchetype,
    LayerModel,
    ProjectInfo,
    ScrapeManifest,
)


def _manifest(layers):
    return ScrapeManifest(
        status="OK",
        schema_version="2.0",
        project_info=ProjectInfo(name="TestComp", width=1920, height=1080, fps=24.0),
        layers=layers,
    )


def test_archetype_computed_for_text_layer():
    """A layer with typographic_info classifies as TYPE."""
    layer = LayerModel(
        index=1,
        name="Headline",
        layer_kind="text",
        typographic_info={"text": "Hello", "font_size": 48.0},
        position=[960.0, 540.0, 0.0],
        scale=[100.0, 100.0, 100.0],
    )
    manifest = _manifest([layer])
    survey_manifest(manifest, comp_width=1920, comp_height=1080)
    assert manifest.layers[0].archetype == LayerArchetype.TYPE


def test_archetype_computed_for_camera():
    """A 3D camera layer classifies as THREE_D."""
    layer = LayerModel(
        index=1,
        name="Camera 1",
        layer_kind="camera",
        threeD=True,
        position=[960.0, 540.0, -1000.0],
        scale=[100.0, 100.0, 100.0],
    )
    manifest = _manifest([layer])
    survey_manifest(manifest, comp_width=1920, comp_height=1080)
    assert manifest.layers[0].archetype == LayerArchetype.THREE_D


def test_archetype_never_overwrites_existing_value():
    """If a layer already carries an archetype (e.g. set by a prior
    pass or a re-survey), survey_manifest() must not clobber it —
    same guard shape as the existing source_coverage precedent."""
    layer = LayerModel(
        index=1,
        name="Headline",
        layer_kind="text",
        typographic_info={"text": "Hello", "font_size": 48.0},
        archetype=LayerArchetype.VECTOR_2D,
        position=[960.0, 540.0, 0.0],
        scale=[100.0, 100.0, 100.0],
    )
    manifest = _manifest([layer])
    survey_manifest(manifest, comp_width=1920, comp_height=1080)
    assert manifest.layers[0].archetype == LayerArchetype.VECTOR_2D


def test_archetype_does_not_affect_content_tag_fields():
    """Diagnostic-only: archetype computation must not change
    content_tag / content_tag_source / content_tag_confidence
    resolution for an otherwise-identical layer."""
    layer_with = LayerModel(
        index=1,
        name="BG_Plate",
        layer_kind="av",
        source_coverage=1.0,
        position=[960.0, 540.0, 0.0],
        scale=[100.0, 100.0, 100.0],
    )
    layer_without = LayerModel(
        index=1,
        name="BG_Plate",
        layer_kind="av",
        source_coverage=1.0,
        position=[960.0, 540.0, 0.0],
        scale=[100.0, 100.0, 100.0],
    )
    manifest_with = _manifest([layer_with])
    manifest_without = _manifest([layer_without])
    survey_manifest(manifest_with, comp_width=1920, comp_height=1080)
    survey_manifest(manifest_without, comp_width=1920, comp_height=1080)

    assert manifest_with.layers[0].archetype is not None
    assert (
        manifest_with.layers[0].content_tag
        == manifest_without.layers[0].content_tag
    )
    assert (
        manifest_with.layers[0].content_tag_source
        == manifest_without.layers[0].content_tag_source
    )
