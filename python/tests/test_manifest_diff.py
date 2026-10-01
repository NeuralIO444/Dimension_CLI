# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.manifest_diff import (  # noqa: E402
    compare_manifest_dicts,
    material_divergences,
)


def _camera_layer(position, temporal_data, expressions=None):
    return {
        "index": 1,
        "name": "Camera 1",
        "uid": "cam_uid",
        "layer_kind": "camera",
        "position": position,
        "temporal_data": temporal_data,
        "expressions": expressions or {},
    }


def test_playhead_noise_skipped_when_keyframes_match():
    ref = {
        "project_info": {"name": "Test"},
        "layers": [_camera_layer(
            [960, 540, -1000],
            {
                "position_x": {
                    "times": [0.0, 2.0],
                    "values": [960, 1200],
                },
                "position_y": {
                    "times": [0.0, 2.0],
                    "values": [540, 540],
                },
                "position_z": {
                    "times": [0.0, 2.0],
                    "values": [-1000, -800],
                },
            },
        )],
    }
    new = {
        "project_info": {"name": "Test"},
        "layers": [_camera_layer(
            [1200, 540, -800],
            ref["layers"][0]["temporal_data"],
        )],
    }

    divs = compare_manifest_dicts(ref, new)
    noise = [d for d in divs if d.skipped_playhead_noise]
    assert noise, "expected playhead-noise candidates"
    assert material_divergences(divs) == []


def test_real_divergence_still_reported_when_keyframes_differ():
    ref = {
        "project_info": {"name": "Test"},
        "layers": [_camera_layer(
            [960, 540, 0],
            {
                "position_x": {
                    "times": [0.0],
                    "values": [960],
                },
            },
        )],
    }
    new = {
        "project_info": {"name": "Test"},
        "layers": [_camera_layer(
            [1200, 540, 0],
            {
                "position_x": {
                    "times": [0.0],
                    "values": [1200],
                },
            },
        )],
    }

    divs = material_divergences(compare_manifest_dicts(ref, new))
    assert any("position" in d.path for d in divs)


def test_conformed_only_skips_source_carryover_camera_block():
    ref = {
        "layers": [_camera_layer(
            [960, 540, -1000],
            {"position_z": {"times": [0.0], "values": [-1000]}},
        )],
    }
    ref["layers"][0]["camera"] = {
        "pointOfInterest": [960, 540, 0],
        "zoom": 1867.0,
    }
    ref["layers"][0]["conformed_transforms"] = {
        "position": [540, 960, -1778],
        "camera": {"pointOfInterest": [540, 960, 0], "zoom": 3318.0},
    }
    new = {
        "layers": [{
            **ref["layers"][0],
            "camera": {"pointOfInterest": [960, 540, 0], "zoom": 1867.0},
            "conformed_transforms": {
                "position": [540, 960, -1778],
                "camera": {"pointOfInterest": [540, 960, 0], "zoom": 3318.0},
            },
        }],
    }

    divs = material_divergences(
        compare_manifest_dicts(ref, new, conformed_only=True)
    )
    assert divs == []


def test_static_diff_without_keyframes_still_reported():
    ref = {
        "project_info": {"name": "Test"},
        "layers": [_camera_layer([960, 540, 0], {})],
    }
    new = {
        "project_info": {"name": "Test"},
        "layers": [_camera_layer([1000, 540, 0], {})],
    }

    divs = material_divergences(compare_manifest_dicts(ref, new))
    assert any("position" in d.path for d in divs)