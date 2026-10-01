# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Unit tests for session_correlate and qa_common — no live AE required."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from tools.qa_common import (
    find_conform_reports_by_session,
    parse_transfer_timestamp,
    pick_best_inject_run,
    scan_log_by_session,
    scan_log_window,
    summarize_log_events,
)
from tools.session_correlate import (
    VERDICT_NOT_FOUND,
    VERDICT_SUCCESS,
    VERDICT_WARNINGS,
    correlate_session,
    main,
)

_REPO = Path(__file__).resolve().parents[2]


def _newest_repo_session_id() -> str | None:
    """Session id of the newest repo-root conform report, or None.

    The repo-root conform_report__*.json files are LIVE artifacts —
    every real conform run rewrites them with a fresh session id.
    Tests must never hardcode a specific session id (the original
    hardcoded C4E2EC2C went NOT_FOUND the first time Matt ran a live
    conform after the tests shipped); instead they exercise the same
    lookup machinery against whatever session is currently on disk.
    """
    reports = sorted(
        _REPO.glob("conform_report__*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in reports:
        try:
            meta = json.loads(path.read_text(encoding="utf-8")).get("meta") or {}
        except (OSError, json.JSONDecodeError):
            continue
        sid = meta.get("session_id")
        if isinstance(sid, str) and sid:
            return sid
    return None


class TestQaCommonParsing:
    def test_parse_transfer_timestamp(self):
        ts = parse_transfer_timestamp("Sun Jul 05 2026 17:38:31 GMT-0700")
        assert ts is not None
        assert ts.year == 2026
        assert ts.month == 7
        assert ts.day == 5
        assert ts.hour == 17
        assert ts.minute == 38

    def test_pick_best_inject_run_prefers_layer_count(self):
        runs = [
            {"layerCount": 9, "auditPass": True, "ts": "a"},
            {"layerCount": 23, "auditPass": True, "ts": "b"},
        ]
        best = pick_best_inject_run(runs, layer_count=23)
        assert best is not None
        assert best["layerCount"] == 23

    def test_summarize_log_events_extracts_warnings(self):
        records = [
            {"ts": "2026-07-05T17:38:25", "level": "WARNING", "msg": "overlap CUTOFF"},
            {"ts": "2026-07-05T17:38:26", "level": "INFO", "msg": "Report written", "path": "/tmp/r.html"},
            {"ts": "2026-07-05T17:38:31", "level": "INFO", "msg": "Job dispatched"},
            {"ts": "2026-07-05T17:38:35", "level": "INFO", "msg": "Conform complete — auditPass=true"},
        ]
        summary = summarize_log_events(records)
        assert summary["warnings"] == ["overlap CUTOFF"]
        assert summary["job_dispatched"] is True
        assert summary["conform_complete"] is True
        assert "/tmp/r.html" in summary["report_paths"]


class TestSessionCorrelateIntegration:
    def test_find_newest_live_report_in_repo(self):
        sid = _newest_repo_session_id()
        if sid is None:
            pytest.skip("no conform_report__*.json with a session_id at repo root")
        matches = find_conform_reports_by_session(_REPO, sid)
        assert len(matches) >= 1
        assert matches[0].meta.get("source_name")

    def test_correlate_known_session_success_or_warnings(self, tmp_path: Path):
        report_dir = tmp_path / "reports"
        report_dir.mkdir()
        report_path = report_dir / "conform_report__builtin_tiktok_video__1080x1920.json"
        report_path.write_text(
            json.dumps(
                {
                    "meta": {
                        "session_id": "TEST1234",
                        "source_name": "TestComp",
                        "target_w": 1080,
                        "target_h": 1920,
                        "scale_mode": "Fill",
                        "uniform_scale": 1.5,
                        "timestamp": "2026-07-05 17:38:25",
                        "total_layers": 23,
                        "total_chunks": 1,
                        "collapse_warnings": 0,
                    },
                    "layers": [],
                }
            ),
            encoding="utf-8",
        )

        log_path = tmp_path / "dimension.log"
        log_lines = [
            {
                "ts": "2026-07-05T17:38:24",
                "level": "INFO",
                "msg": "Scale engine conform started",
                "module": "scale_engine",
            },
            {
                "ts": "2026-07-05T17:38:25",
                "level": "INFO",
                "msg": "Report written",
                "module": "orchestrator",
                "path": str(report_path.with_suffix(".html")),
            },
            {
                "ts": "2026-07-05T17:38:30",
                "level": "INFO",
                "msg": "Job dispatched",
                "module": "inject",
            },
            {
                "ts": "2026-07-05T17:38:35",
                "level": "INFO",
                "msg": "Conform complete — auditPass=true",
                "module": "babysitter",
            },
        ]
        log_path.write_text(
            "\n".join(json.dumps(line) for line in log_lines) + "\n",
            encoding="utf-8",
        )

        transfer_path = tmp_path / "transfer_status.log"
        transfer_path.write_text(
            json.dumps(
                {
                    "status": "COMPLETE",
                    "auditPass": True,
                    "layerCount": 23,
                    "mirror_comp_count": 3,
                    "ts": "Sun Jul 05 2026 17:38:31 GMT-0700",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        # Symlink-style: copy report to repo-shaped tmp root
        (tmp_path / "conform_report__test.json").write_text(
            report_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        payload = correlate_session(
            tmp_path,
            "TEST1234",
            log_path=log_path,
            transfer_path=transfer_path,
        )
        assert payload["verdict"] in (VERDICT_SUCCESS, VERDICT_WARNINGS)
        assert payload["inject"]["best_match"]["layerCount"] == 23
        # Old-style log lines carry no session_id — the correlator must
        # fall back to the timestamp window (backward compat, M8).
        assert payload["log_match"] == "timestamp_window"

    def test_correlate_prefers_session_id_match(self, tmp_path: Path):
        """M8 — dimension.log lines stamped with session_id are matched
        exactly, beating the timestamp window: lines with the id are
        included even OUTSIDE the window, and in-window lines belonging
        to a different session are excluded."""
        report_path = tmp_path / "conform_report__builtin_tiktok_video__1080x1920.json"
        report_path.write_text(
            json.dumps(
                {
                    "meta": {
                        "session_id": "SIDMATCH",
                        "source_name": "TestComp",
                        "timestamp": "2026-07-05 17:38:25",
                        "total_layers": 23,
                        "collapse_warnings": 0,
                    },
                    "layers": [],
                }
            ),
            encoding="utf-8",
        )

        log_path = tmp_path / "dimension.log"
        log_lines = [
            # In-window, WRONG session — must be excluded.
            {
                "ts": "2026-07-05T17:38:26",
                "level": "WARNING",
                "msg": "other-session noise",
                "session_id": "OTHER111",
            },
            # In-window, no session id — excluded once sid-match kicks in.
            {
                "ts": "2026-07-05T17:38:27",
                "level": "WARNING",
                "msg": "unstamped noise",
            },
            # Stamped with our session — in window.
            {
                "ts": "2026-07-05T17:38:25",
                "level": "INFO",
                "msg": "Report written",
                "session_id": "SIDMATCH",
                "path": "/tmp/r.html",
            },
            # Stamped with our session — OUTSIDE the ±5/15min window.
            {
                "ts": "2026-07-05T19:00:00",
                "level": "INFO",
                "msg": "Conform complete — auditPass=true",
                "session_id": "SIDMATCH",
            },
        ]
        log_path.write_text(
            "\n".join(json.dumps(line) for line in log_lines) + "\n",
            encoding="utf-8",
        )

        payload = correlate_session(
            tmp_path,
            "SIDMATCH",
            log_path=log_path,
            transfer_path=tmp_path / "transfer_status.log",  # absent — fine
        )
        assert payload["log_match"] == "session_id"
        clog = payload["conform_log"]
        assert clog["record_count"] == 2
        assert clog["conform_complete"] is True
        assert "/tmp/r.html" in clog["report_paths"]
        assert clog["warnings"] == []  # neither noise line leaked in

    def test_dispatch_file_bridge_inject_logs_job_dispatched(self, tmp_path: Path):
        """M8 — the file-bridge inject dispatch emits the exact
        "Job dispatched" msg summarize_log_events keys on (the retired
        Qt launcher used to emit it; the CEP-era dispatch had no log
        line, leaving the correlator's job_dispatched signal dead).

        The "dimension" logger sets propagate=False, so caplog can't
        see it — attach a temporary capture handler instead."""
        import logging

        from core.logger import log as dim_log
        from stages.inject import dispatch_file_bridge_inject

        manifest = tmp_path / "chunk_manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        transfer = tmp_path / "transfer_status.log"
        transfer.write_text("", encoding="utf-8")

        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = _Capture(level=logging.INFO)
        dim_log.addHandler(handler)
        try:
            job_path = dispatch_file_bridge_inject(
                str(tmp_path), str(manifest), str(transfer)
            )
        finally:
            dim_log.removeHandler(handler)

        assert Path(job_path).is_file()
        dispatched = [r for r in records if r.getMessage() == "Job dispatched"]
        assert dispatched, "dispatch must emit the correlator's 'Job dispatched' msg"
        assert getattr(dispatched[0], "job", "").startswith("job_")

    def test_scan_log_by_session_case_insensitive_and_missing_file(self, tmp_path: Path):
        log_path = tmp_path / "dimension.log"
        log_path.write_text(
            json.dumps({"ts": "2026-07-05T17:38:25", "msg": "x", "session_id": "abcd1234"})
            + "\n",
            encoding="utf-8",
        )
        assert len(scan_log_by_session(log_path, "ABCD1234")) == 1
        assert scan_log_by_session(tmp_path / "nope.log", "ABCD1234") == []


def _write_report(path: Path, session_id: str, *, mtime_offset_s: float = 0.0) -> None:
    path.write_text(
        json.dumps({"meta": {"session_id": session_id, "source_name": "C"}}),
        encoding="utf-8",
    )
    if mtime_offset_s:
        import os
        ts = path.stat().st_mtime + mtime_offset_s
        os.utime(path, (ts, ts))


class TestSinceLast:
    def test_find_latest_session_id_picks_newest_by_mtime(self, tmp_path: Path):
        from tools.qa_common import find_latest_session_id

        _write_report(
            tmp_path / "conform_report__builtin_a__100x100.json",
            "OLDSESSN",
            mtime_offset_s=-3600,
        )
        _write_report(
            tmp_path / "conform_report__builtin_b__200x200.json",
            "newsessn",  # lower-case on disk — must normalize
        )
        assert find_latest_session_id(tmp_path) == "NEWSESSN"

    def test_find_latest_session_id_none_when_no_reports(self, tmp_path: Path):
        from tools.qa_common import find_latest_session_id

        assert find_latest_session_id(tmp_path) is None

    def test_cli_since_last_resolves_and_correlates(self, tmp_path: Path, capsys):
        _write_report(
            tmp_path / "conform_report__builtin_a__100x100.json", "AUTOPICK"
        )
        rc = main(["--since-last", "--repo", str(tmp_path), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["session_id"] == "AUTOPICK"
        assert payload["verdict"] != VERDICT_NOT_FOUND
        assert rc in (0, 1)  # found; verdict depends on log presence

    def test_cli_since_last_no_reports_exits_2(self, tmp_path: Path, capsys):
        rc = main(["--since-last", "--repo", str(tmp_path)])
        assert rc == 2

    def test_cli_rejects_both_id_and_since_last(self, tmp_path: Path):
        with pytest.raises(SystemExit) as exc_info:
            main(["ABCD1234", "--since-last", "--repo", str(tmp_path)])
        assert exc_info.value.code == 2  # argparse error

    def test_cli_rejects_neither(self, tmp_path: Path):
        with pytest.raises(SystemExit) as exc_info:
            main(["--repo", str(tmp_path)])
        assert exc_info.value.code == 2

    def test_correlate_not_found_exit_code(self, tmp_path: Path):
        rc = main(["NOPE0000", "--repo", str(tmp_path)])
        assert rc == 2

    def test_scan_log_window_filters_by_time(self, tmp_path: Path):
        log_path = tmp_path / "dimension.log"
        log_path.write_text(
            "\n".join(
                [
                    json.dumps({"ts": "2026-07-05T17:30:00", "msg": "early"}),
                    json.dumps({"ts": "2026-07-05T17:38:25", "msg": "in"}),
                    json.dumps({"ts": "2026-07-05T17:50:00", "msg": "late"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        start = datetime(2026, 7, 5, 17, 35, 0)
        end = datetime(2026, 7, 5, 17, 45, 0)
        records = scan_log_window(log_path, start, end)
        assert len(records) == 1
        assert records[0]["msg"] == "in"

    def test_cli_check_session_on_known_session(self):
        sid = _newest_repo_session_id()
        if sid is None:
            pytest.skip("no conform_report__*.json with a session_id at repo root")
        proc = subprocess.run(
            [
                sys.executable,
                str(_REPO / "python/tools/session_correlate.py"),
                sid,
                "--repo",
                str(_REPO),
                "--json",
            ],
            cwd=_REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode in (0, 1), proc.stderr or proc.stdout
        payload = json.loads(proc.stdout)
        assert payload["session_id"] == sid.upper()
        assert payload["verdict"] != VERDICT_NOT_FOUND