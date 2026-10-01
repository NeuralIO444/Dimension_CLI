# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_report_safe_zone_verification.py
#420 — the "Verified post-inject" badge on the SOE audit section.

`_load_safe_zone_verification` reads transfer_status.log for the last
audit line carrying a `qc` field (the signal that Auditor.runFull, #427,
actually executed) and reports whether any SAFE_ZONE_DRIFT warning fired.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from logic.report_generator import (
    _load_safe_zone_verification,
    _render_safe_zone_verification_badge,
    generate_report,
)


def _write_log(tmp_path, lines):
    log_path = tmp_path / "transfer_status.log"
    with log_path.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")
    return log_path


class TestLoadSafeZoneVerification:
    def test_none_when_no_log_file(self, tmp_path):
        assert _load_safe_zone_verification(tmp_path) is None

    def test_none_when_log_has_no_qc_field_at_all(self, tmp_path):
        # Legacy-shaped audit line (pre-#427) -- runFull never ran.
        _write_log(tmp_path, [
            {"status": "COMPLETE", "auditPass": True, "layerCount": 4,
             "mirror_comp_count": 1, "audit_path": "mirror_tree"},
        ])
        assert _load_safe_zone_verification(tmp_path) is None

    def test_empty_drift_when_qc_line_has_no_safe_zone_warning(self, tmp_path):
        _write_log(tmp_path, [
            {"status": "COMPLETE", "auditPass": True, "layerCount": 4,
             "qc": {"rootCount": 3, "childCount": 1}},
        ])
        result = _load_safe_zone_verification(tmp_path)
        assert result == {"drift": []}

    def test_drift_extracted_when_safe_zone_drift_warning_present(self, tmp_path):
        _write_log(tmp_path, [
            {"status": "COMPLETE", "auditPass": True, "layerCount": 4,
             "qc": {"rootCount": 3, "childCount": 1},
             "warnings": [
                 {"check": "SAFE_ZONE_DRIFT", "count": 2, "tolerance": 0.01,
                  "layers": [
                      {"layer": "Caption", "uid": "ab0"},
                      {"layer": "Legal", "uid": "cd1"},
                  ]},
             ]},
        ])
        result = _load_safe_zone_verification(tmp_path)
        assert result is not None
        assert len(result["drift"]) == 2
        assert result["drift"][0]["layer"] == "Caption"

    def test_takes_the_last_qc_line_when_multiple_runs_logged(self, tmp_path):
        _write_log(tmp_path, [
            {"status": "COMPLETE", "qc": {"rootCount": 1, "childCount": 0},
             "warnings": [{"check": "SAFE_ZONE_DRIFT", "layers": [{"layer": "Old"}]}]},
            {"status": "COMPLETE", "qc": {"rootCount": 2, "childCount": 0}},
        ])
        result = _load_safe_zone_verification(tmp_path)
        # Second (most recent) line has no SAFE_ZONE_DRIFT warning at all.
        assert result == {"drift": []}

    def test_corrupt_log_line_skipped_not_fatal(self, tmp_path):
        log_path = tmp_path / "transfer_status.log"
        log_path.write_text(
            "not json at all\n"
            + json.dumps({"status": "COMPLETE", "qc": {"rootCount": 1, "childCount": 0}}) + "\n",
            encoding="utf-8",
        )
        result = _load_safe_zone_verification(tmp_path)
        assert result == {"drift": []}

    def test_unreadable_log_directory_returns_none_not_raise(self, tmp_path):
        # A directory where a log file is expected but isn't a file at all.
        (tmp_path / "transfer_status.log").mkdir()
        assert _load_safe_zone_verification(tmp_path) is None


class TestVerificationBadgeRendering:
    def test_none_renders_not_verified(self):
        html = _render_safe_zone_verification_badge(None)
        assert "Not verified post-inject" in html
        assert "soe-verify-unknown" in html

    def test_empty_drift_renders_verified_clean(self):
        html = _render_safe_zone_verification_badge({"drift": []})
        assert "Verified post-inject" in html
        assert "soe-verify-ok" in html

    def test_nonempty_drift_renders_count_and_drift_class(self):
        html = _render_safe_zone_verification_badge(
            {"drift": [{"layer": "A"}, {"layer": "B"}]}
        )
        assert "2 layer(s) drifted post-inject" in html
        assert "soe-verify-drift" in html


class TestBadgeIntegratedIntoFullReport:
    """End-to-end: generate_report() actually wires the badge in, in both
    the empty-state and populated SOE-section branches."""

    def _render(self, tmp_path, soe_corrections=None, log_lines=None):
        if soe_corrections is not None:
            soe_dir = tmp_path / ".dimension"
            soe_dir.mkdir(parents=True, exist_ok=True)
            (soe_dir / "soe_corrections.json").write_text(
                json.dumps(soe_corrections), encoding="utf-8"
            )
        if log_lines is not None:
            _write_log(tmp_path, log_lines)
        out_path = tmp_path / "report.html"
        generate_report(
            conformed_manifest={"layers": [], "warnings": {"collapsed_layers": []}},
            scrape_manifest={"project_info": {"name": "T", "width": 1920, "height": 1080}},
            preset_label="T",
            target_w=1080, target_h=1920,
            scale_mode="Fit", uniform_scale=0.5625,
            session_id="TEST",
            output_path=str(out_path),
            aep_dir=tmp_path,
        )
        return out_path.read_text(encoding="utf-8")

    def test_empty_state_soe_section_shows_not_verified_badge_with_no_log(self, tmp_path):
        html = self._render(tmp_path, soe_corrections=[])
        assert "No layers required occlusion checking" in html
        assert "Not verified post-inject" in html

    def test_populated_soe_section_shows_verified_clean_badge(self, tmp_path):
        html = self._render(
            tmp_path,
            soe_corrections=[{
                "layer_index": 1, "layer_uid": "u-1", "layer_name": "Title",
                "content_tag": "TYPE", "original_position": [100.0, 250.0, 0.0],
                "corrected_position": [100.0, 100.0, 0.0], "zone_hit": "GO",
                "strategy": "TRANSLATE", "move_distance_px": 150.0,
            }],
            log_lines=[{"status": "COMPLETE", "qc": {"rootCount": 1, "childCount": 0}}],
        )
        assert "Verified post-inject" in html
        assert "soe-verify-ok" in html

    def test_populated_soe_section_shows_drift_badge(self, tmp_path):
        html = self._render(
            tmp_path,
            soe_corrections=[{
                "layer_index": 1, "layer_uid": "u-1", "layer_name": "Title",
                "content_tag": "TYPE", "original_position": [100.0, 250.0, 0.0],
                "corrected_position": [100.0, 100.0, 0.0], "zone_hit": "GO",
                "strategy": "TRANSLATE", "move_distance_px": 150.0,
            }],
            log_lines=[{
                "status": "COMPLETE", "qc": {"rootCount": 1, "childCount": 0},
                "warnings": [{"check": "SAFE_ZONE_DRIFT", "layers": [{"layer": "Title"}]}],
            }],
        )
        assert "1 layer(s) drifted post-inject" in html
        assert "soe-verify-drift" in html
