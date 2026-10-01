# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_duplication_report.py
v5.8 — coverage for the "Precomp Duplications" section of
conform_report.html.

Tests render-only logic — no AE, no file I/O on the input side
(though we use tmp_path to read duplication_log.json from disk via
the public helper). The section's state machine:
  - both inputs None → ""
  - plan present, no log → DRY RUN preview
  - log clean → green / "ok" section
  - log with errors → amber section
  - skip rows render with reason
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


def _plan_dict():
    return {
        "session_id":       "sess-1",
        "session_folder":   "From Dimensions/TIKTOK_2026-04-25_153022",
        "preset_id":        "TIKTOK",
        "target_dimensions": [1080, 1920],
        "aspect_ratio_changed": True,
        "duplicates": [
            {"original_uid":   "100",
             "original_name":  "Hero",
             "duplicate_name": "Hero_1080x1920",
             "target_folder_path": "From Dimensions/TIKTOK_2026-04-25_153022",
             "original_width":  1920,
             "original_height": 1080,
             "reason":          "SHARED",
             "is_protected":    False,
             "will_be_skipped": False},
            {"original_uid":   "200",
             "original_name":  "CTA",
             "duplicate_name": "CTA_1080x1920",
             "target_folder_path": "From Dimensions/TIKTOK_2026-04-25_153022",
             "original_width":  1920,
             "original_height": 1080,
             "reason":          "SHARED",
             "is_protected":    True,
             "will_be_skipped": True},
        ],
        "rewires":     [],
        "depth_max":   0,
    }


# ── _render_duplication_section ──────────────────────────────────────


class TestRenderDuplicationSection:
    def test_omits_when_both_inputs_none(self):
        from logic.report_generator import _render_duplication_section
        assert _render_duplication_section(None, None) == ""

    def test_dry_run_when_plan_present_no_log(self):
        from logic.report_generator import _render_duplication_section
        out = _render_duplication_section(_plan_dict(), None)
        assert "DRY RUN" in out
        assert "Hero_1080x1920" in out
        assert "CTA_1080x1920" in out
        # Dry run never reports CREATED / FAILED.
        assert "FAILED" not in out
        assert "CREATED" not in out

    def test_log_clean_renders_ok_section(self):
        from logic.report_generator import _render_duplication_section
        log = {
            "session_id": "sess-1",
            "duplicates_made": [
                {"original_uid": "100",
                 "duplicate_uid": "999",
                 "duplicate_name": "Hero_1080x1920"},
            ],
            "rewires_made":  [],
            "skipped":       [
                {"original_uid": "200", "reason": "PROTECT"},
            ],
            "errors": [],
        }
        out = _render_duplication_section(_plan_dict(), log)
        assert "dup-section ok" in out
        assert "1 created" in out
        assert "1 skipped" in out
        # Hero row → CREATED status.
        assert "Hero_1080x1920" in out and "CREATED" in out
        # CTA row → SKIPPED · PROTECT.
        assert "PROTECT" in out
        # Footer with copy-paste folder path.
        assert "Created 1 duplicate" in out
        assert "From Dimensions/TIKTOK_2026-04-25_153022" in out

    def test_log_with_errors_renders_amber_section(self):
        from logic.report_generator import _render_duplication_section
        log = {
            "session_id": "sess-1",
            "duplicates_made": [],
            "rewires_made":    [],
            "skipped":         [],
            "errors": [
                {"phase": "duplicate",
                 "original_uid": "100",
                 "detail":  "comp source vanished mid-undo"},
            ],
        }
        out = _render_duplication_section(_plan_dict(), log)
        assert "dup-section amber" in out
        assert "1 failed" in out
        assert "FAILED" in out
        assert "comp source vanished" in out

    def test_dry_run_explicit_flag(self):
        """A log can be present alongside dry_run=True (e.g. the user
        ran a dry-run preview that wrote a stub log). Dry-run flag
        wins the styling."""
        from logic.report_generator import _render_duplication_section
        out = _render_duplication_section(
            _plan_dict(),
            {"session_id": "x", "duplicates_made": [],
             "rewires_made": [], "skipped": [], "errors": []},
            dry_run=True,
        )
        assert "dup-section dry-run" in out
        assert "DRY RUN" in out


# ── _load_duplication_log ───────────────────────────────────────────


class TestLoadDuplicationLog:
    def test_missing_file_returns_none(self, tmp_path):
        from logic.report_generator import _load_duplication_log
        assert _load_duplication_log(tmp_path) is None

    def test_corrupt_file_returns_none_does_not_raise(self, tmp_path):
        from logic.report_generator import _load_duplication_log
        d = tmp_path / ".dimension"
        d.mkdir()
        (d / "duplication_log.json").write_text("not json {", encoding="utf-8")
        assert _load_duplication_log(tmp_path) is None

    def test_valid_file_round_trips(self, tmp_path):
        from logic.report_generator import _load_duplication_log
        d = tmp_path / ".dimension"
        d.mkdir()
        payload = {"session_id": "sess-load",
                    "duplicates_made": [], "rewires_made": [],
                    "skipped": [], "errors": []}
        (d / "duplication_log.json").write_text(
            json.dumps(payload), encoding="utf-8")
        loaded = _load_duplication_log(tmp_path)
        assert loaded is not None
        assert loaded["session_id"] == "sess-load"


# ── generate_report integration ─────────────────────────────────────


class TestGenerateReportIntegration:
    def test_section_appears_when_plan_supplied(self, tmp_path):
        """generate_report passes duplication_plan through to the
        render helper, which lands in the HTML."""
        from logic.report_generator import generate_report
        out_path = tmp_path / "report.html"
        path = generate_report(
            conformed_manifest={"layers": []},
            scrape_manifest={"project_info": {"name": "ART_v14"}},
            preset_label="TIKTOK",
            target_w=1080, target_h=1920,
            scale_mode="Fit",
            uniform_scale=0.5,
            session_id="sess-X",
            output_path=str(out_path),
            aep_dir=tmp_path,
            duplication_plan=_plan_dict(),
            duplication_dry_run=True,
        )
        html = Path(path).read_text(encoding="utf-8")
        assert "Precomp Duplications" in html
        assert "DRY RUN" in html

    def test_section_omitted_when_no_plan_no_log(self, tmp_path):
        from logic.report_generator import generate_report
        out_path = tmp_path / "report.html"
        path = generate_report(
            conformed_manifest={"layers": []},
            scrape_manifest={"project_info": {"name": "ART_v14"}},
            preset_label="TIKTOK",
            target_w=1080, target_h=1920,
            scale_mode="Fit",
            uniform_scale=0.5,
            session_id="sess-X",
            output_path=str(out_path),
            aep_dir=tmp_path,
        )
        html = Path(path).read_text(encoding="utf-8")
        # CSS comment mentions the section name; the only thing we
        # actually care about is whether the <section> element
        # rendered.
        assert "<section class=\"dup-section" not in html
