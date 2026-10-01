"""Unit tests for python/tools/debug_bundle.py."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from python.tools.debug_bundle import _filtered_log_tail, build_debug_bundle


def test_build_debug_bundle_with_sample_session(tmp_path: Path) -> None:
    # Setup mock log/session directories in repo root
    base_dir = tmp_path
    bundles_dir = base_dir / "logs" / "bundles"

    session_id = "TEST1234"
    html_path = base_dir / f"conform_report__{session_id}.html"
    json_path = base_dir / f"conform_report__{session_id}.json"

    html_path.write_text("<html><body>Conform Report</body></html>", encoding="utf-8")
    sample_json_data = {
        "meta": {
            "session_id": session_id,
            "source_name": "Test_Comp",
            "target_w": 1080,
            "target_h": 1920,
            "scale_mode": "Fit",
            "uniform_scale": 1.0,
            "aspect_strategy": "fit",
            "total_layers": 5,
            "total_chunks": 1,
            "collapse_warnings": 0,
        }
    }
    json_path.write_text(json.dumps(sample_json_data), encoding="utf-8")

    # Run bundle creation pointing to tmp_path base
    bundle_zip, summary_path = build_debug_bundle(
        session_id=session_id,
        repo=base_dir,
        output_dir=bundles_dir,
    )

    assert bundle_zip.exists()
    assert summary_path.exists()
    assert bundle_zip.suffix == ".zip"

    # Verify archive contents
    with zipfile.ZipFile(bundle_zip, "r") as z:
        names = z.namelist()
        assert "QA_SUMMARY.md" in names
        assert f"conform_report__{session_id}.html" in names
        assert f"conform_report__{session_id}.json" in names
        assert "environment.json" in names

    # Verify QA summary content
    summary_text = summary_path.read_text(encoding="utf-8")
    assert f"Session `{session_id}`" in summary_text
    assert "Test_Comp" in summary_text
    assert "1080" in summary_text
    assert "1920" in summary_text


def test_build_debug_bundle_digests_soe_and_duplication_artifacts(tmp_path: Path) -> None:
    """Issue #503 — the SOE Corrections and Duplication digests must read
    the real on-disk field names (`move_distance_px`/`layer_name`/`strategy`/
    `zone_hit` for soe_corrections.json; `duplicates_made`/`duplicate_name`
    for duplication_log.json, which is an object, not a bare list) rather
    than guessed names."""
    base_dir = tmp_path
    session_id = "TEST5678"
    json_path = base_dir / f"conform_report__{session_id}.json"
    json_path.write_text(json.dumps({"meta": {"session_id": session_id}}), encoding="utf-8")

    soe_corrections = [
        {
            "layer_name": "TT_Headline",
            "strategy": "NUDGE_UP",
            "move_distance_px": 42.5,
            "zone_hit": "TITLE_SAFE",
        },
        {
            "layer_name": "BG_Plate",
            "strategy": "SKIPPED_STRUCTURAL",
            "move_distance_px": 0,
            "zone_hit": None,
        },
    ]
    (base_dir / "soe_corrections.json").write_text(json.dumps(soe_corrections), encoding="utf-8")

    duplication_log = {
        "session_id": session_id,
        "schema_version": "1.0",
        "duplicates_made": [
            {"original_uid": "111", "duplicate_uid": "222", "duplicate_name": "Precomp_A_dup1"},
        ],
        "rewires_made": [
            {"conformed_layer_uid": "abc", "conformed_layer_name": "L1",
             "original_source_uid": "111", "new_source_uid": "222"},
        ],
        "skipped": [],
        "errors": [],
    }
    (base_dir / "duplication_log.json").write_text(json.dumps(duplication_log), encoding="utf-8")

    _, summary_path = build_debug_bundle(
        session_id=session_id,
        repo=base_dir,
        output_dir=base_dir / "logs" / "bundles",
    )

    summary_text = summary_path.read_text(encoding="utf-8")
    assert "SOE Corrections Digest (2 total, 1 moved)" in summary_text
    assert "`TT_Headline`: NUDGE_UP (42px, zone `TITLE_SAFE`)" in summary_text
    assert "Duplication Digest (1 duplicated, 1 rewired, 0 skipped, 0 error(s))" in summary_text
    assert "`Precomp_A_dup1`" in summary_text


def test_filtered_log_tail_handles_empty_log() -> None:
    assert _filtered_log_tail("") == "(log is empty)"


def test_filtered_log_tail_handles_every_line_matching() -> None:
    # max_matched_lines caps which matches get a window at all — this is
    # a hard truncation, not a "show everything but summarize" cap. With
    # 20/20 lines matching and max_matched_lines=5, only the first 5
    # matches (lines 1-5) get windows; context_lines=2 extends that to
    # lines 1-7. Lines 8-20 must NOT appear, even though they matched.
    text = "\n".join(f"line {i}: error" for i in range(1, 21))
    result = _filtered_log_tail(text, context_lines=2, max_matched_lines=5)
    assert "20 matching line(s) for error/warning/exception, 2-line context each, showing first 5" in result
    assert "line 1: error" in result
    assert "line 7: error" in result
    assert "line 8: error" not in result
    assert "line 20: error" not in result


def test_filtered_log_tail_returns_fallback_tail_when_no_matches() -> None:
    text = "\n".join(f"line {i}: all clear" for i in range(1, 21))
    result = _filtered_log_tail(text, fallback_lines=5)
    assert "no error/warning/exception lines found" in result
    assert "line 16: all clear" in result
    assert "line 20: all clear" in result
    assert "line 1: all clear" not in result


def test_filtered_log_tail_includes_context_around_matches() -> None:
    lines = [f"line{i}" for i in range(1, 11)]
    lines[5] = "ERROR something broke"
    text = "\n".join(lines)
    result = _filtered_log_tail(text, context_lines=2)
    assert "ERROR something broke" in result
    assert "line4" in result  # 2 lines before
    assert "line8" in result  # 2 lines after
    assert "1 matching line(s)" in result


def test_filtered_log_tail_merges_overlapping_windows() -> None:
    lines = [f"line{i}" for i in range(1, 21)]
    lines[4] = "WARNING first"
    lines[6] = "WARNING second"
    text = "\n".join(lines)
    result = _filtered_log_tail(text, context_lines=2)
    # Windows for matches at index 4 and 6 (context 2) overlap: [2,6] and
    # [4,8] merge into a single [2,8] block with one header, not two.
    assert result.count("--- lines") == 1
    assert "2 matching line(s)" in result
