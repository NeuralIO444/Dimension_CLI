# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_surveyor_hero_precomp.py
Unit tests for Dual-Zone Conforming: Precomp Hero / Type X-Ray Guard & Canvas Fill preservation.
"""

from core.surveyor import survey_manifest
from models.scrape_manifest import ScrapeManifest, LayerModel, ProjectInfo, SourceItem


def test_hero_precomp_guard_against_fill():
    """Verify that a precomp with 1.0 coverage but a hero/neon/logo name classifies as CENTER, not FILL."""
    layers = [
        LayerModel(
            index=1,
            name="87N_Reels_DEV_87Neon_HD_10_mc",
            layer_kind="precomp",
            source_item=SourceItem(id=10, name="87N_Reels_DEV_87Neon_HD_10_mc", kind="comp", width=1920, height=1080),
            source_coverage=1.0,
            position=[960.0, 540.0, 0.0],
            scale=[100.0, 100.0, 100.0],
        ),
        LayerModel(
            index=2,
            name="Contract_BW_Grunge_4.jpg",
            layer_kind="av",
            source_item=SourceItem(id=11, name="Contract_BW_Grunge_4.jpg", kind="footage", width=1920, height=1080),
            source_coverage=1.0,
            position=[960.0, 540.0, 0.0],
            scale=[100.0, 100.0, 100.0],
        ),
        LayerModel(
            index=3,
            name="OTHER SIDE Outlines",
            layer_kind="shape",
            source_coverage=0.35,
            position=[960.0, 540.0, 0.0],
            scale=[100.0, 100.0, 100.0],
        )
    ]
    manifest = ScrapeManifest(
        status="OK",
        schema_version="2.0",
        project_info=ProjectInfo(name="TestComp", width=1920, height=1080, fps=24.0),
        layers=layers
    )

    summary = survey_manifest(manifest, comp_width=1920, comp_height=1080)

    # 87N Neon Precomp MUST be CENTER (Hero Safe Zone), NOT FILL
    assert manifest.layers[0].content_tag in ("CENTER", "HERO")
    assert manifest.layers[0].content_tag != "FILL"

    # Background Grunge plate MUST be FILL (Canvas Zone)
    assert manifest.layers[1].content_tag in ("FILL", "BG", "BACKGROUND")

    # Text outlines MUST be TOP / TYPE / CENTER
    assert manifest.layers[2].content_tag in ("TOP", "TYPE", "CENTER")
