# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_report_collapse_section.py — Slot 12.5 Stage D collapse-
disable fix (2026-05-19).

Functional tests for the conform report's new "Collapse
Transformations — Disabled by Dimension" section:

  - Sidecar absent → section omitted (no Stage D inject ran).
  - Sidecar present, empty list → section omitted (Stage D ran,
    nothing to warn about).
  - Sidecar present, populated → amber warning section rendered
    with one row per override, plus the designer-readable
    explanation paragraph.
  - Stage D exit criterion requires this be VISIBLE — not a log
    entry — so the section must use amber styling and a heading
    a scanning designer will notice.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from logic.report_generator import (
    _load_collapse_overrides,
    _render_collapse_overrides_section,
    generate_report,
)


# ---------------------------------------------------------------------------
# 1. Loader behavior
# ---------------------------------------------------------------------------

class TestLoadCollapseOverrides:
    def test_returns_none_when_sidecar_missing(self, tmp_path: Path):
        """No sidecar → loader returns None so the section is
        omitted (legacy 87N flat path / pre-Stage-D conforms)."""
        assert _load_collapse_overrides(tmp_path) is None

    def test_returns_empty_list_when_no_overrides(self, tmp_path: Path):
        """Stage D ran with no collapse-on wrappers → sidecar
        exists, list is empty, section is still omitted."""
        sidecar = tmp_path / "inject_collapse_overrides.json"
        sidecar.write_text(json.dumps({
            "schema_version": "1.0",
            "session_id": "abc123",
            "ts": "2026-05-19",
            "collapse_overrides": [],
        }))
        assert _load_collapse_overrides(tmp_path) == []

    def test_loads_populated_list(self, tmp_path: Path):
        """Stage D ran with collapse-on wrappers → loader returns
        the override list verbatim."""
        sidecar = tmp_path / "inject_collapse_overrides.json"
        sidecar.write_text(json.dumps({
            "schema_version": "1.0",
            "session_id": "abc123",
            "ts": "2026-05-19",
            "collapse_overrides": [
                {
                    "wrapper_uid": "19e3b6de29e_7185ac",
                    "wrapper_name": "Precomp_A_Wrapper",
                    "wrapper_in_source_comp": "Root_Omnibus",
                    "target_mirror_source_name": "Precomp_A",
                },
            ],
        }))
        result = _load_collapse_overrides(tmp_path)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["wrapper_name"] == "Precomp_A_Wrapper"

    def test_malformed_sidecar_returns_none(self, tmp_path: Path):
        """Unreadable / malformed JSON → loader returns None and
        logs a warning. Don't take down the report over a corrupted
        sidecar."""
        sidecar = tmp_path / "inject_collapse_overrides.json"
        sidecar.write_text("{ not valid json")
        assert _load_collapse_overrides(tmp_path) is None


# ---------------------------------------------------------------------------
# 2. Section renderer
# ---------------------------------------------------------------------------

class TestRenderCollapseSection:
    def test_none_input_renders_empty(self):
        """None → empty string (section omitted)."""
        assert _render_collapse_overrides_section(None) == ""

    def test_empty_list_renders_empty(self):
        """Empty list → empty string (section omitted)."""
        assert _render_collapse_overrides_section([]) == ""

    def test_populated_renders_amber_section(self):
        """Single override → amber section with the wrapper named,
        the explanation visible, and an HTML table."""
        html = _render_collapse_overrides_section([{
            "wrapper_uid": "19e3b6de29e_7185ac",
            "wrapper_name": "Precomp_A_Wrapper",
            "wrapper_in_source_comp": "Root_Omnibus",
            "target_mirror_source_name": "Precomp_A",
        }])
        # Visible amber section + uppercased heading.
        assert 'class="collapse-section amber"' in html
        assert "Collapse Transformations" in html
        assert "Disabled by Dimension" in html
        # Specific wrapper called out.
        assert "Precomp_A_Wrapper" in html
        assert "Root_Omnibus" in html
        assert "19e3b6de29e_7185ac" in html
        # Designer-readable explanation paragraph.
        assert "disabled" in html.lower()

    def test_multiple_overrides_pluralizes(self):
        """Count and pluralization correct for multi-override
        scenarios (Corpus_03's mixed-collapse case)."""
        html = _render_collapse_overrides_section([
            {"wrapper_uid": "a", "wrapper_name": "W1",
             "wrapper_in_source_comp": "C1", "target_mirror_source_name": "T1"},
            {"wrapper_uid": "b", "wrapper_name": "W2",
             "wrapper_in_source_comp": "C2", "target_mirror_source_name": "T2"},
        ])
        assert "<strong>2</strong> wrapper" in html
        assert "wrappers had" in html  # plural
        assert "W1" in html and "W2" in html

    def test_single_override_singular(self):
        """One override uses singular phrasing (Corpus_01's case)."""
        html = _render_collapse_overrides_section([{
            "wrapper_uid": "a", "wrapper_name": "W1",
            "wrapper_in_source_comp": "C1", "target_mirror_source_name": "T1",
        }])
        assert "<strong>1</strong> wrapper had" in html


# ---------------------------------------------------------------------------
# 3. End-to-end: section appears in generated report HTML
# ---------------------------------------------------------------------------

class TestReportIntegration:
    def _minimal_inputs(self) -> Dict[str, Any]:
        return {
            "conformed_manifest": {
                "layers": [],
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

    def test_report_renders_collapse_section_when_present(self, tmp_path: Path):
        """End-to-end: a sidecar in the output dir produces a
        visible warning section in the rendered report HTML."""
        sidecar = tmp_path / "inject_collapse_overrides.json"
        sidecar.write_text(json.dumps({
            "schema_version": "1.0",
            "session_id": "test123",
            "ts": "2026-05-19",
            "collapse_overrides": [
                {
                    "wrapper_uid": "19e3b6de29e_7185ac",
                    "wrapper_name": "Precomp_A_Wrapper",
                    "wrapper_in_source_comp": "Root_Omnibus",
                    "target_mirror_source_name": "Precomp_A",
                },
            ],
        }))
        report_path = tmp_path / "conform_report.html"
        out = generate_report(
            output_path=str(report_path),
            **self._minimal_inputs(),
        )
        html = Path(out).read_text(encoding="utf-8")
        assert "Precomp_A_Wrapper" in html
        assert "Collapse Transformations" in html
        assert "Disabled by Dimension" in html
        assert "collapse-section" in html

    def test_report_omits_section_when_no_sidecar(self, tmp_path: Path):
        """87N flat regression / pre-Stage-D: no sidecar exists →
        no warning section in the output."""
        report_path = tmp_path / "conform_report.html"
        out = generate_report(
            output_path=str(report_path),
            **self._minimal_inputs(),
        )
        html = Path(out).read_text(encoding="utf-8")
        assert "Disabled by Dimension" not in html
        # collapse-section CSS class is in the template's <style>
        # block (it ships unconditionally). The SECTION markup
        # `<section class="collapse-section amber">` should NOT be
        # present.
        assert '<section class="collapse-section' not in html

    def test_report_omits_section_when_empty_overrides(self, tmp_path: Path):
        """Stage D ran cleanly with no collapse-on wrappers — empty
        list in sidecar → section still omitted."""
        sidecar = tmp_path / "inject_collapse_overrides.json"
        sidecar.write_text(json.dumps({
            "schema_version": "1.0",
            "session_id": "test123",
            "ts": "2026-05-19",
            "collapse_overrides": [],
        }))
        report_path = tmp_path / "conform_report.html"
        out = generate_report(
            output_path=str(report_path),
            **self._minimal_inputs(),
        )
        html = Path(out).read_text(encoding="utf-8")
        assert '<section class="collapse-section' not in html
