# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_exporter_render_queue_options_flag.py — issue #348/#251 narrow slice.

`PayloadSlicer.slice_and_export`'s new `render_queue_options` parameter
must default to None (so every existing caller is byte-identical to
before this change) and must round-trip into the chunk manifest's
`render_queue_options` key exactly, since that's the only channel
Babysitter.jsx has to learn the artist wants render-queue configuration
applied (see Scripts/Dimension_Assets/Babysitter_src/50_workspace.jsx,
which reads `this._state.manifest.render_queue_options`).
"""

from __future__ import annotations

import json
import os
import sys

import pytest

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


def _export(tmp_path, **kwargs):
    out_dir = str(tmp_path / "Chunks")
    slicer = PayloadSlicer(output_dir=out_dir)
    manifest_path = slicer.slice_and_export(
        [_minimal_conformed_layer()],
        expected_comp_name="Root",
        target_width=1080,
        target_height=1920,
        preset_label="OOH",
        **kwargs,
    )
    with open(manifest_path) as f:
        return json.load(f)


def test_defaults_to_none_when_omitted(tmp_path):
    data = _export(tmp_path)
    assert data["render_queue_options"] is None


def test_options_round_trip_exactly(tmp_path):
    options = {
        "template_name": "ProRes 4444",
        "disable_audio": True,
        "file_extension": ".mov",
    }
    data = _export(tmp_path, render_queue_options=options)
    assert data["render_queue_options"] == options


def test_partial_options_round_trip(tmp_path):
    """Only some keys set -- must round-trip exactly as given, no
    silent defaulting/filling on the Python side (Babysitter.jsx's
    configureRenderQueueItem already handles missing keys itself)."""
    options = {"disable_audio": False}
    data = _export(tmp_path, render_queue_options=options)
    assert data["render_queue_options"] == options


@pytest.mark.parametrize("options", [None, {"disable_audio": True}])
def test_does_not_affect_other_manifest_fields(tmp_path, options):
    baseline = _export(tmp_path, render_queue_options=None)
    variant = _export(tmp_path, render_queue_options=options)
    other_keys = set(baseline.keys()) - {"render_queue_options"}
    for key in other_keys:
        assert variant[key] == baseline[key], f"unexpected change in {key!r}"
