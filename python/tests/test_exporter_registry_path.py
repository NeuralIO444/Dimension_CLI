# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_exporter_registry_path.py — property_registry.json placement contract.

`PayloadSlicer.slice_and_export` emits the property registry alongside
the chunk manifest so ExtendScript consumers read the same dispatch
table Python does ("without a second source of truth", per the comment
at the write site). That sibling relationship is the contract.

The registry path was previously built with a bare
`os.path.abspath("property_registry.json")`, which resolves against the
*current working directory* rather than the slicer's `output_dir`. The
file therefore landed wherever the process happened to be launched
from — in a dev checkout, straight into the working tree as untracked,
non-gitignored debris, and never beside the manifest it belongs to.

These tests deliberately do NOT chdir into the temp directory. Running
from an unrelated CWD is the whole point: it is what tells a path bound
to `output_dir` apart from one bound to the process CWD. A regression
here would pass under a chdir-ing test and still ship the bug.
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

_REGISTRY = "property_registry.json"


def _minimal_conformed_layer(idx=1, name="L"):
    return {
        "index": idx, "name": name, "uid": f"u_{idx}",
        "parent_index": -1, "layer_kind": "av",
        "position": [0, 0, 0], "scale": [100, 100, 100],
        "rotation_z": 0, "anchor": [0, 0, 0],
        "conformed_transforms": {
            "position": [0, 0, 0], "scale": [100, 100, 100],
            "rotation": 0, "anchor": [0, 0, 0],
            "is_root": True,
        },
    }


@pytest.fixture
def exported(tmp_path):
    """Export into tmp_path/Chunks from the ambient CWD, and hand back
    the output dir plus the parsed chunk manifest."""
    out_dir = str(tmp_path / "Chunks")
    slicer = PayloadSlicer(output_dir=out_dir)
    manifest_path = slicer.slice_and_export(
        [_minimal_conformed_layer()],
        expected_comp_name="Root",
        target_width=1080,
        target_height=1920,
        preset_label="TikTok",
    )
    with open(manifest_path) as f:
        return out_dir, json.load(f)


def test_registry_written_into_output_dir(exported):
    """The registry is a sibling of the chunks, as the write site claims."""
    out_dir, _ = exported
    assert os.path.isfile(os.path.join(out_dir, _REGISTRY))


def test_registry_not_written_to_cwd(exported):
    """The regression guard: nothing lands in the process CWD.

    Asserted against the CWD captured during the export rather than a
    hardcoded path, so this holds however pytest was invoked.
    """
    _, _ = exported
    assert not os.path.exists(os.path.join(os.getcwd(), _REGISTRY))


def test_manifest_registry_key_points_at_the_real_file(exported):
    """Consumers resolve the registry through the manifest key, so it
    must name a file that exists and sit inside the output dir."""
    out_dir, data = exported
    recorded = data["property_registry"]
    assert os.path.isfile(recorded)
    assert os.path.dirname(os.path.abspath(recorded)) == os.path.abspath(out_dir)


def test_registry_has_dispatch_table(exported):
    """Sanity: the emitted file carries the versioned property table."""
    out_dir, _ = exported
    with open(os.path.join(out_dir, _REGISTRY)) as f:
        payload = json.load(f)
    assert payload["version"] == 1
    assert payload["properties"]
