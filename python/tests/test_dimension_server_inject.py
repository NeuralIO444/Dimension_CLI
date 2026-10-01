# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Unit tests for dimension_server.py's file-bridge inject dispatch.

Covers the Option B fix (see .pipeline/plan.md §3/§7): `run_batch_thread`
now dispatches inject via `dispatch_file_bridge_inject()` (the correct,
already-tested, untyped file-bridge job shape) instead of the removed
`run_socket_inject()` (which built an invalid `"type": "inject"`
descriptor that `parse_typed_job()` always rejected).

No live AE/bridge required — `dispatch_file_bridge_inject` and
`run_batch_conform` are mocked; `tail_transfer_log_until_terminal` is
exercised directly against synthetic on-disk log files.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import dimension_server


# ── tail_transfer_log_until_terminal ────────────────────────────────


def test_tail_returns_promptly_on_complete_appended_mid_poll(tmp_path: Path):
    """A COMPLETE line appended after the tail loop has already started
    polling must be picked up on the very next poll tick, not held
    until timeout."""
    log_path = tmp_path / "transfer_status.log"
    log_path.write_text("")

    def _append_complete_soon():
        time.sleep(0.3)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"phase": "CHUNKS", "status": None}) + "\n")
            f.write(json.dumps({"status": "COMPLETE"}) + "\n")

    writer = threading.Thread(target=_append_complete_soon)
    writer.start()

    start = time.time()
    logs = dimension_server.tail_transfer_log_until_terminal(
        str(log_path), timeout_s=5.0, poll_interval_s=0.05
    )
    elapsed = time.time() - start
    writer.join()

    assert elapsed < 3.0, "tail loop should return promptly after COMPLETE, not wait for timeout"
    assert any(m.get("status") == "COMPLETE" for m in logs)
    assert any(m.get("phase") == "CHUNKS" for m in logs)


def test_tail_returns_on_failed_status(tmp_path: Path):
    """A FAILED line is also a terminal condition — the loop must return
    immediately (not keep polling for a COMPLETE that will never come)."""
    log_path = tmp_path / "transfer_status.log"
    log_path.write_text(json.dumps({"status": "FAILED", "error": "boom"}) + "\n")

    logs = dimension_server.tail_transfer_log_until_terminal(
        str(log_path), timeout_s=5.0, poll_interval_s=0.05
    )

    assert len(logs) == 1
    assert logs[0]["status"] == "FAILED"
    assert logs[0]["error"] == "boom"


def test_tail_returns_on_aborted_status(tmp_path: Path):
    """An ABORTED line is also a terminal condition (Track A Stage 1 —
    consumer support for the not-yet-built Babysitter abort producer,
    see docs/architecture/track-a-beacon-contract.md §7.4) — the loop
    must return immediately, not spin until TRANSFER_LOG_TAIL_TIMEOUT_S."""
    log_path = tmp_path / "transfer_status.log"
    log_path.write_text(
        json.dumps({"status": "ABORTED", "chunk_index": 2, "error": "User cancelled"}) + "\n"
    )

    start = time.time()
    logs = dimension_server.tail_transfer_log_until_terminal(
        str(log_path), timeout_s=5.0, poll_interval_s=0.05
    )
    elapsed = time.time() - start

    assert elapsed < 3.0, "tail loop should return promptly on ABORTED, not wait for timeout"
    assert len(logs) == 1
    assert logs[0]["status"] == "ABORTED"


def test_terminal_inject_statuses_constant_includes_all_three():
    """Locks the single-source-of-truth constant's membership so a
    future status can't be added to the tailer's check without also
    being added here — the whole point of Track A Stage 1."""
    assert set(dimension_server.TERMINAL_INJECT_STATUSES) == {
        "COMPLETE",
        "FAILED",
        "ABORTED",
    }


def test_terminal_inject_statuses_matches_stages_inject_mirror():
    """Parity guard for the mirrored-constant decision (Track A Stage 2b,
    .pipeline/plan.md §0) — without this, a future PR could edit one
    copy and not the other and nothing would fail."""
    from stages.inject import TERMINAL_INJECT_STATUSES as stages_terminal

    assert dimension_server.TERMINAL_INJECT_STATUSES == stages_terminal


def test_tail_respects_timeout_when_no_terminal_line(tmp_path: Path):
    """No COMPLETE/FAILED line ever appears — the loop must respect a
    short, test-injected timeout instead of hanging the test suite."""
    log_path = tmp_path / "transfer_status.log"
    log_path.write_text(json.dumps({"phase": "SETUP"}) + "\n")

    start = time.time()
    logs = dimension_server.tail_transfer_log_until_terminal(
        str(log_path), timeout_s=0.5, poll_interval_s=0.05
    )
    elapsed = time.time() - start

    assert elapsed < 2.0, "tail loop must not run substantially longer than its own timeout"
    assert elapsed >= 0.5
    assert any(m.get("phase") == "SETUP" for m in logs)
    assert not any(m.get("status") in ("COMPLETE", "FAILED") for m in logs)


def test_tail_skips_malformed_json_lines(tmp_path: Path):
    """Non-JSON lines are skipped (matching the JS tailer's behavior),
    not treated as a fatal error."""
    log_path = tmp_path / "transfer_status.log"
    log_path.write_text(
        "not json at all\n"
        + json.dumps({"phase": "INJECT"}) + "\n"
        + json.dumps({"status": "COMPLETE"}) + "\n"
    )

    logs = dimension_server.tail_transfer_log_until_terminal(
        str(log_path), timeout_s=5.0, poll_interval_s=0.05
    )

    assert len(logs) == 2
    assert logs[0]["phase"] == "INJECT"
    assert logs[1]["status"] == "COMPLETE"


def test_tail_missing_file_respects_timeout(tmp_path: Path):
    """transfer_status.log doesn't exist yet (dispatch raced ahead of
    Babysitter creating it) — must not crash, must respect timeout."""
    log_path = tmp_path / "does_not_exist.log"

    start = time.time()
    logs = dimension_server.tail_transfer_log_until_terminal(
        str(log_path), timeout_s=0.3, poll_interval_s=0.05
    )
    elapsed = time.time() - start

    assert elapsed < 2.0
    assert logs == []


# ── run_batch_thread dispatch wiring ────────────────────────────────


def test_run_batch_thread_calls_dispatch_file_bridge_inject_with_correct_args(
    tmp_path: Path,
):
    """run_batch_thread must dispatch inject via dispatch_file_bridge_inject
    (project_root, chunk_manifest_path, transfer_log_path) — not the
    removed run_socket_inject — and must not attempt to load a
    monolithic in-memory manifest first."""
    project_root = tmp_path
    chunk_manifest = project_root / "chunk_manifest.json"
    chunk_manifest.write_text(json.dumps({"chunk_paths": []}))
    transfer_log = project_root / "transfer_status.log"

    with patch("dimension_server.run_batch_conform") as mock_run_batch_conform, \
         patch("dimension_server.dispatch_file_bridge_inject") as mock_dispatch, \
         patch("dimension_server.tail_transfer_log_until_terminal") as mock_tail:
        mock_run_batch_conform.return_value = (1, ["conformed ok"])
        mock_dispatch.return_value = str(project_root / ".dimension_inbox" / "job_x.json")
        mock_tail.return_value = [{"status": "COMPLETE"}]

        dimension_server.run_batch_thread(
            manifest_path=str(project_root / "scrape_manifest.json"),
            profile="default",
            mode="Fit",
            bleed=0.0,
            targets=[{"preset": "tiktok_vertical"}],
            project_root=str(project_root),
        )

        mock_dispatch.assert_called_once_with(
            str(project_root), str(chunk_manifest), str(transfer_log)
        )
        mock_tail.assert_called_once_with(str(transfer_log))

    with dimension_server.execution_lock:
        assert dimension_server.execution_state["status"] == "success"


def test_run_batch_thread_marks_failed_on_dispatch_exception(tmp_path: Path):
    """If dispatch_file_bridge_inject raises, run_batch_thread must
    surface a failed status with an actionable error — not crash the
    background thread silently."""
    project_root = tmp_path
    chunk_manifest = project_root / "chunk_manifest.json"
    chunk_manifest.write_text(json.dumps({"chunk_paths": []}))

    with patch("dimension_server.run_batch_conform") as mock_run_batch_conform, \
         patch("dimension_server.dispatch_file_bridge_inject") as mock_dispatch:
        mock_run_batch_conform.return_value = (1, ["conformed ok"])
        mock_dispatch.side_effect = RuntimeError("no poller heartbeat")

        dimension_server.run_batch_thread(
            manifest_path=str(project_root / "scrape_manifest.json"),
            profile="default",
            mode="Fit",
            bleed=0.0,
            targets=[{"preset": "tiktok_vertical"}],
            project_root=str(project_root),
        )

    with dimension_server.execution_lock:
        assert dimension_server.execution_state["status"] == "failed"
        assert "no poller heartbeat" in dimension_server.execution_state["error"]


def test_run_batch_thread_marks_failed_when_tail_reports_failed_status(
    tmp_path: Path,
):
    """A FAILED terminal status from the tail loop must propagate to a
    failed execution_state with the AE-reported error message."""
    project_root = tmp_path
    chunk_manifest = project_root / "chunk_manifest.json"
    chunk_manifest.write_text(json.dumps({"chunk_paths": []}))

    with patch("dimension_server.run_batch_conform") as mock_run_batch_conform, \
         patch("dimension_server.dispatch_file_bridge_inject") as mock_dispatch, \
         patch("dimension_server.tail_transfer_log_until_terminal") as mock_tail:
        mock_run_batch_conform.return_value = (1, ["conformed ok"])
        mock_dispatch.return_value = "job_path"
        mock_tail.return_value = [{"status": "FAILED", "error": "Babysitter eval failed"}]

        dimension_server.run_batch_thread(
            manifest_path=str(project_root / "scrape_manifest.json"),
            profile="default",
            mode="Fit",
            bleed=0.0,
            targets=[{"preset": "tiktok_vertical"}],
            project_root=str(project_root),
        )

    with dimension_server.execution_lock:
        assert dimension_server.execution_state["status"] == "failed"
        assert dimension_server.execution_state["error"] == "Babysitter eval failed"


# ── run_batch_thread per-target results + cancel ────────────────────


def _reset_batch_state():
    with dimension_server.execution_lock:
        dimension_server.execution_state["target_results"] = []
        dimension_server.execution_state["cancel_requested"] = False
        dimension_server.execution_state["project_root"] = None
        dimension_server.execution_state["error"] = None
        dimension_server.execution_state["status"] = "idle"


def test_run_batch_thread_records_per_target_results(tmp_path: Path):
    """Each target's own outcome must land in execution_state["target_results"],
    not just the aggregate success_count — this is what the Retry Failed
    UI reads to know which target(s) to re-queue."""
    _reset_batch_state()
    project_root = tmp_path
    chunk_manifest = project_root / "chunk_manifest.json"
    chunk_manifest.write_text(json.dumps({"chunk_paths": []}))

    with patch("dimension_server.run_batch_conform") as mock_run_batch_conform, \
         patch("dimension_server.dispatch_file_bridge_inject") as mock_dispatch, \
         patch("dimension_server.tail_transfer_log_until_terminal") as mock_tail:
        mock_run_batch_conform.side_effect = [(1, ["ok"]), (0, ["conform error"])]
        mock_dispatch.return_value = "job_path"
        mock_tail.return_value = [{"status": "COMPLETE"}]

        dimension_server.run_batch_thread(
            manifest_path=str(project_root / "scrape_manifest.json"),
            profile="default",
            mode="Fit",
            bleed=0.0,
            targets=[{"name": "TikTok"}, {"name": "Reels"}],
            project_root=str(project_root),
        )

    with dimension_server.execution_lock:
        results = dimension_server.execution_state["target_results"]
        assert len(results) == 2
        assert results[0]["label"] == "TikTok"
        assert results[0]["success"] is True
        assert results[0]["error"] is None
        assert results[1]["label"] == "Reels"
        assert results[1]["success"] is False
        assert results[1]["error"] is not None
        assert results[1]["target"] == {"name": "Reels"}
        # One success out of two is enough to proceed to inject.
        assert dimension_server.execution_state["status"] == "success"


def test_run_batch_thread_cancel_before_start_skips_conform_and_inject(tmp_path: Path):
    """A cancel requested before run_batch_thread even begins its loop
    must stop it from conforming or injecting anything at all."""
    _reset_batch_state()
    with dimension_server.execution_lock:
        dimension_server.execution_state["cancel_requested"] = True

    project_root = tmp_path

    with patch("dimension_server.run_batch_conform") as mock_run_batch_conform, \
         patch("dimension_server.dispatch_file_bridge_inject") as mock_dispatch:
        dimension_server.run_batch_thread(
            manifest_path=str(project_root / "scrape_manifest.json"),
            profile="default",
            mode="Fit",
            bleed=0.0,
            targets=[{"name": "TikTok"}],
            project_root=str(project_root),
        )
        mock_run_batch_conform.assert_not_called()
        mock_dispatch.assert_not_called()

    with dimension_server.execution_lock:
        assert dimension_server.execution_state["status"] == "cancelled"
        assert dimension_server.execution_state["target_results"] == []


def test_run_batch_thread_cancel_mid_batch_injects_completed_targets(tmp_path: Path):
    """A cancel requested between targets must stop further conforms but
    still inject whatever already succeeded — completed work shouldn't
    be thrown away just because the rest of the queue was cancelled."""
    _reset_batch_state()
    project_root = tmp_path
    chunk_manifest = project_root / "chunk_manifest.json"
    chunk_manifest.write_text(json.dumps({"chunk_paths": []}))

    def _conform_then_cancel(*args, **kwargs):
        with dimension_server.execution_lock:
            dimension_server.execution_state["cancel_requested"] = True
        return (1, ["ok"])

    with patch("dimension_server.run_batch_conform", side_effect=_conform_then_cancel) as mock_run_batch_conform, \
         patch("dimension_server.dispatch_file_bridge_inject") as mock_dispatch, \
         patch("dimension_server.tail_transfer_log_until_terminal") as mock_tail:
        mock_dispatch.return_value = "job_path"
        mock_tail.return_value = [{"status": "COMPLETE"}]

        dimension_server.run_batch_thread(
            manifest_path=str(project_root / "scrape_manifest.json"),
            profile="default",
            mode="Fit",
            bleed=0.0,
            targets=[{"name": "TikTok"}, {"name": "Reels"}],
            project_root=str(project_root),
        )

        # Only the first target should ever have been attempted.
        assert mock_run_batch_conform.call_count == 1
        mock_dispatch.assert_called_once()

    with dimension_server.execution_lock:
        assert dimension_server.execution_state["status"] == "cancelled"
        assert len(dimension_server.execution_state["target_results"]) == 1


# ── run_batch_thread inject progress-bar monotonic clamp ────────────


def test_run_batch_thread_on_phase_never_moves_progress_bar_backward(tmp_path: Path):
    """Track A Stage 2b: run_batch_thread's local `_on_phase` closure must
    clamp the Dashboard's progress bar monotonically, mirroring
    cep/js/inject_progress_ui.js's `_setInjectBarPct`. Without the clamp, a chunk line
    that interpolates the bar above 93 followed by a real `{phase:
    "INJECT"}` anchor (which stages/inject.py's corrected
    INJECT_PHASE_PROGRESS now actually fires, unlike its old dead-key
    map) would visibly snap the bar backward to 93."""
    _reset_batch_state()
    project_root = tmp_path
    chunk_manifest = project_root / "chunk_manifest.json"
    chunk_manifest.write_text(json.dumps({"chunk_paths": []}))

    pct_history: list[int] = []
    real_state = dict(dimension_server.execution_state)

    class _RecordingState(dict):
        def __setitem__(self, key, value):
            if key == "progress_pct":
                pct_history.append(value)
            super().__setitem__(key, value)

    recording_state = _RecordingState(real_state)

    with patch("dimension_server.run_batch_conform") as mock_run_batch_conform, \
         patch("dimension_server.dispatch_file_bridge_inject") as mock_dispatch, \
         patch("dimension_server.tail_transfer_log_until_terminal") as mock_tail, \
         patch.object(dimension_server, "execution_state", recording_state):
        mock_run_batch_conform.return_value = (1, ["conformed ok"])
        mock_dispatch.return_value = "job_path"
        # Chunk line interpolates the bar into the 95-97 band, THEN a
        # real INJECT phase anchor (93) fires — the exact backward-jump
        # shape `{ phase: "INJECT", event: "skip_inject_summary" }` can
        # produce mid-chunk-loop per the beacon contract.
        mock_tail.return_value = [
            {"chunkIndex": 3, "totalChunks": 4},
            {"phase": "INJECT"},
            {"status": "COMPLETE"},
        ]

        dimension_server.run_batch_thread(
            manifest_path=str(project_root / "scrape_manifest.json"),
            profile="default",
            mode="Fit",
            bleed=0.0,
            targets=[{"preset": "tiktok_vertical"}],
            project_root=str(project_root),
        )

    # Every progress_pct write recorded once the bar entered the inject
    # band (>= 92, where run_batch_thread starts the inject phase) must
    # be non-decreasing — the clamp must have rejected the backward
    # INJECT=93 write after the chunk line already pushed the bar past it.
    inject_band_values = [v for v in pct_history if v >= 92]
    assert inject_band_values == sorted(inject_band_values)
    assert 93 not in inject_band_values[1:], "clamp failed to reject a backward INJECT=93 write"


# ── Track A Phase A3 — abort flag + Dashboard outcome ───────────────


def test_run_batch_thread_does_not_erase_a_cancel_written_before_dispatch(tmp_path: Path):
    """Audit fix (2026-08-27) regression test. run_batch_thread used to
    unconditionally clear_inject_abort_request() right before dispatch,
    intended only to wipe a leftover flag from a PRIOR run. But that
    call landed several statements after progress_pct hit 90 — the
    threshold the cancel route uses to decide "write the abort flag" —
    creating a real TOCTOU window: a cancel written in that window got
    silently deleted before Babysitter ever polled for it, while the
    Dashboard had already told the user "cancel requested."

    Simulates the flag having been written in that window (deterministic
    stand-in for timing a real thread race) and asserts run_batch_thread
    no longer touches it — the clear now happens once, at the true start
    of a fresh /api/execute-batch request, tested separately in
    test_dimension_server_routes.py."""
    _reset_batch_state()
    project_root = tmp_path
    chunk_manifest = project_root / "chunk_manifest.json"
    chunk_manifest.write_text(json.dumps({"chunk_paths": []}))

    abort_path = dimension_server.write_inject_abort_request(
        str(project_root), source="dashboard"
    )
    assert os.path.exists(abort_path)

    with patch("dimension_server.run_batch_conform") as mock_run_batch_conform, \
         patch("dimension_server.dispatch_file_bridge_inject") as mock_dispatch, \
         patch("dimension_server.tail_transfer_log_until_terminal") as mock_tail:
        mock_run_batch_conform.return_value = (1, ["ok"])
        mock_dispatch.return_value = "job_path"
        mock_tail.return_value = [{"status": "COMPLETE"}]

        dimension_server.run_batch_thread(
            manifest_path=str(project_root / "scrape_manifest.json"),
            profile="default",
            mode="Fit",
            bleed=0.0,
            targets=[{"name": "TikTok"}],
            project_root=str(project_root),
        )

    assert os.path.exists(abort_path), (
        "run_batch_thread erased an abort flag that was written before "
        "dispatch — a real cancel for this exact run would be silently "
        "lost"
    )


def test_write_and_clear_inject_abort_request(tmp_path: Path):
    path = dimension_server.write_inject_abort_request(str(tmp_path), source="dashboard")
    assert Path(path).name == dimension_server.INJECT_ABORT_REQUEST_FILENAME
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload["requested"] is True
    assert payload["source"] == "dashboard"
    dimension_server.clear_inject_abort_request(str(tmp_path))
    assert not Path(path).exists()
    dimension_server.clear_inject_abort_request(str(tmp_path))  # missing is fine


def test_run_batch_thread_aborted_inject_is_cancelled_not_timeout(tmp_path: Path):
    """E6: an ABORTED terminal must not fall through to the 30-minute
    timeout message. Same PR as the A3 producer (E5)."""
    _reset_batch_state()
    project_root = tmp_path
    (project_root / "chunk_manifest.json").write_text(json.dumps({"chunk_paths": []}))

    with patch("dimension_server.run_batch_conform") as mock_run_batch_conform, \
         patch("dimension_server.dispatch_file_bridge_inject") as mock_dispatch, \
         patch("dimension_server.tail_transfer_log_until_terminal") as mock_tail:
        mock_run_batch_conform.return_value = (1, ["ok"])
        mock_dispatch.return_value = "job_path"
        mock_tail.return_value = [
            {"phase": "INJECT"},
            {
                "status": "ABORTED",
                "phase": "chunk",
                "chunkIndex": 2,
                "totalChunks": 5,
                "error": "User cancelled",
            },
        ]

        dimension_server.run_batch_thread(
            manifest_path=str(project_root / "scrape_manifest.json"),
            profile="default",
            mode="Fit",
            bleed=0.0,
            targets=[{"preset": "tiktok_vertical"}],
            project_root=str(project_root),
        )

    with dimension_server.execution_lock:
        assert dimension_server.execution_state["status"] == "cancelled"
        text = dimension_server.execution_state["progress_text"].lower()
        assert "timed out" not in text
        assert "cancel" in text
        assert dimension_server.execution_state["error"] is None


def test_abort_filename_agrees_across_cep_python_and_jsx():
    """S3: the abort-flag filename is law. A drift here is a silent
    cancel that never stops Babysitter."""
    repo = Path(__file__).resolve().parents[2]
    name = dimension_server.INJECT_ABORT_REQUEST_FILENAME
    assert name == "inject_abort_request.json"
    babysitter = (repo / "Scripts" / "Dimension_Assets" / "Babysitter.jsx").read_text(
        encoding="utf-8"
    )
    bridge = (repo / "cep" / "js" / "backend_bridge.js").read_text(encoding="utf-8")
    assert f'"{name}"' in babysitter or f"'{name}'" in babysitter
    assert name in babysitter
    assert f"'{name}'" in bridge or f'"{name}"' in bridge
    assert "BB.INJECT_ABORT_REQUEST_FILENAME" in bridge
