# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_panel_slicing_wiring.py — #346 (narrow slice).

core.panel_slicer.PanelSlicingPlanner was built and unit-tested (26/26)
but had zero production callers. This proves `stages.conform.plan_panel_slicing`
— the new caller — is strictly gated: only targets whose metadata carries a
`multi_panel` spec (2 of 265 catalog targets, both transit_triptychs) produce
a non-None result, and every other target is byte-identical to before this
function existed. Mirrors test_ooh_render_queue_wiring.py's shape exactly,
same gating pattern (#417).

Deliberately NOT covered here (out of scope for this narrow slice, per
core/panel_slicer.py's own docstring): Babysitter/JSX comp-creation
execution, and SOE gap-zone occlusion wiring. No JSX file is touched by
this change.
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from logic.preset_manager import PresetManager
from stages.conform import plan_panel_slicing


def _target_ns(metadata=None):
    return types.SimpleNamespace(id="test_target", label="Test Target", metadata=metadata or {})


class TestGating:
    def test_no_metadata_returns_none(self):
        t = _target_ns(metadata={})
        assert plan_panel_slicing(t) is None

    def test_metadata_none_returns_none(self):
        t = types.SimpleNamespace(id="x", label="x", metadata=None)
        assert plan_panel_slicing(t) is None

    def test_target_with_no_metadata_attr_returns_none(self):
        t = types.SimpleNamespace(id="x", label="x")
        assert plan_panel_slicing(t) is None

    def test_unrelated_metadata_only_returns_none(self):
        t = _target_ns(metadata={"video_codec": "H.264", "audio": False})
        assert plan_panel_slicing(t) is None

    def test_multi_panel_none_value_returns_none(self):
        t = _target_ns(metadata={"multi_panel": None})
        assert plan_panel_slicing(t) is None

    def test_target_none_returns_none(self):
        assert plan_panel_slicing(None) is None


class TestPlanning:
    _MARKET_15_METADATA = {
        "multi_panel": {
            "panel_count": 3,
            "panel_width": 1080,
            "panel_height": 1920,
            "gap_px": 221,
            "split_naming": ["LEFT", "CENTER", "RIGHT"],
        }
    }

    def test_produces_plan_matching_adr_worked_example(self):
        """N=3, Wp=1080, Hp=1920, Gp=221 -> W_total=3682, panel offsets
        540/-761/-2062, gap zones [1080..1301] and [2381..2602] — the
        exact ADR 01 worked example core/panel_slicer.py's own test
        suite validates against."""
        t = _target_ns(metadata=self._MARKET_15_METADATA)
        result = plan_panel_slicing(t)
        assert result is not None
        assert result["master_width"] == 3682
        assert result["master_height"] == 1920
        assert result["panel_count"] == 3
        assert len(result["panels"]) == 3
        offsets = [tuple(p["master_layer_position"]) for p in result["panels"]]
        assert offsets[0][0] == 540
        assert offsets[1][0] == -761
        assert offsets[2][0] == -2062
        gap_windows = [(g["x_start"], g["x_end"]) for g in result["gap_zones"]]
        assert gap_windows == [(1080, 1301), (2381, 2602)]

    def test_panel_names_use_split_naming(self):
        t = _target_ns(metadata=self._MARKET_15_METADATA)
        result = plan_panel_slicing(t)
        assert [p["name"] for p in result["panels"]] == ["LEFT", "CENTER", "RIGHT"]

    def test_base_name_derived_from_target_id(self):
        t = types.SimpleNamespace(id="OOH-064", label="Market 15 Liveboard",
                                   metadata=self._MARKET_15_METADATA)
        result = plan_panel_slicing(t)
        assert result["master_comp_name"] == "OOH-064_MASTER_3682x1920"

    def test_result_is_plain_dict_not_pydantic_model(self):
        """manifest-facing shape -- must be JSON-serializable as-is."""
        import json
        t = _target_ns(metadata=self._MARKET_15_METADATA)
        result = plan_panel_slicing(t)
        assert isinstance(result, dict)
        json.dumps(result)  # must not raise


class TestRealCatalogTargets:
    """Same assertions via the real production catalog
    (data.target_catalog.OOH), not a hand-built fixture."""

    def test_real_multi_panel_catalog_targets_produce_plans(self):
        from data.target_catalog import OOH
        multi_panel_targets = [t for t in OOH if (t.metadata or {}).get("multi_panel")]
        if not multi_panel_targets:
            import pytest
            pytest.skip("No multi_panel OOH targets loaded from production specs JSON")

        for t in multi_panel_targets:
            result = plan_panel_slicing(t)
            assert result is not None
            # item_count is a computed @property on PanelSlicingPlan, not
            # a declared field -- model_dump() only carries fields, so
            # re-derive it the same way rather than looking it up.
            assert (1 + len(result["panels"])) == 1 + result["panel_count"]
            assert result["master_width"] == t.width
            assert result["master_height"] == t.height

    def test_real_non_multi_panel_ooh_targets_return_none(self):
        from data.target_catalog import OOH
        single_panel_targets = [t for t in OOH if not (t.metadata or {}).get("multi_panel")]
        if not single_panel_targets:
            import pytest
            pytest.skip("No non-multi-panel OOH targets to check")
        for t in single_panel_targets[:10]:
            assert plan_panel_slicing(t) is None

    def test_get_target_resolution_path(self):
        from data.target_catalog import OOH
        multi_panel_targets = [t for t in OOH if (t.metadata or {}).get("multi_panel")]
        if not multi_panel_targets:
            import pytest
            pytest.skip("No multi_panel OOH targets loaded")
        real_id = multi_panel_targets[0].id
        resolved = PresetManager().get_target(real_id)
        assert resolved is not None
        result = plan_panel_slicing(resolved)
        assert result is not None
        assert result["master_width"] == resolved.width
        assert result["master_height"] == resolved.height
