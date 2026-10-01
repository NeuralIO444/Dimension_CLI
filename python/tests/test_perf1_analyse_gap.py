# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_perf1_analyse_gap.py

Tests for python/scripts/perf1_analyse_gap.py — the PERF-1 gap-bisection
log analyzer — against SYNTHETIC transfer_status.log content.

Scope honesty (CLAUDE.md anti-pattern "Trusting synthetic Python test
fixtures to prove a JSX↔Python integration contract"): these fixtures
prove the analyzer's parsing and arithmetic, NOT that Babysitter.jsx
actually emits these lines at runtime. The companion static guard
(test_perf1_telemetry_markers.py) proves the emitting source text
exists; only Matt's live AE inject run proves the wire format. The
fixture line shapes below were copied from the actual _writeLog call
sites in Babysitter.jsx (commit 8990488), not invented.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.perf1_analyse_gap import analyse, main, parse_lines  # noqa: E402

# Base wall-clock; arbitrary but realistic epoch-ms.
T = 1_780_000_000_000


def _healthy_records(dead_ms: int = 13_100) -> list[dict]:
    """A minimal, realistic rewire→chunk sequence. Shapes mirror the
    real _writeLog call sites (every line carries t_ms because
    _writeLog auto-stamps it)."""
    return [
        {"event": "pump.enter", "phase": "rewire", "chunk_index": None, "t_ms": T},
        # endUndoGroup took 150ms; t_ms is stamped AFTER the call returns
        {"event": "telemetry", "phase": "rewire_undo_close", "duration_ms": 150, "t_ms": T + 200},
        {"event": "pump.rewire.schedule", "t_ms": T + 210},
        {"event": "telemetry", "phase": "schedule_task_call", "duration_ms": 5, "t_ms": T + 215},
        # AE sits busy for dead_ms before the scheduled task fires
        {"event": "pump.enter", "phase": "chunk", "chunk_index": 0, "t_ms": T + 215 + dead_ms},
        {"event": "pump.chunk.start", "chunk_index": 0, "t_ms": T + 220 + dead_ms},
    ]


class TestAnalyseHealthy:
    def test_segments_telescope_to_total(self):
        result = analyse(_healthy_records())
        assert result["ok"], result["errors"]
        assert sum(result["segments"].values()) == result["total_ms"]

    def test_dominant_segment_is_scheduler_dead_time(self):
        result = analyse(_healthy_records(dead_ms=13_100))
        assert result["ok"]
        assert result["dominant"].startswith("scheduler_dead"), result["dominant"]
        assert result["segments"]["scheduler_dead (AE busy pre-fire)"] == 13_100

    def test_undo_close_dominates_when_commit_is_slow(self):
        """If Hypothesis 2a is right (deferred recalc inside endUndoGroup),
        the analyzer must attribute the gap there instead."""
        recs = _healthy_records(dead_ms=50)
        recs[1]["duration_ms"] = 12_900  # slow commit; t_ms unchanged shape
        recs[1]["t_ms"] = T + 12_950
        recs[2]["t_ms"] = T + 12_960
        recs[3]["t_ms"] = T + 12_965
        recs[4]["t_ms"] = T + 12_965 + 50
        recs[5]["t_ms"] = T + 12_970 + 50
        result = analyse(recs)
        assert result["ok"], result["errors"]
        assert result["dominant"].startswith("undo_close"), result["dominant"]

    def test_uses_last_run_in_appended_log(self):
        """transfer_status.log can carry multiple runs; the analyzer must
        bisect the most recent one."""
        stale = _healthy_records(dead_ms=1)
        fresh = [dict(r, t_ms=r["t_ms"] + 60_000) for r in _healthy_records(dead_ms=9_000)]
        result = analyse(stale + fresh)
        assert result["ok"]
        assert result["segments"]["scheduler_dead (AE busy pre-fire)"] == 9_000


class TestAnalyseFailureModes:
    def test_missing_undo_marker_fails_with_stale_jsx_hint(self):
        recs = [r for r in _healthy_records() if r.get("phase") != "rewire_undo_close"]
        result = analyse(recs)
        assert not result["ok"]
        assert any("stale JSX" in e for e in result["errors"])

    def test_missing_downstream_marker_fails(self):
        recs = [r for r in _healthy_records() if r.get("event") != "pump.rewire.schedule"]
        result = analyse(recs)
        assert not result["ok"]
        assert any("pump.rewire.schedule" in e for e in result["errors"])

    def test_broken_ordering_fails(self):
        recs = _healthy_records()
        recs[4]["t_ms"] = T - 500  # chunk pump.enter before the rewire — impossible
        result = analyse(recs)
        assert not result["ok"]
        assert any("ordering broken" in e for e in result["errors"])


class TestCliRoundTrip:
    def test_parse_lines_skips_non_json_banner_lines(self, tmp_path):
        log = tmp_path / "transfer_status.log"
        lines = ["=== Babysitter v6 ==="] + [
            __import__("json").dumps(r) for r in _healthy_records()
        ]
        log.write_text("\n".join(lines), encoding="utf-8")
        assert len(parse_lines(log)) == 6

    def test_main_exit_codes(self, tmp_path, capsys):
        import json as _json

        good = tmp_path / "good.log"
        good.write_text(
            "\n".join(_json.dumps(r) for r in _healthy_records()), encoding="utf-8"
        )
        assert main([str(good)]) == 0
        assert "DOMINANT" in capsys.readouterr().out

        bad = tmp_path / "bad.log"
        bad.write_text('{"event": "pump.enter", "phase": "setup", "t_ms": 1}\n', encoding="utf-8")
        assert main([str(bad)]) == 1
