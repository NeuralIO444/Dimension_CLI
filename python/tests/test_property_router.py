# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_property_router.py
Tests for core/property_router.py

Covers:
  - matchName → temporal_key mapping for all canonical properties
  - v5 KeyStream → legacy format conversion (interp name → AE int)
  - Separated-dimensions expansion (per_axis → position_x/y/z streams)
  - merge_legacy_and_v5 precedence (v5 wins over legacy)
  - Unknown matchNames are silently skipped (no KeyError)
  - Empty / None inputs produce empty dict, not exceptions
"""

import sys
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from core.property_router import (
    properties_to_temporal_data,
    merge_legacy_and_v5,
    _keystream_to_legacy,
    _interp_to_ae_int,
)


# ── helpers ────────────────────────────────────────────────────────────

def _make_keys(n: int = 3, kind: str = "linear") -> dict:
    """Build a minimal v5 KeyStream dict with n keyframes."""
    return {
        "times":   [float(i) / 24.0 for i in range(n)],
        "values":  [[float(i * 10), float(i * 5), 0.0] for i in range(n)],
        "in_interp":  [kind] * n,
        "out_interp": [kind] * n,
        "temporal_ease_in":  None,
        "temporal_ease_out": None,
        "spatial_tangent_in":  None,
        "spatial_tangent_out": None,
        "is_roving": None,
        "is_hold":   [False] * n,
    }


def _make_record(mn: str, keys=None, separated: bool = False) -> dict:
    return {
        "match_name":   mn,
        "display_name": "",
        "keys":         keys,
        "separated":    separated,
        "per_axis":     None,
        "static":       None,
    }


# ── interp conversion ──────────────────────────────────────────────────

class TestInterpConversion:
    def test_linear_to_6612(self):
        assert _interp_to_ae_int("linear") == 6612

    def test_bezier_to_6613(self):
        assert _interp_to_ae_int("bezier") == 6613

    def test_hold_to_6614(self):
        assert _interp_to_ae_int("hold") == 6614

    def test_unknown_falls_back_to_bezier(self):
        assert _interp_to_ae_int("unknown") == 6613
        assert _interp_to_ae_int("GARBAGE") == 6613

    def test_case_insensitive(self):
        assert _interp_to_ae_int("LINEAR") == 6612
        assert _interp_to_ae_int("Hold") == 6614


# ── keystream conversion ───────────────────────────────────────────────

class TestKeystreamToLegacy:
    def test_basic_conversion(self):
        keys = _make_keys(3, "linear")
        out = _keystream_to_legacy(keys)
        assert out is not None
        assert len(out["times"]) == 3
        assert out["keyInInterpolationType"]  == [6612, 6612, 6612]
        assert out["keyOutInterpolationType"] == [6612, 6612, 6612]

    def test_mixed_interp_types(self):
        keys = _make_keys(2)
        keys["in_interp"]  = ["linear", "hold"]
        keys["out_interp"] = ["bezier", "hold"]
        out = _keystream_to_legacy(keys)
        assert out["keyInInterpolationType"]  == [6612, 6614]
        assert out["keyOutInterpolationType"] == [6613, 6614]

    def test_returns_none_for_empty_times(self):
        assert _keystream_to_legacy({"times": [], "values": []}) is None

    def test_returns_none_for_none_input(self):
        assert _keystream_to_legacy(None) is None

    def test_values_preserved(self):
        keys = _make_keys(2)
        keys["values"] = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
        out = _keystream_to_legacy(keys)
        assert out["values"] == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]


# ── properties_to_temporal_data ────────────────────────────────────────

class TestPropertiesToTemporalData:
    def test_position_maps_correctly(self):
        rec = _make_record("ADBE Position", _make_keys(4))
        out = properties_to_temporal_data([rec])
        assert "position" in out
        assert len(out["position"]["times"]) == 4

    def test_scale_maps_correctly(self):
        rec = _make_record("ADBE Scale", _make_keys(2))
        out = properties_to_temporal_data([rec])
        assert "scale" in out

    def test_anchor_maps_correctly(self):
        rec = _make_record("ADBE Anchor Point", _make_keys(2))
        out = properties_to_temporal_data([rec])
        assert "anchor" in out

    def test_rotation_maps_correctly(self):
        rec = _make_record("ADBE Rotate Z", _make_keys(3))
        out = properties_to_temporal_data([rec])
        assert "rotation" in out

    def test_opacity_maps_correctly(self):
        rec = _make_record("ADBE Opacity", _make_keys(2))
        out = properties_to_temporal_data([rec])
        assert "opacity" in out

    def test_camera_zoom_maps_correctly(self):
        rec = _make_record("ADBE Camera Zoom", _make_keys(3))
        out = properties_to_temporal_data([rec])
        assert "camera_zoom" in out

    def test_camera_focus_distance_maps_correctly(self):
        rec = _make_record("ADBE Camera Focus Distance", _make_keys(2))
        out = properties_to_temporal_data([rec])
        assert "camera_focusDistance" in out

    def test_unknown_match_name_is_skipped(self):
        rec = _make_record("ADBE NonExistent Effect", _make_keys(3))
        out = properties_to_temporal_data([rec])
        assert out == {}

    def test_empty_list_returns_empty_dict(self):
        assert properties_to_temporal_data([]) == {}

    def test_none_input_returns_empty_dict(self):
        assert properties_to_temporal_data(None) == {}

    def test_property_with_no_keys_is_skipped(self):
        rec = _make_record("ADBE Position", keys=None)
        out = properties_to_temporal_data([rec])
        assert out == {}

    def test_multiple_properties_in_one_pass(self):
        recs = [
            _make_record("ADBE Position",    _make_keys(3)),
            _make_record("ADBE Scale",       _make_keys(2)),
            _make_record("ADBE Rotate Z",    _make_keys(5)),
            _make_record("ADBE Opacity",     _make_keys(1)),
        ]
        out = properties_to_temporal_data(recs)
        assert set(out.keys()) == {"position", "scale", "rotation", "opacity"}
        assert len(out["rotation"]["times"]) == 5

    def test_reserved_property_no_keys_emitted(self):
        # ADBE Camera Aperture has temporal_key=None → should not appear in output
        keys = _make_keys(2)
        rec = _make_record("ADBE Camera Aperture", keys)
        out = properties_to_temporal_data([rec])
        # The match_name IS in the table (value None) — so it should be skipped cleanly
        assert out == {}


# ── separated dimensions ───────────────────────────────────────────────

class TestSeparatedDimensions:
    def _make_axis_keys(self, n: int = 3) -> dict:
        """Scalar keystream (per-axis values are floats, not lists)."""
        return {
            "times":   [float(i) / 24.0 for i in range(n)],
            "values":  [float(i * 100) for i in range(n)],
            "in_interp":  ["bezier"] * n,
            "out_interp": ["bezier"] * n,
            "temporal_ease_in": None,
            "temporal_ease_out": None,
            "spatial_tangent_in": None,
            "spatial_tangent_out": None,
            "is_roving": None,
            "is_hold": [False] * n,
        }

    def _make_axis_record(self, mn: str, n: int = 3) -> dict:
        return {
            "match_name":   mn,
            "display_name": "",
            "keys":         self._make_axis_keys(n),
            "separated":    False,
            "per_axis":     None,
            "static":       None,
        }

    def test_separated_position_expands_to_three_streams(self):
        parent = {
            "match_name":   "ADBE Position",
            "display_name": "Position",
            "keys":         None,   # no keys on parent when separated
            "separated":    True,
            "static":       [960.0, 540.0, 0.0],
            "per_axis": {
                "ADBE Position_0": self._make_axis_record("ADBE Position_0", 4),
                "ADBE Position_1": self._make_axis_record("ADBE Position_1", 3),
                "ADBE Position_2": self._make_axis_record("ADBE Position_2", 2),
            },
        }
        out = properties_to_temporal_data([parent])
        assert "position_x" in out
        assert "position_y" in out
        assert "position_z" in out
        assert len(out["position_x"]["times"]) == 4
        assert len(out["position_y"]["times"]) == 3
        assert len(out["position_z"]["times"]) == 2

    def test_separated_parent_does_not_emit_unified_position(self):
        parent = {
            "match_name": "ADBE Position",
            "keys": None,
            "separated": True,
            "static": [960.0, 540.0, 0.0],
            "per_axis": {
                "ADBE Position_0": self._make_axis_record("ADBE Position_0"),
            },
        }
        out = properties_to_temporal_data([parent])
        assert "position" not in out

    def test_separated_with_empty_per_axis_returns_empty(self):
        parent = {
            "match_name": "ADBE Position",
            "keys": None,
            "separated": True,
            "static": [960.0, 540.0, 0.0],
            "per_axis": {},
        }
        out = properties_to_temporal_data([parent])
        assert out == {}

    def test_interp_conversion_in_separated_axes(self):
        parent = {
            "match_name": "ADBE Position",
            "keys": None,
            "separated": True,
            "static": [960.0, 540.0, 0.0],
            "per_axis": {
                "ADBE Position_0": {
                    "match_name": "ADBE Position_0",
                    "keys": {
                        "times": [0.0, 1.0],
                        "values": [0.0, 100.0],
                        "in_interp": ["linear", "hold"],
                        "out_interp": ["bezier", "hold"],
                        "temporal_ease_in": None,
                        "temporal_ease_out": None,
                        "spatial_tangent_in": None,
                        "spatial_tangent_out": None,
                        "is_roving": None,
                        "is_hold": [False, True],
                    },
                    "separated": False,
                    "static": None,
                    "per_axis": None,
                },
            },
        }
        out = properties_to_temporal_data([parent])
        assert out["position_x"]["keyInInterpolationType"]  == [6612, 6614]
        assert out["position_x"]["keyOutInterpolationType"] == [6613, 6614]


# ── merge_legacy_and_v5 ────────────────────────────────────────────────

class TestMergeFunc:
    def test_v5_wins_over_legacy_for_same_key(self):
        legacy = {
            "position": {
                "times": [0.0, 1.0],
                "values": [[100.0, 200.0, 0.0], [200.0, 300.0, 0.0]],
                "keyInInterpolationType": [6612, 6612],
                "keyOutInterpolationType": [6612, 6612],
            }
        }
        v5_props = [_make_record("ADBE Position", {
            "times": [0.0, 0.5, 1.0],
            "values": [[10.0, 20.0, 0.0], [50.0, 60.0, 0.0], [90.0, 100.0, 0.0]],
            "in_interp": ["bezier", "bezier", "bezier"],
            "out_interp": ["bezier", "bezier", "bezier"],
            "temporal_ease_in": None,
            "temporal_ease_out": None,
            "spatial_tangent_in": None,
            "spatial_tangent_out": None,
            "is_roving": None,
            "is_hold": [False, False, False],
        })]
        merged = merge_legacy_and_v5(legacy, v5_props)
        # v5 has 3 keyframes; legacy has 2 → v5 wins
        assert len(merged["position"]["times"]) == 3

    def test_legacy_preserved_when_no_v5_for_that_key(self):
        legacy = {
            "opacity": {
                "times": [0.0, 1.0],
                "values": [100.0, 50.0],
                "keyInInterpolationType": [6612, 6612],
                "keyOutInterpolationType": [6612, 6612],
            }
        }
        # v5 has no opacity property
        v5_props = [_make_record("ADBE Position", _make_keys(2))]
        merged = merge_legacy_and_v5(legacy, v5_props)
        assert "opacity" in merged
        assert "position" in merged

    def test_none_legacy_returns_v5_only(self):
        v5_props = [_make_record("ADBE Scale", _make_keys(3))]
        merged = merge_legacy_and_v5(None, v5_props)
        assert "scale" in merged

    def test_none_v5_returns_legacy_only(self):
        legacy = {"rotation": {"times": [0.0], "values": [45.0],
                               "keyInInterpolationType": None,
                               "keyOutInterpolationType": None}}
        merged = merge_legacy_and_v5(legacy, None)
        assert merged == legacy

    def test_both_none_returns_empty(self):
        assert merge_legacy_and_v5(None, None) == {}

    def test_preserves_camera_keys_from_legacy(self):
        legacy = {
            "camera_zoom": {
                "times": [0.0, 2.0],
                "values": [2000.0, 1800.0],
                "keyInInterpolationType": [6613, 6613],
                "keyOutInterpolationType": [6613, 6613],
            }
        }
        # v5 has no camera zoom property in this fixture
        merged = merge_legacy_and_v5(legacy, [])
        assert "camera_zoom" in merged
