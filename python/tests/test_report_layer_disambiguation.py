# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_report_layer_disambiguation.py
2026-06-30 — `layer.index` is only unique per-comp, not across the
flattened layer list a conform report renders (a precomp's layer 1 and
the root comp's layer 1 share the same number). Both the per-layer
HTML table and the embedded JSON/CSV export blob carry
`containing_comp_id` now so layers sharing an index are distinguishable
without changing the visible "#" numbering artists already rely on as
an AE layer-panel cross-reference.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from logic.report_generator import generate_report  # noqa: E402


def _inputs_with_layers() -> Dict[str, Any]:
    return {
        "conformed_manifest": {
            "layers": [
                {
                    "index": 1,
                    "name": "Root Layer",
                    "containing_comp_id": None,
                    "uid": "a1b2c3d4",
                    "conformed_transforms": {"position": [0, 0, 0], "scale": [100, 100, 100]},
                },
                {
                    # No uid key at all — legacy pre-stamp manifest layer.
                    "index": 1,
                    "name": "Precomp Layer",
                    "containing_comp_id": 600123,
                    "conformed_transforms": {"position": [0, 0, 0], "scale": [100, 100, 100]},
                },
            ],
            "warnings": {"collapsed_layers": []},
            "aspect_strategy": "fit",
        },
        "scrape_manifest": {
            "project_info": {"name": "TestComp", "width": 1920, "height": 1080},
        },
        "preset_label": "TikTok",
        "target_w": 1080,
        "target_h": 1920,
        "scale_mode": "Fit",
        "uniform_scale": 0.5625,
        "session_id": "test123",
    }


class TestLayerIndexDisambiguation:
    def test_html_table_carries_comp_id_tooltip(self, tmp_path: Path):
        report_path = tmp_path / "conform_report.html"
        out = generate_report(output_path=str(report_path), **_inputs_with_layers())
        html = Path(out).read_text(encoding="utf-8")

        assert 'title="comp: root"' in html, (
            "Root-comp layer (containing_comp_id=None) should show 'root'"
        )
        assert 'title="comp: 600123"' in html, (
            "Precomp layer should show its numeric containing_comp_id"
        )

    def test_json_export_carries_containing_comp_id(self, tmp_path: Path):
        report_path = tmp_path / "conform_report.html"
        out = generate_report(output_path=str(report_path), **_inputs_with_layers())
        html = Path(out).read_text(encoding="utf-8")

        # json.dumps default separators use ": " — embedded verbatim in
        # the report's <script> tag (see generate_report's
        # report_data_json assembly).
        assert '"containing_comp_id": null' in html
        assert '"containing_comp_id": 600123' in html

    def test_json_rows_carry_uid_none_safe(self, tmp_path: Path):
        """Every JSON row exports "uid" — the layer's stable identity when
        stamped, null for legacy unstamped layers. Unblocks dashboard
        click-to-select (REPORT-DASHBOARD-UI-SPEC.md, Eng notes)."""
        import json

        report_path = tmp_path / "conform_report.html"
        out = generate_report(output_path=str(report_path), **_inputs_with_layers())

        sidecar = Path(out).with_suffix(".json")
        assert sidecar.is_file(), "JSON sidecar should be written next to the HTML"
        rows = json.loads(sidecar.read_text(encoding="utf-8"))["layers"]

        assert len(rows) == 2
        by_name = {r["name"]: r for r in rows}
        assert by_name["Root Layer"]["uid"] == "a1b2c3d4"
        # None-safe: layer dict without a uid key exports null, not KeyError.
        assert "uid" in by_name["Precomp Layer"]
        assert by_name["Precomp Layer"]["uid"] is None
