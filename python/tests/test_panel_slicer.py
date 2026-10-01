# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_panel_slicer.py
TASK-P2-01 (#249) — coverage for PanelSlicingPlanner: ADR 01 crop-window
math, master-layer offset math, physical-gap zone math, naming, and the
Comp Count Invariant.

The Market 15 Liveboard (config/ooh_specs.json OOH-064/OOH-066,
N=3, panel_width=1080, panel_height=1920, gap_px=221) is the worked
example in docs/roadmap/presets-2-0-pre-coding-rigor-2026-08-28.md
§5.1 — every number asserted against it below (W_total=3682, panel
offsets 540 / -761 / -2062, gap zones [1080..1301] and [2381..2602])
comes directly from that ADR, not from this test's own invention.
"""

from __future__ import annotations

import json
import os

import pytest
from pydantic import ValidationError

from core.panel_slicer import (
    GapZone,
    MultiPanelSpec,
    PanelSlicingPlan,
    PanelSlicingPlanner,
    PanelSpec,
    synthesize_gap_zone_mask,
)


_OOH_SPECS_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "ooh_specs.json"))


def _market_15_spec() -> MultiPanelSpec:
    return MultiPanelSpec(
        panel_count=3, panel_width=1080, panel_height=1920, gap_px=221,
        split_naming=["LEFT", "CENTER", "RIGHT"],
    )


# ── MultiPanelSpec validation ───────────────────────────────────────


def test_valid_spec_constructs():
    spec = _market_15_spec()
    assert spec.panel_count == 3
    assert spec.split_naming == ["LEFT", "CENTER", "RIGHT"]


def test_panel_count_below_two_rejected():
    with pytest.raises(ValidationError):
        MultiPanelSpec(panel_count=1, panel_width=1080, panel_height=1920, gap_px=221)


def test_non_positive_panel_dimensions_rejected():
    with pytest.raises(ValidationError):
        MultiPanelSpec(panel_count=3, panel_width=0, panel_height=1920, gap_px=221)
    with pytest.raises(ValidationError):
        MultiPanelSpec(panel_count=3, panel_width=1080, panel_height=-1, gap_px=221)


def test_negative_gap_rejected():
    with pytest.raises(ValidationError):
        MultiPanelSpec(panel_count=3, panel_width=1080, panel_height=1920, gap_px=-1)


def test_zero_gap_allowed():
    spec = MultiPanelSpec(panel_count=2, panel_width=500, panel_height=800, gap_px=0)
    assert spec.gap_px == 0


def test_split_naming_length_mismatch_rejected():
    with pytest.raises(ValidationError):
        MultiPanelSpec(
            panel_count=3, panel_width=1080, panel_height=1920, gap_px=221,
            split_naming=["LEFT", "RIGHT"],
        )


# ── ADR 01 math — Market 15 Liveboard worked example ────────────────


def test_master_canvas_dimensions():
    planner = PanelSlicingPlanner(_market_15_spec(), base_name="CAMPAIGN_LIVEBOARD")
    assert planner.master_width == 3682
    assert planner.master_height == 1920


def test_crop_windows_match_adr():
    planner = PanelSlicingPlanner(_market_15_spec(), base_name="CAMPAIGN_LIVEBOARD")
    assert planner.crop_window(0) == (0, 1080)
    assert planner.crop_window(1) == (1301, 2381)
    assert planner.crop_window(2) == (2602, 3682)


def test_master_layer_positions_match_adr():
    planner = PanelSlicingPlanner(_market_15_spec(), base_name="CAMPAIGN_LIVEBOARD")
    assert planner.master_layer_position(0) == (540.0, 960.0)
    assert planner.master_layer_position(1) == (-761.0, 960.0)
    assert planner.master_layer_position(2) == (-2062.0, 960.0)


def test_gap_zones_match_adr():
    planner = PanelSlicingPlanner(_market_15_spec(), base_name="CAMPAIGN_LIVEBOARD")
    assert planner.gap_window(0) == (1080, 1301)
    assert planner.gap_window(1) == (2381, 2602)


def test_crop_window_out_of_range_raises():
    planner = PanelSlicingPlanner(_market_15_spec(), base_name="CAMPAIGN_LIVEBOARD")
    with pytest.raises(ValueError):
        planner.crop_window(3)
    with pytest.raises(ValueError):
        planner.crop_window(-1)


def test_gap_window_out_of_range_raises():
    planner = PanelSlicingPlanner(_market_15_spec(), base_name="CAMPAIGN_LIVEBOARD")
    with pytest.raises(ValueError):
        planner.gap_window(2)  # only gaps 0, 1 exist for 3 panels
    with pytest.raises(ValueError):
        planner.gap_window(-1)


# ── Naming ───────────────────────────────────────────────────────────


def test_panel_name_uses_explicit_split_naming():
    planner = PanelSlicingPlanner(_market_15_spec(), base_name="CAMPAIGN_LIVEBOARD")
    assert planner.panel_name(0) == "LEFT"
    assert planner.panel_name(1) == "CENTER"
    assert planner.panel_name(2) == "RIGHT"


def test_panel_name_defaults_to_left_center_right_for_three_panels():
    spec = MultiPanelSpec(panel_count=3, panel_width=1080, panel_height=1920, gap_px=221)
    planner = PanelSlicingPlanner(spec, base_name="X")
    assert [planner.panel_name(k) for k in range(3)] == ["LEFT", "CENTER", "RIGHT"]


def test_panel_name_falls_back_to_generic_for_non_triptych_counts():
    spec = MultiPanelSpec(panel_count=2, panel_width=500, panel_height=800, gap_px=50)
    planner = PanelSlicingPlanner(spec, base_name="X")
    assert planner.panel_name(0) == "PANEL_0"
    assert planner.panel_name(1) == "PANEL_1"


def test_panel_name_out_of_range_raises():
    planner = PanelSlicingPlanner(_market_15_spec(), base_name="X")
    with pytest.raises(ValueError):
        planner.panel_name(3)


def test_empty_base_name_rejected():
    with pytest.raises(ValueError):
        PanelSlicingPlanner(_market_15_spec(), base_name="")


# ── Full plan assembly ───────────────────────────────────────────────


def test_plan_produces_correct_comp_names():
    plan = PanelSlicingPlanner(_market_15_spec(), base_name="CAMPAIGN_LIVEBOARD").plan()
    assert isinstance(plan, PanelSlicingPlan)
    assert plan.master_comp_name == "CAMPAIGN_LIVEBOARD_MASTER_3682x1920"
    assert [p.comp_name for p in plan.panels] == [
        "CAMPAIGN_LIVEBOARD_LEFT_1080x1920",
        "CAMPAIGN_LIVEBOARD_CENTER_1080x1920",
        "CAMPAIGN_LIVEBOARD_RIGHT_1080x1920",
    ]


def test_plan_panel_indices_are_ordered():
    plan = PanelSlicingPlanner(_market_15_spec(), base_name="X").plan()
    assert [p.index for p in plan.panels] == [0, 1, 2]
    for panel in plan.panels:
        assert isinstance(panel, PanelSpec)
        assert panel.width == 1080
        assert panel.height == 1920


def test_plan_gap_zones_named_and_flagged_non_rendering():
    plan = PanelSlicingPlanner(_market_15_spec(), base_name="X").plan()
    assert len(plan.gap_zones) == 2
    assert [g.name for g in plan.gap_zones] == ["GAP_PILLAR_01", "GAP_PILLAR_02"]
    assert all(isinstance(g, GapZone) and g.guide_layer is True for g in plan.gap_zones)
    assert [(g.x_start, g.x_end) for g in plan.gap_zones] == [(1080, 1301), (2381, 2602)]


def test_plan_zero_gap_emits_no_gap_zones():
    spec = MultiPanelSpec(panel_count=2, panel_width=500, panel_height=800, gap_px=0)
    plan = PanelSlicingPlanner(spec, base_name="X").plan()
    assert plan.gap_zones == []
    # Contiguous panels: master width equals the sum of panel widths.
    assert plan.master_width == 1000
    assert plan.panels[0].crop_x_end == plan.panels[1].crop_x_start == 500


def test_plan_comp_count_invariant():
    """Anti-Shatter Threat Model §6.3: 1 Master Comp + N children,
    no more, no fewer."""
    plan = PanelSlicingPlanner(_market_15_spec(), base_name="X").plan()
    assert plan.item_count == 4
    assert plan.item_count == 1 + len(plan.panels)


def test_plan_metadata_fields_mirror_spec():
    plan = PanelSlicingPlanner(_market_15_spec(), base_name="X").plan()
    assert plan.panel_count == 3
    assert plan.panel_width == 1080
    assert plan.panel_height == 1920
    assert plan.gap_px == 221


# ── Generalization beyond N=3 (two-panel diptych) ───────────────────


def test_two_panel_diptych_math():
    spec = MultiPanelSpec(panel_count=2, panel_width=500, panel_height=800, gap_px=100)
    planner = PanelSlicingPlanner(spec, base_name="X")
    assert planner.master_width == 1100  # 2*500 + 1*100
    assert planner.crop_window(0) == (0, 500)
    assert planner.crop_window(1) == (600, 1100)
    assert planner.master_layer_position(0) == (250.0, 400.0)
    assert planner.master_layer_position(1) == (-350.0, 400.0)
    assert planner.gap_window(0) == (500, 600)


# ── from_metadata() against the real production dataset ─────────────
# Per CLAUDE.md's anti-pattern on trusting only synthetic fixtures for
# contract-adjacent code: this reads the actual multi_panel dict from
# config/ooh_specs.json rather than re-typing it, so a future edit to
# the shipped OOH dataset that breaks this contract fails here instead
# of silently drifting.


def _load_market_15_metadata() -> dict:
    with open(_OOH_SPECS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    spec = next(
        s for s in data["production_specs"]
        if s.get("category") == "transit_triptychs" and s.get("width_px") == 3682
    )
    return spec["multi_panel"]


def test_from_metadata_matches_real_ooh_specs_dataset():
    metadata = _load_market_15_metadata()
    planner = PanelSlicingPlanner.from_metadata(metadata, base_name="CAMPAIGN_LIVEBOARD")
    plan = planner.plan()
    assert plan.master_width == 3682
    assert plan.master_height == 1920
    assert plan.panel_count == 3
    assert [p.name for p in plan.panels] == ["LEFT", "CENTER", "RIGHT"]
    assert plan.item_count == 4


def test_panel_spatial_tokens_and_integer_gap_sum():
    """TASK-SUB-05 (#280): Verify spatial tokens and exact integer sum."""
    metadata = _load_market_15_metadata()
    planner = PanelSlicingPlanner.from_metadata(metadata, base_name="CAMPAIGN_LIVEBOARD")
    plan = planner.plan()

    # Verify spatial tokens
    assert [p.spatial_token for p in plan.panels] == [
        "Panel01_LEFT",
        "Panel02_CENTER",
        "Panel03_RIGHT",
    ]

    # Verify integer sum invariant: 3 panels of 1080 + 2 gaps of 221 == 3682
    total_w = sum(p.width for p in plan.panels) + sum(g.width for g in plan.gap_zones)
    assert total_w == plan.master_width == 3682


# ── synthesize_gap_zone_mask (issue #346, SOE half) ─────────────────


def test_gap_zone_mask_shape_matches_master_dims():
    planner = PanelSlicingPlanner(_market_15_spec(), base_name="CAMPAIGN_LIVEBOARD")
    plan = planner.plan()
    mask = synthesize_gap_zone_mask(plan)
    assert mask.shape == (1920, 3682)
    assert mask.dtype.name == "uint8"


def test_gap_zone_mask_cutoff_at_adr_worked_example_coordinates():
    """Same N=3/Wp=1080/Hp=1920/Gp=221 worked example as the rest of this
    file: gap zones [1080..1301] and [2381..2602] must be CUTOFF (0),
    everywhere else GO (255)."""
    planner = PanelSlicingPlanner(_market_15_spec(), base_name="CAMPAIGN_LIVEBOARD")
    plan = planner.plan()
    mask = synthesize_gap_zone_mask(plan)

    assert (mask[:, 1080:1301] == 0).all()
    assert (mask[:, 2381:2602] == 0).all()

    # A single column just outside each gap, and the panel interiors,
    # must all still be GO -- proves the CUTOFF strip doesn't bleed.
    assert (mask[:, 1079] == 255).all()
    assert (mask[:, 1301] == 255).all()
    assert (mask[:, 2380] == 255).all()
    assert (mask[:, 2602] == 255).all()
    assert (mask[:, 0] == 255).all()
    assert (mask[:, 3681] == 255).all()


def test_gap_zone_mask_all_go_when_gap_px_is_zero():
    spec = MultiPanelSpec(panel_count=2, panel_width=1000, panel_height=500, gap_px=0)
    plan = PanelSlicingPlanner(spec, base_name="CONTIGUOUS").plan()
    assert plan.gap_zones == []
    mask = synthesize_gap_zone_mask(plan)
    assert mask.shape == (500, 2000)
    assert (mask == 255).all()
