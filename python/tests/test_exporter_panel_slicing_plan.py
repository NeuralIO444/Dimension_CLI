# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_exporter_panel_slicing_plan.py — issue #346 narrow slice.

`PayloadSlicer.slice_and_export`'s new `panel_slicing_plan` parameter
must default to None (so every existing caller that doesn't pass it is
byte-identical to before this change) and must round-trip into the
chunk manifest's `panel_slicing_plan` key exactly. No Babysitter/JSX
code reads this key yet -- see stages.conform.plan_panel_slicing's
docstring -- so this only proves the manifest-facing data contract,
not any AE behavior. Mirrors test_exporter_safe_zone_guide_flag.py's
shape exactly (#338's identical threading pattern).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
))

from logic.exporter import PayloadSlicer  # noqa: E402


def _minimal_conformed_layer():
    return {
        "index": 1, "name": "L", "uid": "u_1",
        "parent_index": -1, "layer_kind": "av",
        "position": [0, 0, 0], "scale": [100, 100, 100],
        "rotation_z": 0, "anchor": [0, 0, 0],
        "conformed_transforms": {
            "position": [0, 0, 0], "scale": [100, 100, 100],
            "rotation": 0, "anchor": [0, 0, 0],
            "is_root": True,
        },
    }


_SAMPLE_PLAN = {
    "master_comp_name": "OOH-064_MASTER_3682x1920",
    "master_width": 3682, "master_height": 1920,
    "panel_count": 3, "panel_width": 1080, "panel_height": 1920, "gap_px": 221,
    "panels": [], "gap_zones": [], "item_count": 4,
}


def _export(tmp_path, **kwargs):
    out_dir = str(tmp_path / "Chunks")
    slicer = PayloadSlicer(output_dir=out_dir)
    manifest_path = slicer.slice_and_export(
        [_minimal_conformed_layer()],
        expected_comp_name="Root",
        target_width=1080,
        target_height=1920,
        preset_label="TikTok",
        **kwargs,
    )
    with open(manifest_path) as f:
        return json.load(f)


def test_defaults_to_none_when_omitted(tmp_path):
    """Every existing caller that doesn't pass this parameter (263 of
    265 catalog targets, all non-OOH targets) must be unaffected --
    confirms the additive, backward-compatible default."""
    data = _export(tmp_path)
    assert data["panel_slicing_plan"] is None


def test_explicit_none_round_trips(tmp_path):
    data = _export(tmp_path, panel_slicing_plan=None)
    assert data["panel_slicing_plan"] is None


def test_plan_dict_round_trips_exactly(tmp_path):
    data = _export(tmp_path, panel_slicing_plan=_SAMPLE_PLAN)
    assert data["panel_slicing_plan"] == _SAMPLE_PLAN


def test_does_not_affect_other_manifest_fields(tmp_path):
    """The plan must be purely additive -- setting it must not perturb
    any other manifest key (chunk_paths, total_layers, etc.)."""
    baseline = _export(tmp_path, panel_slicing_plan=None)
    variant = _export(tmp_path, panel_slicing_plan=_SAMPLE_PLAN)
    other_keys = set(baseline.keys()) - {"panel_slicing_plan"}
    for key in other_keys:
        assert variant[key] == baseline[key], f"unexpected change in {key!r}"
