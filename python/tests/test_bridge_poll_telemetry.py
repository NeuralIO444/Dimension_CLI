# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_bridge_poll_telemetry.py
2026-07-07 — coverage for the bridge poll-cadence telemetry added to
`SovereignBridge._wait_for_result` / `_emit_poll_telemetry`.

CLAUDE.md's "File-bridge polling intervals" sharp edge notes
`_POLL_INTERVAL_S` (100ms) was "tested empirically" years ago but
nothing measures it today. This instrumentation appends one
`event: "telemetry"` line (phase `python_bridge_poll`) to
transfer_status.log per successful bridge round-trip, carrying the
dispatch timestamp, the result-detected timestamp, the delta, and the
number of poll ticks that returned empty before the result appeared.

Tests here cover the three things the task calls out explicitly:
  1. telemetry is emitted on a successful round-trip
  2. an emission failure never breaks the underlying bridge job
  3. the emitted line parses as the expected structure

No AE, no JSX. Mirrors the file-bridge mocking pattern already used
by test_bridge_validation.py / test_duplication_bridge.py: a
background thread watches the tmp-path inbox and writes a synthetic
JSX-side result.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from models.bridge_jobs import BRIDGE_SCHEMA_VERSION  # noqa: E402


# ── Fixtures + helpers ───────────────────────────────────────────


@pytest.fixture()
def bridge_factory(tmp_path, monkeypatch):
    """Yield a builder for SovereignBridge with poller-alive
    patched true and a tmp project root — mirrors
    test_bridge_validation.py's fixture of the same name."""
    from bridge.sovereign_bridge import SovereignBridge

    def _make():
        b = SovereignBridge(project_root=str(tmp_path))
        monkeypatch.setattr(b, "_socket_is_listening", lambda: False)
        os.makedirs(b._inbox_dir(), exist_ok=True)
        Path(b._heartbeat_path()).write_text("tick_count=0\n", encoding="utf-8")
        return b

    return _make


def _spawn_responder(inbox: Path, response: dict, *,
                      job_type: str = "tag-write",
                      delay_s: float = 0.0):
    """Background thread that watches the inbox for a typed job
    descriptor matching job_type, then writes the response to the
    result path after an optional delay (used to force a few empty
    poll ticks before the result appears)."""
    stop = threading.Event()

    def run():
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not stop.is_set():
            for name in os.listdir(str(inbox)):
                if (not name.startswith("job_")
                        or not name.endswith(".json")
                        or name.endswith(".result.json")
                        or name.endswith(".tmp")):
                    continue
                full = inbox / name
                try:
                    desc = json.loads(full.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if desc.get("type") != job_type:
                    continue
                if delay_s:
                    time.sleep(delay_s)
                result_path = Path(desc["result"])
                tmp = result_path.with_suffix(result_path.suffix + ".tmp")
                tmp.write_text(json.dumps(response), encoding="utf-8")
                os.replace(tmp, result_path)
                return
            time.sleep(0.02)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, stop


def _read_telemetry_lines(transfer_log: Path, phase: str) -> list[dict]:
    if not transfer_log.exists():
        return []
    out = []
    for line in transfer_log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)  # must be valid single-line JSON
        if entry.get("event") == "telemetry" and entry.get("phase") == phase:
            out.append(entry)
    return out


_OK_RESPONSE = {
    "schema_version": BRIDGE_SCHEMA_VERSION,
    "job_type": "tag-write",
    "status": "OK",
    "uid": "u-1",
    "tag": "HERO",
    "source": "manual_comment",
    "label": 1,
}


# ── 1. Telemetry emitted on a successful round-trip ──────────────


class TestTelemetryEmittedOnSuccess:
    def test_emits_one_python_bridge_poll_line(self, bridge_factory):
        bridge = bridge_factory()
        responder, stop = _spawn_responder(
            Path(bridge._inbox_dir()), _OK_RESPONSE, job_type="tag-write",
        )
        try:
            result = bridge.apply_manual_tag("u-1", "HERO", timeout_s=4.0)
        finally:
            stop.set(); responder.join(timeout=2)

        assert result["status"] == "OK"

        lines = _read_telemetry_lines(
            Path(bridge._transfer_log_path()), "python_bridge_poll",
        )
        assert len(lines) == 1, (
            f"expected exactly one python_bridge_poll telemetry line, got {lines}"
        )

    def test_poll_ticks_empty_reflects_actual_delay(self, bridge_factory):
        """Force the responder to wait past a couple of 100ms poll
        ticks before writing the result — poll_ticks_empty must be
        > 0 and duration_ms must be roughly consistent with the
        forced delay (loose bound; this is wall-clock, not exact)."""
        bridge = bridge_factory()
        responder, stop = _spawn_responder(
            Path(bridge._inbox_dir()), _OK_RESPONSE, job_type="tag-write",
            delay_s=0.25,
        )
        try:
            bridge.apply_manual_tag("u-1", "HERO", timeout_s=4.0)
        finally:
            stop.set(); responder.join(timeout=2)

        lines = _read_telemetry_lines(
            Path(bridge._transfer_log_path()), "python_bridge_poll",
        )
        assert len(lines) == 1
        entry = lines[0]
        assert entry["poll_ticks_empty"] >= 1
        assert entry["duration_ms"] >= 100  # at least one full poll period


# ── 2. Emission failure never breaks the job ─────────────────────


class TestEmissionFailureDoesNotBreakJob:
    def test_unwritable_transfer_log_path_does_not_raise(
        self, bridge_factory, monkeypatch,
    ):
        """Point _transfer_log_path at a directory (not a file) so
        the internal `open(path, "a")` raises IsADirectoryError.
        `_emit_poll_telemetry` must swallow it — the underlying
        bridge job must still complete and return OK."""
        bridge = bridge_factory()

        bad_log_dir = Path(bridge.project_root) / "transfer_status.log"
        bad_log_dir.mkdir(parents=True, exist_ok=True)  # a dir, not a file
        monkeypatch.setattr(
            bridge, "_transfer_log_path", lambda: str(bad_log_dir),
        )

        responder, stop = _spawn_responder(
            Path(bridge._inbox_dir()), _OK_RESPONSE, job_type="tag-write",
        )
        try:
            result = bridge.apply_manual_tag("u-1", "HERO", timeout_s=4.0)
        finally:
            stop.set(); responder.join(timeout=2)

        assert result["status"] == "OK", (
            "a telemetry emission failure must never break the bridge job"
        )

    def test_emit_poll_telemetry_swallows_exception_directly(
        self, bridge_factory, monkeypatch,
    ):
        """Unit-level check on the helper itself: any exception raised
        while building/writing the line must not propagate."""
        bridge = bridge_factory()

        def _boom():
            raise OSError("disk full (simulated)")

        monkeypatch.setattr(bridge, "_transfer_log_path", _boom)

        # Must not raise.
        bridge._emit_poll_telemetry(
            job_type="tag-write",
            dispatch_ms=1_000,
            result_ms=1_100,
            poll_ticks_empty=1,
        )


# ── 3. The emitted line parses as the expected structure ─────────


class TestTelemetryLineShape:
    def test_line_has_expected_fields_and_types(self, bridge_factory):
        bridge = bridge_factory()
        responder, stop = _spawn_responder(
            Path(bridge._inbox_dir()), _OK_RESPONSE, job_type="tag-write",
        )
        try:
            bridge.apply_manual_tag("u-1", "HERO", timeout_s=4.0)
        finally:
            stop.set(); responder.join(timeout=2)

        lines = _read_telemetry_lines(
            Path(bridge._transfer_log_path()), "python_bridge_poll",
        )
        assert len(lines) == 1
        entry = lines[0]

        # Envelope shared with every other event:"telemetry" line in
        # transfer_status.log (see Babysitter.jsx's _writeLog callers
        # and python/tools/measure_chunk_overhead.py's parser).
        assert entry["event"] == "telemetry"
        assert entry["phase"] == "python_bridge_poll"

        assert entry["job_type"] == "tag-write"
        assert isinstance(entry["dispatch_ms"], int)
        assert isinstance(entry["result_ms"], int)
        assert isinstance(entry["duration_ms"], (int, float))
        assert isinstance(entry["poll_ticks_empty"], int)
        assert entry["poll_ticks_empty"] >= 0
        assert isinstance(entry["t_ms"], int)

        # Internal consistency.
        assert entry["t_ms"] == entry["result_ms"]
        assert entry["duration_ms"] == pytest.approx(
            entry["result_ms"] - entry["dispatch_ms"], abs=0.001,
        )

    def test_line_is_single_line_newline_delimited_json(self, bridge_factory):
        """Guards against the macOS append-mode \\n quirk documented on
        Babysitter.jsx's _writeLog — the Python writer must produce one
        JSON object per line, not a concatenated blob."""
        bridge = bridge_factory()
        responder, stop = _spawn_responder(
            Path(bridge._inbox_dir()), _OK_RESPONSE, job_type="tag-write",
        )
        try:
            bridge.apply_manual_tag("u-1", "HERO", timeout_s=4.0)
        finally:
            stop.set(); responder.join(timeout=2)

        raw = Path(bridge._transfer_log_path()).read_text(encoding="utf-8")
        assert raw.endswith("\n")
        non_empty_lines = [l for l in raw.splitlines() if l.strip()]
        for line in non_empty_lines:
            json.loads(line)  # each line must independently parse

    def test_unknown_job_type_falls_back_to_unknown(self, bridge_factory):
        """When the JSX result omits job_type, the telemetry line
        still gets written with a safe fallback rather than a
        missing/null field (mirrors _emit_bridge_handshake's
        `payload.get("job_type") or "unknown"` convention)."""
        bridge = bridge_factory()
        response = dict(_OK_RESPONSE)
        del response["job_type"]
        responder, stop = _spawn_responder(
            Path(bridge._inbox_dir()), response, job_type="tag-write",
        )
        try:
            # Missing job_type fails schema validation deeper in
            # _wait_for_result (ScrapeEngineError) — telemetry is
            # emitted before that validation runs, so it still lands.
            from bridge.sovereign_bridge import ScrapeEngineError
            with pytest.raises(ScrapeEngineError):
                bridge.apply_manual_tag("u-1", "HERO", timeout_s=4.0)
        finally:
            stop.set(); responder.join(timeout=2)

        lines = _read_telemetry_lines(
            Path(bridge._transfer_log_path()), "python_bridge_poll",
        )
        assert len(lines) == 1
        assert lines[0]["job_type"] == "unknown"
