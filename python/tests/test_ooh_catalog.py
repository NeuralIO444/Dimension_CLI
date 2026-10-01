# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).

import os
import json
from data.target_catalog import OOH
from logic.target_store import TargetStoreManager


def test_ooh_specs_json_exists():
    specs_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "config", "ooh_specs.json"))
    assert os.path.exists(specs_path)
    with open(specs_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["schema_version"] == "1.0"
    assert len(data["production_specs"]) > 0


def test_ooh_catalog_loaded_into_builtin_targets():
    assert len(OOH) > 0
    assert all(t.channel == "ooh" for t in OOH)
    assert all(t.source == "builtin" for t in OOH)
    assert all(t.category == "custom_signage" for t in OOH)


def test_ooh_subcategories_mapped():
    subcats = {t.subcategory for t in OOH}
    expected = {
        "stadium_ribbons",
        "urban_spectaculars",
        "transit_triptychs",
        "portrait_towers",
        "horizontal_billboards"
    }
    assert expected.issubset(subcats)


def test_ooh_technical_metadata_preserved():
    # Find stadium ribbon (extreme aspect ratio)
    ribbon = next(t for t in OOH if t.subcategory == "stadium_ribbons" and t.width == 16960)
    assert ribbon.height == 90
    assert ribbon.fps == 60.0
    assert ribbon.metadata.get("video_codec") == "Apple ProRes 422 MOV"
    assert ribbon.metadata.get("audio") is False

    # Find triptych (multi-panel gap planning)
    triptych = next(t for t in OOH if t.subcategory == "transit_triptychs" and t.width == 3682)
    assert triptych.height == 1920
    multi_panel = triptych.metadata.get("multi_panel")
    assert multi_panel is not None
    assert multi_panel["panel_count"] == 3
    assert multi_panel["gap_px"] == 221


def test_target_store_manager_includes_ooh():
    tsm = TargetStoreManager()
    all_targets = tsm.all_targets()
    ooh_targets = [t for t in all_targets if t.channel == "ooh"]
    assert len(ooh_targets) == len(OOH)
