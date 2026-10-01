# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_report_archetype.py — issue #423 (closes the founding example of
issue #418): `layer.archetype` is computed by surveyor.py AND
SovCore_Layer.jsx but had zero readers anywhere. Scoped slice per the
issue: surface it as a diagnostic in the conform report only. It must
NOT affect any tag/gravity/scale output.
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


def _inputs_with_archetype(archetype: str | None = "TYPE") -> Dict[str, Any]:
    layer: Dict[str, Any] = {
        "index": 1,
        "name": "Lower Third Text",
        "containing_comp_id": None,
        "content_tag": "BOTTOM",
        "conformed_transforms": {"position": [0, 0, 0], "scale": [100, 100, 100]},
    }
    if archetype is not None:
        layer["archetype"] = archetype
    return {
        "conformed_manifest": {
            "layers": [layer],
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


class TestArchetypeDiagnosticSurface:
    def test_html_table_shows_archetype_tooltip(self, tmp_path: Path):
        report_path = tmp_path / "conform_report.html"
        out = generate_report(output_path=str(report_path), **_inputs_with_archetype("TYPE"))
        html = Path(out).read_text(encoding="utf-8")
        assert "Archetype: TYPE" in html

    def test_html_omits_archetype_tooltip_when_absent(self, tmp_path: Path):
        report_path = tmp_path / "conform_report.html"
        out = generate_report(output_path=str(report_path), **_inputs_with_archetype(None))
        html = Path(out).read_text(encoding="utf-8")
        assert "Archetype:" not in html

    def test_json_export_carries_archetype(self, tmp_path: Path):
        report_path = tmp_path / "conform_report.html"
        out = generate_report(output_path=str(report_path), **_inputs_with_archetype("KEYED_ALPHA"))
        sidecar = Path(out).with_suffix(".json")
        rows = json.loads(sidecar.read_text(encoding="utf-8"))["layers"]
        assert rows[0]["archetype"] == "KEYED_ALPHA"

    def test_json_export_archetype_none_safe(self, tmp_path: Path):
        report_path = tmp_path / "conform_report.html"
        out = generate_report(output_path=str(report_path), **_inputs_with_archetype(None))
        sidecar = Path(out).with_suffix(".json")
        rows = json.loads(sidecar.read_text(encoding="utf-8"))["layers"]
        assert rows[0]["archetype"] is None

    def test_csv_export_carries_archetype_column(self, tmp_path: Path):
        report_path = tmp_path / "conform_report.html"
        out = generate_report(output_path=str(report_path), **_inputs_with_archetype("VECTOR_2D"))
        csv_path = Path(out).with_suffix(".csv")
        csv_text = csv_path.read_text(encoding="utf-8")
        rows = csv_text.splitlines()
        assert "Archetype" in rows[0]
        assert "VECTOR_2D" in rows[1]

    def test_live_enum_instance_unwraps_to_plain_value(self, tmp_path: Path):
        """CLAUDE.md sharp edge: `str(LayerArchetype.TYPE)` is
        "LayerArchetype.TYPE" on Python 3.11+, not "TYPE" — a manifest
        dict that hasn't round-tripped through JSON yet (e.g.
        `layer.model_dump()` in the python mode used throughout
        scale_engine_*.py) can still carry a live enum instance here.
        The HTML tooltip, JSON export and CSV export must all show the
        plain value, never the `LayerArchetype.X` repr."""
        from models.scrape_manifest import LayerArchetype

        report_path = tmp_path / "conform_report.html"
        out = generate_report(
            output_path=str(report_path),
            **_inputs_with_archetype(LayerArchetype.TYPE),  # type: ignore[arg-type]
        )
        html = Path(out).read_text(encoding="utf-8")
        assert "Archetype: TYPE" in html
        assert "LayerArchetype.TYPE" not in html

        sidecar = Path(out).with_suffix(".json")
        rows = json.loads(sidecar.read_text(encoding="utf-8"))["layers"]
        assert rows[0]["archetype"] == "TYPE"

        csv_path = Path(out).with_suffix(".csv")
        csv_text = csv_path.read_text(encoding="utf-8")
        assert "LayerArchetype.TYPE" not in csv_text
        assert "TYPE" in csv_text.splitlines()[1]

    def test_archetype_does_not_affect_tag_or_transforms(self, tmp_path: Path):
        """Visibility-only per the issue: the tag pill / classification
        cell and the position/scale cells must be identical whether or
        not archetype is present."""
        report_path_a = tmp_path / "a" / "conform_report.html"
        report_path_a.parent.mkdir()
        report_path_b = tmp_path / "b" / "conform_report.html"
        report_path_b.parent.mkdir()

        out_a = generate_report(output_path=str(report_path_a), **_inputs_with_archetype("TYPE"))
        out_b = generate_report(output_path=str(report_path_b), **_inputs_with_archetype(None))

        html_a = Path(out_a).read_text(encoding="utf-8")
        html_b = Path(out_b).read_text(encoding="utf-8")

        assert 'tag-pill bottom' in html_a
        assert 'tag-pill bottom' in html_b
