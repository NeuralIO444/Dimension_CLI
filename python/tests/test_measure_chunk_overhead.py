# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Tests for tools/measure_chunk_overhead.py --parse-only (M3) — the
pure-parser half; the driver loop requires live AE and is a field-
session item. Synthetic transfer_status.log fixtures only."""

from __future__ import annotations

import json
from pathlib import Path

from tools.measure_chunk_overhead import (
    main,
    parse_transfer_runs,
    render_table,
    summarize_run,
)


def _run_lines(
    *,
    base_t: int,
    setup_ms: int = 100,
    chunks: int = 2,
    tick_ms: int = 500,
    setvalue_ms: int = 40,
    audit_gap_ms: int = 75,
    layer_count: int = 60,
    status: str = "COMPLETE",
) -> list[str]:
    """Synthesize one inject run's telemetry in Babysitter's line shapes
    (mirrors the real transfer_status.log captured 2026-07-05/06)."""
    t = base_t
    lines = [json.dumps({"event": "pump.setup.start", "t_ms": t})]
    t += setup_ms
    lines.append(json.dumps({"event": "pump.setup.end", "setup_ms": setup_ms, "ok": True, "t_ms": t}))
    lines.append(json.dumps({"phase": "INJECT", "t_ms": t + 1}))
    for i in range(chunks):
        t += 10
        lines.append(json.dumps({"event": "pump.chunk.start", "chunk_index": i, "t_ms": t}))
        t += tick_ms
        lines.append(json.dumps({
            "event": "pump.chunk.end", "chunk_index": i,
            "setvalue_ms": setvalue_ms, "purge_gc_ms": 5,
            "tick_total_ms": tick_ms, "t_ms": t,
        }))
    t += 20
    lines.append(json.dumps({"event": "pump.audit.start", "t_ms": t}))
    audit_start = t
    t = audit_start + audit_gap_ms
    lines.append(json.dumps({
        "status": status, "auditPass": status == "COMPLETE",
        "layerCount": layer_count,
        "ts": "Sun Jul 05 2026 17:38:31 GMT-0700", "t_ms": t,
    }))
    return lines


class TestParseTransferRuns:
    def test_segments_two_runs(self):
        lines = _run_lines(base_t=1_000_000) + _run_lines(base_t=2_000_000, chunks=3)
        runs = parse_transfer_runs(lines)
        assert len(runs) == 2
        assert runs[0]["status_line"]["status"] == "COMPLETE"

    def test_orphan_telemetry_outside_runs_dropped(self):
        lines = (
            [json.dumps({"event": "telemetry", "phase": "noise", "t_ms": 5})]
            + _run_lines(base_t=1_000_000)
        )
        runs = parse_transfer_runs(lines)
        assert len(runs) == 1

    def test_aborted_run_without_status_discarded(self):
        aborted = [
            json.dumps({"event": "pump.setup.start", "t_ms": 1}),
            json.dumps({"event": "pump.setup.end", "setup_ms": 9, "t_ms": 10}),
        ]
        runs = parse_transfer_runs(aborted + _run_lines(base_t=1_000_000))
        assert len(runs) == 1  # only the completed run survives

    def test_unparseable_lines_skipped(self):
        lines = ["not json at all", ""] + _run_lines(base_t=1_000_000)
        assert len(parse_transfer_runs(lines)) == 1


class TestSummarizeRun:
    def test_headline_numbers(self):
        runs = parse_transfer_runs(_run_lines(
            base_t=1_000_000, setup_ms=120, chunks=3,
            tick_ms=400, setvalue_ms=30, audit_gap_ms=80, layer_count=90,
        ))
        s = summarize_run(runs[0])
        assert s["setup_ms"] == 120
        assert s["chunks"] == 3
        assert s["chunk_ms_sum"] == 3 * 400
        assert s["setvalue_ms_sum"] == 3 * 30
        assert s["audit_ms"] == 80
        assert s["layer_count"] == 90
        assert s["status"] == "COMPLETE"
        assert s["audit_pass"] is True
        # wall = complete.t_ms − setup.start.t_ms, positive and > known parts
        assert s["wall_ms"] is not None
        assert s["wall_ms"] >= s["setup_ms"] + s["chunk_ms_sum"] + s["audit_ms"]

    def test_failed_run_status(self):
        runs = parse_transfer_runs(_run_lines(base_t=1_000_000, status="FAILED"))
        s = summarize_run(runs[0])
        assert s["status"] == "FAILED"
        assert s["audit_pass"] is False

    def test_render_table_handles_missing_fields(self):
        out = render_table([{
            "chunks": 0, "status": None, "setup_ms": None,
            "chunk_ms_sum": 0.0, "audit_ms": None, "wall_ms": None,
        }])
        assert "—" in out


class TestParseOnlyCli:
    def test_parse_only_on_synthetic_log(self, tmp_path: Path, capsys):
        log = tmp_path / "transfer_status.log"
        log.write_text(
            "\n".join(_run_lines(base_t=1_000_000) + _run_lines(base_t=2_000_000))
            + "\n",
            encoding="utf-8",
        )
        rc = main(["--parse-only", str(log), "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert len(payload["runs"]) == 2
        assert payload["runs"][0]["chunks"] == 2

    def test_parse_only_missing_log_exits_2(self, tmp_path: Path, capsys):
        rc = main(["--parse-only", str(tmp_path / "nope.log")])
        assert rc == 2

    def test_parse_only_empty_log_ok(self, tmp_path: Path, capsys):
        log = tmp_path / "transfer_status.log"
        log.write_text("", encoding="utf-8")
        assert main(["--parse-only", str(log)]) == 0
        assert "No complete inject runs" in capsys.readouterr().out

    def test_sizes_validation(self, tmp_path: Path):
        import pytest

        with pytest.raises(SystemExit) as exc_info:
            main(["--sizes", "10,banana", "--repo", str(tmp_path)])
        assert exc_info.value.code == 2
        with pytest.raises(SystemExit) as exc_info:
            main(["--sizes", "0,10", "--repo", str(tmp_path)])
        assert exc_info.value.code == 2
        with pytest.raises(SystemExit) as exc_info:
            main(["--sizes", "10,500", "--repo", str(tmp_path)])
        assert exc_info.value.code == 2
