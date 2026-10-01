# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_report_dashboard_blocks.py — report-dashboard-v1 Item 1.

The in-panel Report Dashboard (docs/design/REPORT-DASHBOARD-UI-SPEC.md)
needs the placement-unit partition and run warnings that today only exist
baked into the report's HTML ("How Dimension Read This Comp" section,
Run Warnings card). This asserts the two ADDITIVE JSON blocks
`generate_report()` now writes to the report's JSON sidecar and embedded
<script> blob:

  - `units[]`   — kind, label, member layer refs (name/uid/index/comp_id),
                  read/did verdict strings.
  - `run_warnings[]` — flat list of warning strings.

Neither block replaces or renames any existing JSON field (see
test_report_layer_disambiguation.py for the pre-existing fields' coverage).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from logic.report_generator import generate_report  # noqa: E402


def _base_inputs() -> Dict[str, Any]:
    return {
        "conformed_manifest": {
            "layers": [
                {
                    "index": 1,
                    "name": "Main Camera",
                    "containing_comp_id": None,
                    "uid": "cam0001",
                    "conformed_transforms": {"position": [0, 0, 0], "scale": [100, 100, 100]},
                },
                {
                    "index": 2,
                    "name": "Graphic_Overlay",
                    "containing_comp_id": None,
                    "uid": "gfx0002",
                    "conformed_transforms": {"position": [0, 0, 0], "scale": [100, 100, 100]},
                },
                {
                    # No uid key — legacy pre-stamp layer inside a precomp.
                    "index": 1,
                    "name": "Inner Text",
                    "containing_comp_id": 600123,
                    "conformed_transforms": {"position": [0, 0, 0], "scale": [100, 100, 100]},
                },
            ],
            "warnings": {"collapsed_layers": []},
            "aspect_strategy": "narrow",
        },
        "scrape_manifest": {
            "project_info": {"name": "CH02_Intro_v04", "width": 1920, "height": 1080},
        },
        "preset_label": "TikTok",
        "target_w": 1080,
        "target_h": 1920,
        "scale_mode": "Fill",
        "uniform_scale": 1.8667,
        "session_id": "sess-units-001",
    }


def _placement_units_fixture() -> Dict[str, Any]:
    return {
        "units": [
            {"kind": "root_frame", "label": "Root frame"},
            {
                "unit_id": "u1",
                "kind": "camera_scene",
                "label": "Main Camera",
                "members": [
                    {"comp_id": None, "index": 1, "name": "Main Camera", "tag": None},
                ],
                "anchor_auto": "preserve",
                "anchor_resolved": "preserved",
            },
            {
                "unit_id": "u2",
                "kind": "precomp",
                "label": "Graphic_Overlay",
                "members": [
                    {"comp_id": None, "index": 2, "name": "Graphic_Overlay", "tag": "SUP"},
                    {"comp_id": 600123, "index": 1, "name": "Inner Text", "tag": "TT"},
                ],
                "anchor_auto": "sealed",
                "anchor_resolved": "degraded (overlaps CUTOFF)",
            },
        ],
        "root_unit_id": "root",
        "summary": "2 units",
    }


class TestUnitsJsonBlock:
    def test_units_block_present_with_members_and_uid(self, tmp_path: Path):
        report_path = tmp_path / "conform_report.html"
        out = generate_report(
            output_path=str(report_path),
            placement_units=_placement_units_fixture(),
            **_base_inputs(),
        )
        sidecar = Path(out).with_suffix(".json")
        payload = json.loads(sidecar.read_text(encoding="utf-8"))

        assert "units" in payload
        units = payload["units"]
        # root_frame is excluded, same as the HTML section's behavior.
        assert len(units) == 2

        camera_unit = next(u for u in units if u["kind"] == "camera_scene")
        assert camera_unit["label"] == "Main Camera"
        assert camera_unit["read"] == "preserve"
        assert camera_unit["did"] == "preserved"
        assert camera_unit["members"] == [
            {"name": "Main Camera", "uid": "cam0001", "index": 1, "comp_id": None},
        ]

        precomp_unit = next(u for u in units if u["kind"] == "precomp")
        assert precomp_unit["did"] == "degraded (overlaps CUTOFF)"
        by_name = {m["name"]: m for m in precomp_unit["members"]}
        # uid resolved via the comp-scoped (comp_id, index) lookup —
        # NOT a flat index lookup (would collide: index 1 exists both
        # at comp_id=None and comp_id=600123).
        assert by_name["Graphic_Overlay"]["uid"] == "gfx0002"
        assert by_name["Graphic_Overlay"]["comp_id"] is None
        assert by_name["Inner Text"]["uid"] is None  # legacy unstamped layer
        assert by_name["Inner Text"]["comp_id"] == 600123
        assert by_name["Inner Text"]["index"] == 1

    def test_units_block_empty_list_when_no_placement_units(self, tmp_path: Path):
        report_path = tmp_path / "conform_report.html"
        out = generate_report(output_path=str(report_path), **_base_inputs())
        sidecar = Path(out).with_suffix(".json")
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        assert payload["units"] == []

    def test_units_block_embedded_in_html_script_blob(self, tmp_path: Path):
        report_path = tmp_path / "conform_report.html"
        out = generate_report(
            output_path=str(report_path),
            placement_units=_placement_units_fixture(),
            **_base_inputs(),
        )
        html = Path(out).read_text(encoding="utf-8")
        assert '"units":' in html
        assert '"degraded (overlaps CUTOFF)"' in html

    def test_existing_fields_untouched(self, tmp_path: Path):
        """Additive means additive — meta/layers keep their exact shape."""
        report_path = tmp_path / "conform_report.html"
        out = generate_report(
            output_path=str(report_path),
            placement_units=_placement_units_fixture(),
            **_base_inputs(),
        )
        sidecar = Path(out).with_suffix(".json")
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        assert payload["meta"]["session_id"] == "sess-units-001"
        assert payload["meta"]["target_w"] == 1080
        assert len(payload["layers"]) == 3


class TestRunWarningsJsonBlock:
    def test_run_warnings_block_collects_survey_and_runtime_warnings(self, tmp_path: Path):
        inputs = _base_inputs()
        inputs["scrape_manifest"] = dict(inputs["scrape_manifest"])
        inputs["scrape_manifest"]["survey_warnings"] = ["Survey degraded on layer X"]

        report_path = tmp_path / "conform_report.html"
        out = generate_report(
            output_path=str(report_path),
            run_warnings=["SOE skipped — mask missing"],
            **inputs,
        )
        sidecar = Path(out).with_suffix(".json")
        payload = json.loads(sidecar.read_text(encoding="utf-8"))

        assert "run_warnings" in payload
        assert "Survey degraded on layer X" in payload["run_warnings"]
        assert "SOE skipped — mask missing" in payload["run_warnings"]

    def test_run_warnings_empty_list_on_clean_run(self, tmp_path: Path):
        report_path = tmp_path / "conform_report.html"
        out = generate_report(output_path=str(report_path), **_base_inputs())
        sidecar = Path(out).with_suffix(".json")
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        assert payload["run_warnings"] == []

    def test_run_warnings_matches_html_run_warnings_section(self, tmp_path: Path):
        """Sanity check: the JSON block and the HTML card render the same
        underlying warning text — no divergent code path snuck in."""
        report_path = tmp_path / "conform_report.html"
        out = generate_report(
            output_path=str(report_path),
            run_warnings=["Camera depth axis ambiguous — used default K"],
            **_base_inputs(),
        )
        html = Path(out).read_text(encoding="utf-8")
        sidecar = Path(out).with_suffix(".json")
        payload = json.loads(sidecar.read_text(encoding="utf-8"))

        assert "Camera depth axis ambiguous — used default K" in html
        assert "Camera depth axis ambiguous — used default K" in payload["run_warnings"]
