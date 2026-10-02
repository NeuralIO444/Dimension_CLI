# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_studio_deck_contracts.py
Unit tests and contract verification for the Unified STUDIO Deck & Ambient Tagging Architecture.
"""

from pathlib import Path


def test_studio_deck_html_and_css_specs():
    """Verify that the CEP assets adhere to the 3-tab Studio Deck contract."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    index_html_path = repo_root / "cep" / "index.html"
    styles_css_path = repo_root / "cep" / "css" / "styles.css"
    main_js_path = repo_root / "cep" / "js" / "main.js"

    assert index_html_path.exists(), "cep/index.html must exist"
    assert styles_css_path.exists(), "cep/css/styles.css must exist"
    assert main_js_path.exists(), "cep/js/main.js must exist"

    html = index_html_path.read_text(encoding="utf-8")
    assert 'id="tab-btn-studio"' in html, "Must contain Studio navigation button"
    assert 'id="tab-btn-dashboard"' in html, "Must contain Dashboard navigation button"
    assert 'id="tab-btn-reports"' in html, "Must contain Reports navigation button"
    # History was merged into Reports (issue #447, 2026-09-07) — both
    # rendered the identical report index with only cosmetic column
    # differences, so this button is gone by design; do not restore it.
    assert 'id="tab-btn-history"' not in html, "History tab was merged into Reports (#447) and must not come back"
    assert 'id="studio-comp-dna-bar"' in html, "Must contain Studio Comp DNA Bar"
    assert 'id="layer-inspector-drawer"' in html, "Must contain Layer Inspector Drawer"


def test_ambient_tagging_data_model_compatibility():
    """Verify that ScrapeManifest and LayerModel support ambient archetype tagging."""
    from models.scrape_manifest import ArtworkBounds, LayerArchetype, LayerModel, ProjectInfo, ScrapeManifest

    l = LayerModel(
        index=1,
        name="Header_Text",
        archetype=LayerArchetype.TYPE,
        artwork_bounds=ArtworkBounds(left=10.0, top=10.0, width=200.0, height=50.0, centroid=[110.0, 35.0]),
        content_tag="TYPE",
        content_tag_source="ambient_heuristics",
        content_tag_confidence=0.98,
    )

    manifest = ScrapeManifest(
        status="OK",
        project_info=ProjectInfo(name="Studio_Comp", width=1920, height=1080),
        layers=[l],
    )

    assert manifest.layers[0].archetype == LayerArchetype.TYPE
    assert manifest.layers[0].content_tag == "TYPE"
    assert manifest.layers[0].content_tag_confidence == 0.98
