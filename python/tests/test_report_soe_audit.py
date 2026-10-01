# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_report_soe_audit.py
v5.2.5 — SOE audit section in conform_report.html.
"""

from __future__ import annotations

import json
import os
import sys


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from logic.report_generator import generate_report


def _render(tmp_path, soe_corrections=None) -> str:
    """Render a minimal conform report into `tmp_path`. When
    `soe_corrections` is not None, write it to
    `<tmp_path>/.dimension/soe_corrections.json` first."""
    if soe_corrections is not None:
        soe_dir = tmp_path / ".dimension"
        soe_dir.mkdir(parents=True, exist_ok=True)
        (soe_dir / "soe_corrections.json").write_text(
            json.dumps(soe_corrections), encoding="utf-8"
        )
    out_path = tmp_path / "report.html"
    generate_report(
        conformed_manifest={"layers": [], "warnings": {"collapsed_layers": []}},
        scrape_manifest={"project_info": {"name": "T", "width": 1920,
                                           "height": 1080}},
        preset_label="T",
        target_w=1080, target_h=1920,
        scale_mode="Fit", uniform_scale=0.5625,
        session_id="TEST",
        output_path=str(out_path),
        aep_dir=tmp_path,
    )
    return out_path.read_text(encoding="utf-8")


def _correction(**overrides) -> dict:
    """Build a SOECorrection-shaped dict (matches asdict(...)
    output)."""
    base = {
        "layer_index": 1,
        "layer_uid": "u-1",
        "layer_name": "Title",
        "content_tag": "TYPE",
        "original_position": [100.0, 250.0, 0.0],
        "corrected_position": [100.0, 100.0, 0.0],
        "zone_hit": "GO",
        "strategy": "TRANSLATE",
        "move_distance_px": 150.0,
        "original_zone_hit": "CUTOFF",
        "notes": "translated by (0,-150)px",
    }
    base.update(overrides)
    return base


# ── Section visibility ─────────────────────────────────────────────


class TestSectionVisibility:
    def test_section_omitted_when_no_file(self, tmp_path):
        html = _render(tmp_path, soe_corrections=None)
        # The CSS rules contain ".soe-audit" but the rendered <section>
        # element is what we care about — its presence means the
        # section was emitted.
        assert '<section class="soe-audit">' not in html
        assert "Spatial Occlusion Audit" not in html

    def test_empty_state_when_no_corrections(self, tmp_path):
        html = _render(tmp_path, soe_corrections=[])
        assert '<section class="soe-audit">' in html
        assert "No layers required occlusion checking" in html

    def test_corrupt_corrections_file_graceful(self, tmp_path):
        soe_dir = tmp_path / ".dimension"
        soe_dir.mkdir(parents=True, exist_ok=True)
        (soe_dir / "soe_corrections.json").write_bytes(b"{ this isn't JSON")
        out_path = tmp_path / "report.html"
        generate_report(
            conformed_manifest={"layers": [], "warnings": {"collapsed_layers": []}},
            scrape_manifest={"project_info": {"name": "T", "width": 1920,
                                               "height": 1080}},
            preset_label="T",
            target_w=1080, target_h=1920,
            scale_mode="Fit", uniform_scale=0.5625,
            session_id="TEST",
            output_path=str(out_path),
            aep_dir=tmp_path,
        )
        html = out_path.read_text(encoding="utf-8")
        # Section omitted, report still rendered, no traceback.
        assert '<section class="soe-audit">' not in html
        assert "DIMENSION CONFORM REPORT" in html


# ── Full audit rendering ───────────────────────────────────────────


class TestFullAuditRendersAllStrategies:
    def test_all_strategies_present(self, tmp_path):
        corrections = [
            _correction(strategy=s, layer_index=i + 1,
                        layer_name=f"layer-{s.lower()}",
                        original_zone_hit=None,
                        corrected_position=[100, 250, 0])
            for i, s in enumerate((
                "NONE", "TRANSLATE", "TIGHTEN_MARGIN", "ANCHOR_SHIFT",
                "SKIPPED_KEYED", "SKIPPED_NO_BOUNDS",
                "SKIPPED_STRUCTURAL", "SOE_FAILED",
            ))
        ]
        html = _render(tmp_path, soe_corrections=corrections)
        # Every strategy badge text appears in the table.
        for needed in ("CLEAR", "RELAYOUT", "KEYED",
                       "NO_BOUNDS", "STRUCTURAL", "FAILED"):
            assert needed in html
        # Every layer name appears.
        for s in ("none", "translate", "tighten_margin", "anchor_shift",
                  "skipped_keyed", "skipped_no_bounds",
                  "skipped_structural", "soe_failed"):
            assert f"layer-{s}" in html


class TestSummaryCountsCorrect:
    def test_mixed_summary(self, tmp_path):
        corrections = [
            _correction(strategy="TRANSLATE", layer_index=1),
            _correction(strategy="TRANSLATE", layer_index=2),
            _correction(strategy="SOE_FAILED", layer_index=3),
            _correction(strategy="NONE", layer_index=4),
            _correction(strategy="NONE", layer_index=5),
            _correction(strategy="NONE", layer_index=6),
            _correction(strategy="SKIPPED_KEYED", layer_index=7),
        ]
        html = _render(tmp_path, soe_corrections=corrections)
        assert "2 relayouts" in html
        assert "1 failed" in html
        assert "3 clear" in html
        assert "1 keyed" in html


class TestZeroCountBadgesOmitted:
    def test_only_clear(self, tmp_path):
        corrections = [
            _correction(strategy="NONE", layer_index=i + 1,
                         layer_name=f"L{i}")
            for i in range(5)
        ]
        html = _render(tmp_path, soe_corrections=corrections)
        # Only "5 clear" in the summary; no "0 ..." badges.
        assert "5 clear" in html
        assert "0 relayouts" not in html
        assert "0 failed" not in html
        assert "0 keyed" not in html


# ── Sort order ─────────────────────────────────────────────────────


class TestSortOrderFailedFirst:
    def test_failed_renders_before_clear(self, tmp_path):
        corrections = [
            _correction(strategy="NONE", layer_index=1, layer_name="z-clear"),
            _correction(strategy="SOE_FAILED", layer_index=2, layer_name="a-failed"),
            _correction(strategy="TRANSLATE", layer_index=3, layer_name="b-relayout"),
            _correction(strategy="SKIPPED_KEYED", layer_index=4, layer_name="c-keyed"),
        ]
        html = _render(tmp_path, soe_corrections=corrections)
        # First row should be the FAILED layer; last should be CLEAR.
        i_failed = html.find("a-failed")
        i_relayout = html.find("b-relayout")
        i_keyed = html.find("c-keyed")
        i_clear = html.find("z-clear")
        assert -1 < i_failed < i_relayout < i_keyed < i_clear


# ── Zone-hit arrow ─────────────────────────────────────────────────


class TestZoneHitArrowOnCorrection:
    def test_before_after_arrow(self, tmp_path):
        c = _correction(
            strategy="TRANSLATE",
            zone_hit="GO",
            original_zone_hit="CUTOFF",
        )
        html = _render(tmp_path, soe_corrections=[c])
        # CUTOFF and CLEAR badges separated by an arrow span.
        assert ">CUTOFF<" in html
        assert ">CLEAR<" in html
        assert "soe-zone-arrow" in html

    def test_no_arrow_when_zones_match(self, tmp_path):
        c = _correction(
            strategy="NONE",
            zone_hit="GO",
            original_zone_hit=None,
        )
        html = _render(tmp_path, soe_corrections=[c])
        # Single CLEAR badge in the row, no arrow.
        # The CSS class .soe-zone-arrow always appears in <style>;
        # check the <span class="soe-zone-arrow"> instead.
        assert '<span class="soe-zone-arrow">' not in html


# ── Layer name truncation ──────────────────────────────────────────


class TestTruncatedLayerName:
    def test_long_name_truncated_with_title_attr(self, tmp_path):
        long_name = "this-is-a-very-long-layer-name-that-exceeds-forty-characters"
        c = _correction(layer_name=long_name)
        html = _render(tmp_path, soe_corrections=[c])
        # Truncated text in the cell, full name in the title attr.
        assert long_name in html       # appears at least in title=
        assert "…" in html


# ── Backwards compat with the existing delta table ────────────────


class TestNoBreakingChangeToDeltaTable:
    def test_delta_table_byte_identical_when_no_soe(self, tmp_path):
        # Render with SOE absent vs SOE corrections present and assert
        # the existing per-layer delta table HTML is byte-identical
        # in the region between the original `<table>` and `</table>`.
        no_dir = tmp_path / "no"
        no_dir.mkdir()
        yes_dir = tmp_path / "yes"
        yes_dir.mkdir()
        html_no_soe = _render(no_dir, soe_corrections=None)
        html_with_soe = _render(yes_dir, soe_corrections=[_correction()])
        # Extract the original delta-table block (the first <table>
        # element in each render). Both must be equal.
        def first_table(s: str) -> str:
            i = s.find("<table>")
            j = s.find("</table>", i) + len("</table>")
            return s[i:j]
        assert first_table(html_no_soe) == first_table(html_with_soe)


# ── Corrupt JSON (loader) ─────────────────────────────────────────


# Corrupt-file handling already covered by
# TestSectionVisibility::test_corrupt_corrections_file_graceful
