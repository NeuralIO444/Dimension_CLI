# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_loud_failures.py
Phase 1 (loud failures) — degradations must surface, never vanish.

Background: the surveyor's graceful-degradation contract ("never raise")
let real failures hide for weeks — most notably the _ollama_pass NameError
that silently disabled the entire AI tagging pass (fixed 2026-07-02).
Phase 1 keeps the never-raise contract but makes every degraded path
visible: survey_manifest() now records human-readable notes in
counts["warnings"] and mirrors them onto manifest.survey_warnings, and
generate_report() renders a Run Warnings card.

These tests prove the surfacing works for the highest-risk paths. They
follow the mocking pattern of test_ai_pipeline_smoke.py — patch the
ollama_client module attributes (the surveyor imports them at call time
inside _ollama_pass, so module-attribute patches are picked up).
"""

from __future__ import annotations

import json
import os
import sys


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from models.scrape_manifest import ScrapeManifest  # noqa: E402

_FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "bug_l", "87n_source_manifest.json"
)


def _load_manifest() -> ScrapeManifest:
    with open(_FIXTURE_PATH) as f:
        return ScrapeManifest.model_validate(json.load(f))


class TestSurveyWarnings:
    def test_clean_run_has_empty_warnings_and_none_on_manifest(self):
        """A fully clean survey (AI off, gardener fine) produces an empty
        warnings list in counts and leaves manifest.survey_warnings None —
        clean runs must stay clean, no warning noise."""
        from core.surveyor import survey_manifest

        manifest = _load_manifest()
        counts = survey_manifest(manifest)

        assert counts["warnings"] == []
        assert manifest.survey_warnings is None


class TestReportRunWarnings:
    def _minimal_report_args(self, tmp_path):
        return dict(
            conformed_manifest={"layers": [], "warnings": {}},
            scrape_manifest={"project_info": {"name": "T"}},
            preset_label="TikTok",
            target_w=1080,
            target_h=1920,
            scale_mode="Fit",
            uniform_scale=1.0,
            session_id="sess-test",
            output_path=str(tmp_path / "report.html"),
        )

    def test_run_warnings_render_in_report(self, tmp_path):
        from logic.report_generator import generate_report

        path = generate_report(
            **self._minimal_report_args(tmp_path),
            run_warnings=["SOE skipped — no safe-zone mask for preset 'x'"],
        )
        html = open(path, encoding="utf-8").read()
        assert "Run Warnings" in html
        assert "SOE skipped" in html

    def test_survey_warnings_from_manifest_render_in_report(self, tmp_path):
        """Survey-time degradations travel on the manifest dict — the
        report must pick them up without orchestrator plumbing."""
        from logic.report_generator import generate_report

        args = self._minimal_report_args(tmp_path)
        args["scrape_manifest"]["survey_warnings"] = [
            "AI tagging is enabled but Ollama is not reachable"]
        path = generate_report(**args)
        html = open(path, encoding="utf-8").read()
        assert "Run Warnings" in html
        assert "Ollama is not reachable" in html

    def test_clean_run_renders_no_warnings_card(self, tmp_path):
        from logic.report_generator import generate_report

        path = generate_report(**self._minimal_report_args(tmp_path))
        html = open(path, encoding="utf-8").read()
        assert "Run Warnings" not in html

    def test_warning_text_is_html_escaped(self, tmp_path):
        from logic.report_generator import generate_report

        path = generate_report(
            **self._minimal_report_args(tmp_path),
            run_warnings=['<script>alert("x")</script>'],
        )
        html = open(path, encoding="utf-8").read()
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html


class TestOrchestratorWarningEvent:
    def test_emit_run_warning_prints_event_and_appends(self, capsys):
        """_emit_run_warning is the one chokepoint for pipeline-runtime
        degradations — it must hit stdout (panel event), the sink (report),
        and never raise."""
        from orchestrator import _emit_run_warning

        sink: list = []
        _emit_run_warning("SOE skipped — test", sink)

        assert sink == ["SOE skipped — test"]
        out_lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
        evt = json.loads(out_lines[-1])
        assert evt == {"type": "warning", "msg": "SOE skipped — test"}
