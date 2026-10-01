# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_exporter_safe_zone_guide_flag.py — issue #338 narrow slice.

`PayloadSlicer.slice_and_export`'s new `inject_safe_zone_guide` parameter
must default to False (so every existing caller that doesn't pass it is
byte-identical to before this change) and must round-trip into the
chunk manifest's `inject_safe_zone_guide` key exactly, since that's the
only channel Babysitter.jsx has to learn the flag was set (see
Scripts/Dimension_Assets/Babysitter_src/50_workspace.jsx, which reads
`this._state.manifest.inject_safe_zone_guide`).
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
        preset_label="TikTok",
        **kwargs,
    )
    with open(manifest_path) as f:
        return json.load(f)


def test_defaults_to_false_when_omitted(tmp_path):
    """Every existing caller that doesn't pass this parameter must be
    unaffected -- confirms the additive, backward-compatible default."""
    data = _export(tmp_path)
    assert data["inject_safe_zone_guide"] is False


def test_explicit_false_round_trips(tmp_path):
    data = _export(tmp_path, inject_safe_zone_guide=False)
    assert data["inject_safe_zone_guide"] is False


def test_explicit_true_round_trips(tmp_path):
    """This is the ONLY channel Babysitter.jsx has to learn the artist
    wants a guide layer -- if this key doesn't round-trip exactly,
    the feature is silently inert no matter what JSX does with it."""
    data = _export(tmp_path, inject_safe_zone_guide=True)
    assert data["inject_safe_zone_guide"] is True


@pytest.mark.parametrize("value", [True, False])
def test_does_not_affect_other_manifest_fields(tmp_path, value):
    """The flag must be purely additive -- setting it must not perturb
    any other manifest key (chunk_paths, total_layers, etc.)."""
    baseline = _export(tmp_path, inject_safe_zone_guide=False)
    variant = _export(tmp_path, inject_safe_zone_guide=value)
    other_keys = set(baseline.keys()) - {"inject_safe_zone_guide"}
    for key in other_keys:
        assert variant[key] == baseline[key], f"unexpected change in {key!r}"
