# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_report_performance_section.py — Telemetry performance section tests.
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
    _render_performance_section,
    generate_report,
)


class TestRenderPerformanceSection:
    def test_returns_empty_when_no_telemetry(self, tmp_path: Path):
        """No telemetry data in manifest or logs -> empty string."""
        assert _render_performance_section({}, tmp_path) == ""

    def test_renders_scrape_telemetry(self, tmp_path: Path):
        """Scrape telemetry in manifest is rendered."""
        manifest = {
            "scrape_meta": {
                "telemetry": {
                    "comp_walk_ms": 120.0,
                    "per_layer_scrape_ms": 450.0,
                    "project_structure_scan_ms": 50.0,
                    "total_scrape_ms": 620.0,
                }
            }
        }
        html = _render_performance_section(manifest, tmp_path)
        assert '<section class="perf-section">' in html
        assert "Performance & Telemetry" in html
        assert "Scrape: BFS Comp Walk (JSX)" in html
        assert "120 ms" in html
        assert "Scrape: Per-layer Property Scrape (JSX)" in html
        assert "450 ms" in html
        assert "Scrape: Total Scrape Duration (JSX)" in html
        assert "620 ms" in html

    def test_renders_conform_logs(self, tmp_path: Path):
        """Telemetry entries from transfer_status.log are parsed and rendered."""
        log_path = tmp_path / "transfer_status.log"
        log_path.write_text("\n".join([
            json.dumps({"event": "telemetry", "phase": "setupWorkspace", "duration_ms": 1500}),
            json.dumps({"event": "telemetry", "phase": "rewire", "duration_ms": 400}),
            json.dumps({"event": "telemetry", "phase": "chunk_processing", "chunk_index": 0, "duration_ms": 1200}),
            json.dumps({"event": "telemetry", "phase": "output_comp_inject", "comp_name": "MainComp", "duration_ms": 800}),
            json.dumps({"event": "telemetry", "phase": "audit", "duration_ms": 100}),
            json.dumps({"event": "telemetry", "phase": "python_scale_engine", "duration_ms": 52.4}),
        ]))

        html = _render_performance_section({}, tmp_path)
        assert "Conform: Workspace Setup (JSX)" in html
        assert "1500 ms" in html
        assert "Conform: Precomp Rewire (JSX)" in html
        assert "400 ms" in html
        assert "Conform: Chunk 1 Processing (JSX)" in html
        assert "1200 ms" in html
        assert "Conform: Inject -> MainComp (JSX)" in html
        assert "800 ms" in html
        assert "Conform: Scale Engine (Python)" in html
        assert "52.40 ms" in html


class TestPerformanceReportIntegration:
    def _minimal_inputs(self) -> Dict[str, Any]:
        return {
            "conformed_manifest": {
                "layers": [],
                "warnings": {"collapsed_layers": []},
                "aspect_strategy": "fit",
            },
            "preset_label": "TikTok",
            "target_w": 1080,
            "target_h": 1920,
            "scale_mode": "Fit",
            "uniform_scale": 0.5625,
            "session_id": "test123",
        }

    def test_report_renders_performance_section_when_present(self, tmp_path: Path):
        """End-to-end: telemetry logs render correctly in HTML report."""
        scrape_manifest = {
            "project_info": {"name": "TestComp", "width": 1920, "height": 1080},
            "scrape_meta": {
                "telemetry": {
                    "total_scrape_ms": 350.0,
                }
            }
        }
        log_path = tmp_path / "transfer_status.log"
        log_path.write_text(json.dumps({
            "event": "telemetry",
            "phase": "python_scale_engine",
            "duration_ms": 12.34
        }))

        report_path = tmp_path / "conform_report.html"
        out = generate_report(
            output_path=str(report_path),
            scrape_manifest=scrape_manifest,
            **self._minimal_inputs(),
        )
        html = Path(out).read_text(encoding="utf-8")
        assert "Performance & Telemetry" in html
        assert "Scrape: Total Scrape Duration (JSX)" in html
        assert "350 ms" in html
        assert "Conform: Scale Engine (Python)" in html
        assert "12.34 ms" in html
