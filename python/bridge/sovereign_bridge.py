# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/bridge/sovereign_bridge.py
Dimension — Python ↔ After Effects scrape bridge.

v5.1.8+ — Replaced legacy osascript/DoScript transport.
    Bridge jobs route through `_execute_bridge_job`: prefer TCP socket
    (port 45445, `core.ipc_client.execute_job`) when `Socket.listen`
    succeeds in AE; fall back to the inbox file-bridge
    (`_write_job_file` / `_wait_for_result`) when the poller heartbeat
    is fresh (AE 2026 socket listen I/O error workaround — see
    `docs/architecture/SOCKET-SERVER-AE2026.md`).  No osascript, no
    AppleEvent, no DoScript back-pressure.

Responsibilities:
    1. Pre-flight the AE-side poller heartbeat (reuses the launcher's
       HEARTBEAT_PATH / HEARTBEAT_STALE_SECONDS contract so the dialog
       copy matches inject).
    2. Dispatch a scrape job via atomic .tmp → .json rename.
    3. Poll for `{job_id}.result.json`, bounded by timeout.
    4. Atomic-rewrite the manifest + seal a SHA-256 sidecar.
    5. Parse + validate the manifest (Pydantic ScrapeManifest) and
       surface any embedded scrape errors to the logger.

v5 vs legacy manifest handling is unchanged — the JSX-side
saveScrapeToFile still emits schema_version ≥ 5 when all SovCore_*.jsx
modules load, or falls back to the v4 schema otherwise.  The bridge is
transparent to both.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from core.io_utils import write_hash_sidecar, atomic_write_json
from core.logger import log
from models.bridge_contract import is_fresh_jsx_manifest, parse_jsx_wire_manifest
from models.bridge_jobs import CreateUnitJob, DissolveUnitJob
from models.scrape_manifest import ScrapeManifest
from pydantic import ValidationError

# PR-E.1 (2026-04-28) — pre-flight comment gardener. The scanner
# runs over every parsed manifest and attaches a CommentReport so
# downstream consumers (surveyor, UI) can act on classification
# without re-walking comments. Read-only; pure Python; no I/O.
from core.comment_gardener import scan_comp

# PR-B (2026-04-27) — bridge contract validation. parse_typed_job
# validates outgoing job descriptors at the dispatch boundary;
# parse_result validates incoming JSX result payloads at the receive
# boundary. BridgeSchemaVersionError is the structured-refusal signal
# raised when the JSX panel and Python orchestrator disagree on the
# wire contract version — actionable error message tells the user
# to reload the AE panel.
from models.bridge_jobs import (
    BRIDGE_SCHEMA_VERSION,
    BridgeSchemaVersionError,
    parse_typed_job,
    parse_result,
)
from pydantic import ValidationError as _PydanticValidationError

# Heartbeat + transfer-log paths. Used to live in python/launcher.py;
# inlined here when launcher.py was deleted in the v6 CEP port. The
# CEP flow doesn't use the file-bridge inject path that launcher.py
# owned, but this bridge module retains the heartbeat staleness
# contract for any remaining callers that drive AE via the legacy
# file-poll path (the bridge tests under python/tests/test_bridge_*
# still exercise this surface).
TRANSFER_LOG = os.path.abspath("transfer_status.log")
_INBOX_DIR = os.path.abspath(".dimension_inbox")
HEARTBEAT_PATH = os.path.join(_INBOX_DIR, "poller_heartbeat.txt")
HEARTBEAT_STALE_SECONDS = 10

# AE ExtendScript socket server — must match socket_server.jsx default.
SOCKET_PORT = 45445


# Scrape job timeout.  Typical scrapes on mid-size comps finish in 1–3s;
# 30s is a generous ceiling that still fails fast on a dead poller or a
# runaway comp.  Caller can override via kwarg.
DEFAULT_SCRAPE_TIMEOUT_S = 30.0

# Result-file poll cadence.  100 ms keeps the UI feeling instantaneous
# without burning CPU on stat() calls.
_POLL_INTERVAL_S = 0.1


class ScrapeTimeoutError(RuntimeError):
    """Raised when the AE-side poller doesn't write a result file within
    the configured timeout.  Wraps a RuntimeError so callers that catch
    the broad exception type keep working."""


class ScrapeEngineError(RuntimeError):
    """Raised when the AE-side handler writes a `{"status": "ERROR"}`
    result — e.g. no active comp, Sovereign_Core failed to load,
    saveScrapeToFile threw."""


def _prior_layer_count_for_comp(
    name: str,
    repo_root: Path,
) -> Optional[int]:
    """Slot 4 — find the most recent archived `scrape_manifest.json`
    whose `project_info.name` matches `name`, return its layer count.

    Scans `<repo_root>/logs/archive/Session_*/` in reverse-sorted
    folder order (Session_YYYYMMDD_HHMMSS is lex-sortable as
    chronological). Returns the layer count of the first matching
    session, or None on any of:

      - archive directory missing (first-ever run)
      - no archived session matches this comp name
      - any IO / JSON / shape error (treated as "no prior data")

    Used by `parse_manifest`'s cross-session drop detector. Pure
    filesystem read; no Qt boundary; safe on any thread.
    """
    archive_root = repo_root / "logs" / "archive"
    if not archive_root.is_dir():
        return None
    for session in reversed(sorted(archive_root.glob("Session_*"))):
        candidate = session / "scrape_manifest.json"
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (data.get("project_info") or {}).get("name") == name:
            return len(data.get("layers") or [])
    return None


class SovereignBridge:
    """Python ↔ After Effects scrape IPC layer.

    Thin, stateless.  One bridge per project root.  Construction is
    cheap (just stashes the root path); no AE contact happens until
    trigger_scrape() is called.
    """

    def __init__(self, project_root: Optional[str] = None):
        self.project_root = os.path.abspath(project_root or ".")

    # ── Inbox paths ────────────────────────────────────────────────────

    def _inbox_dir(self) -> str:
        return os.path.join(self.project_root, ".dimension_inbox")

    def _assets_dir(self) -> str:
        return os.path.join(self.project_root, "Scripts", "Dimension_Assets")

    def _transfer_log_path(self) -> str:
        """Path to this bridge instance's transfer_status.log.

        Deliberately project_root-scoped (unlike the module-level
        `TRANSFER_LOG` constant, which is CWD-anchored at import time
        — see the "transfer_status.log path resolution" sharp edge in
        CLAUDE.md) so telemetry lands next to the same manifest/log
        set `report_generator.py` already reads
        (`project_root / "transfer_status.log"`), and so tests that
        construct a `SovereignBridge(project_root=tmp_path)` never
        touch a file outside their own tmp dir.
        """
        return os.path.join(self.project_root, "transfer_status.log")

    # ── Poller pre-flight ──────────────────────────────────────────────

    def _heartbeat_path(self) -> str:
        return os.path.join(self._inbox_dir(), "poller_heartbeat.txt")

    def _socket_is_listening(self) -> bool:
        """True when AE's ExtendScript socket server accepts TCP on SOCKET_PORT."""
        import socket as _socket

        try:
            with _socket.create_connection(
                ("127.0.0.1", SOCKET_PORT), timeout=0.5
            ):
                return True
        except OSError:
            return False

    def _heartbeat_status(self) -> tuple[bool, str, Optional[float]]:
        """Freshness of Dimension_Launcher poller heartbeat file."""
        hb_path = self._heartbeat_path()
        if not os.path.exists(hb_path):
            return (
                False,
                "No poller heartbeat file — run Scripts/Dimension_Launcher.jsx "
                "in After Effects (File → Scripts)",
                None,
            )
        age_s = time.time() - os.path.getmtime(hb_path)
        if age_s > HEARTBEAT_STALE_SECONDS:
            return (
                False,
                f"Poller heartbeat stale ({age_s:.0f}s) — re-run "
                "Dimension_Launcher.jsx in AE",
                age_s,
            )
        return True, "", age_s

    def _poller_is_alive(self) -> tuple[bool, str]:
        """AE engine reachable via socket listener or fresh file-bridge poller."""
        if self._socket_is_listening():
            return True, ""
        ok, reason, _age = self._heartbeat_status()
        if ok:
            return True, ""
        return False, reason

    # ── Job dispatch ───────────────────────────────────────────────────

    def _write_job_file(self, job: dict) -> tuple[str, str, int]:
        """Atomic-write a job descriptor to the inbox.  Returns
        (claim_path, result_path, dispatch_ms) so the caller can poll
        for the result without recomputing paths and so the bridge
        analyser can split the IPC wall clock at the wire boundary.

        Slot 17 addendum — `dispatch_ms` is captured as the last
        action before the atomic rename, so it's the closest possible
        proxy for the moment the JSX poller could first observe the
        descriptor. Paired with the JSX-stamped `_claim_ms` on the
        result payload, it gives the bridge poll-dead-time split.

        PR-B: stamps `schema_version` onto the descriptor (if not
        already present) and validates the full payload through the
        BridgeJob discriminated union before the atomic write. Any
        ValidationError raises before any inbox state changes — a
        broken job descriptor is a Python-side bug that should never
        reach disk.
        """
        inbox = self._inbox_dir()
        os.makedirs(inbox, exist_ok=True)
        # Slot 11 (2026-05-15) — results live in their own namespace so
        # the JSX poller's `inboxFolder.getFiles("job_*.json")` wildcard
        # can't match in-flight result files at the inbox root. Job
        # descriptors stay at the root (the poller's contract); only
        # the result path moves to `results/`. JSX writes the `.tmp`
        # sibling and final result inside `results/`, so its
        # same-folder File.rename still works.
        results_dir = os.path.join(inbox, "results")
        os.makedirs(results_dir, exist_ok=True)

        job_id = uuid.uuid4().hex
        name = f"job_{job_id}.json"
        result_name = f"job_{job_id}.result.json"
        tmp_path = os.path.join(inbox, name + ".tmp")
        final_path = os.path.join(inbox, name)
        result_path = os.path.join(results_dir, result_name)

        # The JSX side writes the result via File.rename within the
        # results directory — same-folder rename, contract preserved.
        job_payload = dict(job, result=result_path)
        # PR-B: stamp the bridge contract version so JSX-side
        # validation (and any Python-side re-parse) sees a well-formed
        # descriptor. Callers may pre-stamp; if so, leave their value
        # alone so a deliberate-mismatch test path can exercise refusal.
        job_payload.setdefault("schema_version", BRIDGE_SCHEMA_VERSION)
        job_payload.setdefault("assets", self._assets_dir())
        job_payload.setdefault("ts", time.time())

        # PR-B: validate the typed job descriptor before write.
        # Raises pydantic.ValidationError (caught + re-raised as
        # ValueError with context) on shape mismatch;
        # BridgeSchemaVersionError on version mismatch (only fires
        # if a caller deliberately stamped a non-matching version).
        try:
            parse_typed_job(job_payload)
        except BridgeSchemaVersionError:
            # Surface verbatim — it's already a structured signal.
            raise
        except _PydanticValidationError as e:
            log.error(
                "Bridge dispatch validation failed",
                extra={
                    "job_type": job_payload.get("type"),
                    "schema_version": job_payload.get("schema_version"),
                    "errors": e.errors(include_url=False),
                },
            )
            raise ValueError(
                f"Invalid bridge job descriptor "
                f"(type={job_payload.get('type')!r}): {e}"
            ) from e

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(job_payload, f)
        # os.replace is atomic on POSIX *and* Windows NTFS — unlike
        # os.rename, which fails if the destination exists on Windows.
        os.replace(tmp_path, final_path)
        # Slot 17 — dispatch_ms is the wall-clock moment the descriptor
        # became visible to the JSX poller. Captured immediately after
        # the atomic rename so the analyser's (claim_ms - dispatch_ms)
        # is an upper bound on poll-period dead time.
        dispatch_ms = int(time.time() * 1000.0)

        log.info(
            "Scrape job dispatched",
            extra={
                "inbox": inbox,
                "job": name,
                "result": result_name,
                "dispatch_ms": dispatch_ms,
                "job_type": job.get("type"),
            },
        )
        return final_path, result_path, dispatch_ms

    @staticmethod
    def _emit_bridge_handshake(payload: dict, dispatch_ms: int) -> None:
        """Slot 17 addendum — turn the JSX-stamped handshake timing on
        a result payload into four `phase.end` log lines. Pure side-
        effect; safe to call repeatedly (idempotent emission). Skips
        silently when any expected field is missing — we want a
        partial timeline on older JSX panels rather than a hard error.
        """
        job_type = payload.get("job_type") or "unknown"
        claim_ms = payload.get("_claim_ms")
        started_ms = payload.get("_started_ms")
        finished_ms = payload.get("_finished_ms")
        local_read_ms = int(time.time() * 1000.0)

        def _emit(name: str, dt_ms: float, **extra) -> None:
            log.info("phase.end", extra={
                "phase": f"bridge.{job_type}.{name}",
                "elapsed_ms": round(dt_ms, 3),
                **extra,
            })

        if isinstance(claim_ms, (int, float)):
            _emit(
                "poll_dead",
                claim_ms - dispatch_ms,
                dispatch_ms=dispatch_ms,
                claim_ms=int(claim_ms),
            )
        if (
            isinstance(claim_ms, (int, float))
            and isinstance(started_ms, (int, float))
        ):
            _emit(
                "jsx_dispatch",
                started_ms - claim_ms,
                claim_ms=int(claim_ms),
                started_ms=int(started_ms),
            )
        if (
            isinstance(started_ms, (int, float))
            and isinstance(finished_ms, (int, float))
        ):
            _emit(
                "jsx_work",
                finished_ms - started_ms,
                started_ms=int(started_ms),
                finished_ms=int(finished_ms),
            )
        if isinstance(finished_ms, (int, float)):
            _emit(
                "python_read_lag",
                local_read_ms - finished_ms,
                finished_ms=int(finished_ms),
                local_read_ms=local_read_ms,
            )
        # Wall-total summary — also covers the case where JSX stamps
        # are missing (older panel build): consumers still get a single
        # bucket they can attribute to "unknown JSX overhead."
        _emit(
            "wall_total",
            local_read_ms - dispatch_ms,
            dispatch_ms=dispatch_ms,
            local_read_ms=local_read_ms,
        )

    def _emit_poll_telemetry(
        self,
        *,
        job_type: Optional[str],
        dispatch_ms: int,
        result_ms: int,
        poll_ticks_empty: int,
    ) -> None:
        """Append one bridge-poll round-trip telemetry line to
        transfer_status.log so `_POLL_INTERVAL_S` (100ms) can be
        evaluated with real data instead of folklore (CLAUDE.md's
        "File-bridge polling intervals" sharp edge).

        Matches the wire shape Babysitter's `_writeLog` already
        writes for its own `event: "telemetry"` lines (`phase`,
        `duration_ms`, `t_ms`) so existing line-oriented parsers
        (`python/tools/measure_chunk_overhead.py`,
        `python/scripts/perf1_analyse_gap.py`,
        `report_generator.py::_render_performance_section`) that
        filter on `entry.get("event") == "telemetry"` keep working
        unmodified — they simply don't recognise this new `phase`
        value yet and skip it, same as any telemetry line from a
        phase they don't special-case.

        Fire-and-forget: this is pure observability bolted onto the
        polling loop, not a polling-behavior change. Any failure
        (disk full, permission error, non-serializable field) is
        swallowed here — a telemetry write must never raise into
        `_wait_for_result` and must never slow or fail a bridge job.
        """
        try:
            duration_ms = round(result_ms - dispatch_ms, 3)
            line = json.dumps({
                "event": "telemetry",
                "phase": "python_bridge_poll",
                "job_type": job_type or "unknown",
                "dispatch_ms": int(dispatch_ms),
                "result_ms": int(result_ms),
                "duration_ms": duration_ms,
                "poll_ticks_empty": int(poll_ticks_empty),
                "t_ms": int(result_ms),
            })
            with open(self._transfer_log_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:  # noqa: BLE001 — telemetry must never
                                 # break or slow a bridge job
            log.debug(
                "Bridge poll telemetry emit failed",
                extra={"error": str(e)},
            )

    @staticmethod
    def _best_effort_cleanup(result_path: str) -> None:
        """Unlink the result file and its `.tmp` sibling if present.

        Slot 11 (2026-05-15) — Bug A fix. Runs on every exit path of
        `_wait_for_result` (success, timeout, validation error, any
        exception) so result files never leak. Quarantined to the
        `results/` namespace by Stage A; this kills the leak itself.

        Best-effort: never raises. The `.tmp` sibling handles the
        narrow case where JSX crashed mid-rename and left a partial
        write behind.
        """
        for path in (result_path, result_path + ".tmp"):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as e:  # noqa: BLE001 — cleanup must not raise
                log.warning(
                    "Bridge result cleanup failed",
                    extra={"path": path, "error": str(e)},
                )

    def _wait_for_result(
        self,
        result_path: str,
        timeout_s: float,
        dispatch_ms: Optional[int] = None,
    ) -> dict:
        """Poll for the result file until it appears or timeout.

        The JSX side writes `{result_path}.tmp` then File.rename's it
        to the final name — the final path only exists when the write
        is complete, so any read that succeeds reads a finished file.
        No half-written result possible.

        Slot 11 (2026-05-15) — Bug A fix. Cleanup runs on EVERY exit
        path (success, timeout, validation error, any exception) via
        a try/finally. Pre-Slot-11 the cleanup only ran on the success
        path, so every other exit leaked the result file. Stage A
        quarantined the leak to `results/`; this finally kills it.

        Slot 17 addendum — when `dispatch_ms` is passed, extracts the
        JSX-stamped handshake timing (`_claim_ms`, `_started_ms`,
        `_finished_ms`) from the result payload and emits four phase
        markers to dimension.log:
          - bridge.<type>.poll_dead       (dispatch → JSX claim)
          - bridge.<type>.jsx_dispatch    (claim → handler entry)
          - bridge.<type>.jsx_work        (handler entry → result write)
          - bridge.<type>.python_read_lag (result write → Python read)
        Backward compatible: callers that don't pass dispatch_ms get
        the legacy result-only path.

        2026-07-07 poll-cadence telemetry — CLAUDE.md's "File-bridge
        polling intervals" sharp edge notes `_POLL_INTERVAL_S` (100ms)
        was "tested empirically" years ago but nothing measures it
        today. `poll_ticks_empty` counts every loop tick where the
        result file was NOT yet present; the moment `os.path.exists`
        first returns True is stamped as `result_ms`. When
        `dispatch_ms` is known, `_emit_poll_telemetry` appends one
        `event: "telemetry"` line to transfer_status.log — the same
        wire shape Babysitter's `_writeLog` already writes — so the
        100ms poll period can be evaluated against real
        dispatch→detect deltas instead of folklore. Purely additive:
        never changes polling cadence, timeout handling, or return
        values, and any emission failure is swallowed (see
        `_emit_poll_telemetry`).
        """
        deadline = time.monotonic() + timeout_s
        poll_ticks_empty = 0
        try:
            while time.monotonic() < deadline:
                if os.path.exists(result_path):
                    result_ms = int(time.time() * 1000.0)
                    # Even with atomic rename, give the filesystem a
                    # beat to fully flush metadata before read.  One
                    # retry is plenty — we're racing a filesystem
                    # sync, not a concurrent writer.
                    for _ in range(3):
                        try:
                            with open(result_path, "r", encoding="utf-8") as f:
                                payload = json.load(f)
                            break
                        except (json.JSONDecodeError, OSError):
                            time.sleep(0.02)
                    else:
                        raise ScrapeEngineError(
                            f"Result file {result_path} exists but is unreadable"
                        )

                    # 2026-07-07 — poll-cadence round-trip telemetry.
                    # Fires as soon as the result file is confirmed
                    # readable, ahead of schema validation, so a
                    # malformed/stale-version payload still yields a
                    # poll-timing data point. Skipped when the caller
                    # didn't pass dispatch_ms (e.g. direct
                    # _wait_for_result calls in tests) since the
                    # round-trip delta would be meaningless.
                    if dispatch_ms is not None:
                        self._emit_poll_telemetry(
                            job_type=(
                                payload.get("job_type")
                                if isinstance(payload, dict) else None
                            ),
                            dispatch_ms=dispatch_ms,
                            result_ms=result_ms,
                            poll_ticks_empty=poll_ticks_empty,
                        )

                    # v5.8.10 — staleness detection. Every JSX result
                    # carries a `dimension_schema_version` stamp; tell
                    # the process-wide observer so the orchestrator's
                    # banner can update if the running panel is older
                    # than this Python build expects.
                    try:
                        from core.staleness_state import notify_jsx_version
                        notify_jsx_version(
                            payload.get("dimension_schema_version")
                            if isinstance(payload, dict) else None
                        )
                    except Exception:  # noqa: BLE001 — never block
                                        # the bridge on observability
                        pass

                    # PR-B (2026-04-27) — receive-side contract
                    # validation. parse_result raises
                    # BridgeSchemaVersionError on bridge contract
                    # version mismatch (loud refusal — the error
                    # message tells the user to reload the panel).
                    # Shape-validation errors get logged with full
                    # context and re-raised as ScrapeEngineError so
                    # the existing callers' error-handling chain
                    # catches them via the broad ScrapeEngineError
                    # except clauses (no caller changes required).
                    #
                    # On success, returns the raw payload dict
                    # unchanged so existing dispatch methods
                    # (trigger_scrape, apply_manual_tag, select_layer,
                    # etc.) consume `payload.get("status")` etc. as
                    # before. A future commit may migrate callers to
                    # typed result models; PR-B's surface area stays
                    # narrow.
                    if isinstance(payload, dict):
                        try:
                            parse_result(payload)
                        except BridgeSchemaVersionError:
                            raise
                        except _PydanticValidationError as e:
                            log.error(
                                "Bridge result validation failed",
                                extra={
                                    "job_type": payload.get("job_type"),
                                    "status": payload.get("status"),
                                    "schema_version":
                                        payload.get("schema_version"),
                                    "errors": e.errors(include_url=False),
                                },
                            )
                            raise ScrapeEngineError(
                                f"Invalid bridge result payload "
                                f"(job_type={payload.get('job_type')!r}, "
                                f"status={payload.get('status')!r}): {e}"
                            ) from e

                    # Slot 17 addendum — bridge handshake split. Logs
                    # four phase markers per round-trip so the analyser
                    # can attribute the wall-clock cost to poll period,
                    # JSX dispatch, JSX handler work, or Python read lag.
                    if (
                        dispatch_ms is not None
                        and isinstance(payload, dict)
                    ):
                        try:
                            self._emit_bridge_handshake(payload, dispatch_ms)
                        except Exception as e:  # noqa: BLE001 — observability
                            log.warning(
                                "Bridge handshake emit failed",
                                extra={"error": str(e)},
                            )

                    return payload
                poll_ticks_empty += 1
                time.sleep(_POLL_INTERVAL_S)

            raise ScrapeTimeoutError(
                f"AE scrape did not produce a result within {timeout_s:.0f}s. "
                "Check that the Dimension_Launcher panel is open and the "
                "engine poller is running (LAUNCH ENGINE button in AE)."
            )
        finally:
            # Slot 11 — runs on every exit path: success, timeout,
            # validation error, unreadable file, any exception. The
            # helper is best-effort and never raises.
            self._best_effort_cleanup(result_path)

    # ── Public API ─────────────────────────────────────────────────────

    def _prepare_job_payload(self, job: dict, *, for_socket: bool) -> dict:
        """Stamp bridge fields and validate the outgoing job descriptor."""
        job_payload = dict(job)
        job_payload.setdefault("schema_version", BRIDGE_SCHEMA_VERSION)
        job_payload.setdefault("assets", self._assets_dir())
        job_payload.setdefault("ts", time.time())
        if for_socket:
            # Socket path has no inbox result file; dummy path satisfies schema.
            job_payload.setdefault("result", "")
        try:
            parse_typed_job(job_payload)
        except BridgeSchemaVersionError:
            raise
        except _PydanticValidationError as e:
            log.error(
                "Bridge dispatch validation failed",
                extra={
                    "job_type": job_payload.get("type"),
                    "schema_version": job_payload.get("schema_version"),
                    "errors": e.errors(include_url=False),
                },
            )
            raise ValueError(
                f"Invalid bridge job descriptor "
                f"(type={job_payload.get('type')!r}): {e}"
            ) from e
        return job_payload

    @staticmethod
    def _validate_job_result(payload: dict) -> dict:
        """Validate an incoming JSX result payload at the receive boundary."""
        try:
            parse_result(payload)
        except BridgeSchemaVersionError:
            raise
        except _PydanticValidationError as e:
            log.error(
                "Bridge result validation failed",
                extra={
                    "job_type": payload.get("job_type"),
                    "status": payload.get("status"),
                    "schema_version": payload.get("schema_version"),
                    "errors": e.errors(include_url=False),
                },
            )
            raise ScrapeEngineError(
                f"Invalid bridge result payload "
                f"(job_type={payload.get('job_type')!r}, "
                f"status={payload.get('status')!r}): {e}"
            ) from e
        return payload

    def _execute_socket_job(self, job: dict, timeout_s: float) -> dict:
        """Execute a validated job over TCP socket (caller routes transport)."""
        job_payload = self._prepare_job_payload(job, for_socket=True)

        from core.ipc_client import execute_job, IPCConnectionError
        try:
            payload = execute_job(job_payload, timeout=timeout_s)
        except (BridgeSchemaVersionError, IPCConnectionError):
            raise
        except Exception as e:
            raise ScrapeEngineError(f"Socket execution failed: {e}") from e

        if isinstance(payload, dict):
            return self._validate_job_result(payload)
        return payload

    def _execute_file_bridge_job(self, job: dict, timeout_s: float) -> dict:
        """Dispatch via inbox descriptor + poll for result file."""
        _claim_path, result_path, dispatch_ms = self._write_job_file(job)
        payload = self._wait_for_result(result_path, timeout_s, dispatch_ms)
        if isinstance(payload, dict):
            return self._validate_job_result(payload)
        return payload

    def execute_bridge_job(self, job: dict, timeout_s: float) -> dict:
        """Public façade over `_execute_bridge_job`.

        Color Match (Track D) intentionally lives in its own module
        (`python/bridge/color_match_bridge.py`) rather than growing
        this already-1300+-line file further, mirroring the CEP-side
        isolation of Color Match UI into `color_match_ui.js` instead
        of `tagging_v2.js`. That module needs to dispatch through the
        existing dual-transport pipe without duplicating it or
        reaching into a leading-underscore "private" method from
        outside this class — this wrapper is the seam. Every other
        job type on this bridge still calls `_execute_bridge_job`
        directly; this is not a rename.
        """
        return self._execute_bridge_job(job, timeout_s)

    def _execute_bridge_job(self, job: dict, timeout_s: float) -> dict:
        """Route bridge IPC: socket when listening, else file-bridge heartbeat."""
        _sid_extra = (
            {"session_id": job["session_id"]}
            if isinstance(job.get("session_id"), str) else {}
        )
        from core.ipc_client import IPCConnectionError
        try:
            # Attempt direct socket connect/execution
            res = self._execute_socket_job(job, timeout_s)
            log.info(
                "Bridge job dispatch",
                extra={"job_type": job.get("type"), "transport": "socket",
                       **_sid_extra},
            )
            return res
        except (IPCConnectionError, ConnectionRefusedError) as e:
            # Fall back to file-bridge if socket is down / refused
            pass

        hb_ok, hb_reason, hb_age = self._heartbeat_status()
        if hb_ok:
            log.info(
                "Bridge job dispatch",
                extra={
                    "job_type": job.get("type"),
                    "transport": "file_bridge",
                    "heartbeat_age_s": round(hb_age or 0.0, 2),
                    **_sid_extra,
                },
            )
            return self._execute_file_bridge_job(job, timeout_s)

        raise RuntimeError(
            f"AE engine not responding: {hb_reason} "
            "Ensure After Effects is open and Dimension_Launcher.jsx has been run."
        )

    def trigger_scrape(
        self,
        manifest_path: Optional[str] = None,
        *,
        mode: str = "standard",
        timeout_s: float = DEFAULT_SCRAPE_TIMEOUT_S,
    ) -> str:
        """Dispatch a scrape job to the AE-side socket server and wait for it to finish."""
        if manifest_path is None:
            manifest_path = os.path.join(self.project_root, "scrape_manifest.json")
        manifest_path = os.path.abspath(manifest_path)

        _t_scrape_total = time.perf_counter()
        _t_preflight = time.perf_counter()

        # Ensure parent dir exists
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        log.info("phase.end", extra={
            "phase": "scrape.preflight",
            "elapsed_ms": round((time.perf_counter() - _t_preflight) * 1000.0, 3),
        })

        _t_dispatch = time.perf_counter()
        job = {
            "type": "scrape",
            "mode": mode,
            "manifest": manifest_path,
        }
        
        try:
            response = self._execute_bridge_job(job, timeout_s)
        except Exception as e:
            log.error("Scrape socket execution failed", extra={"error": str(e)})
            raise ScrapeEngineError(f"Scrape socket execution failed: {e}") from e

        log.info("phase.end", extra={
            "phase": "scrape.dispatch",
            "elapsed_ms": round((time.perf_counter() - _t_dispatch) * 1000.0, 3),
        })

        status = (response.get("status") or "").upper()
        if status != "OK":
            err = response.get("error") or f"Unknown scrape failure ({response!r})"
            log.error("Scrape engine reported error", extra={"error": err})
            raise ScrapeEngineError(err)

        manifest_data = response.get("manifest")
        project_structure_data = response.get("project_structure")

        # File-bridge scrape returns manifest as an on-disk path string
        # (saveScrapeToFile); socket scrape returns inline dict payload.
        if isinstance(manifest_data, str):
            on_disk = os.path.abspath(manifest_data)
            if not os.path.isfile(on_disk):
                raise ScrapeEngineError(
                    f"Scrape result manifest path missing: {on_disk}"
                )
            with open(on_disk, "r", encoding="utf-8") as fh:
                manifest_data = json.load(fh)
            if on_disk != manifest_path:
                manifest_path = on_disk

        if not manifest_data:
            raise ScrapeEngineError("Scrape response missing manifest data")

        # Notify JSX version for staleness detection
        try:
            from core.staleness_state import notify_jsx_version
            notify_jsx_version(response.get("dimension_schema_version"))
        except Exception:
            pass

        # Write manifest to disk
        _t_seal = time.perf_counter()
        digest = atomic_write_json(manifest_path, manifest_data)
        write_hash_sidecar(manifest_path, digest)
        
        # Write project structure to disk
        if project_structure_data:
            structure_path = os.path.join(os.path.dirname(manifest_path), "project_structure.json")
            atomic_write_json(structure_path, project_structure_data)
            
        log.info("Manifest integrity sealed", extra={"sha256": digest[:16]})
        log.info("phase.end", extra={
            "phase": "scrape.seal_sidecar",
            "elapsed_ms": round((time.perf_counter() - _t_seal) * 1000.0, 3),
        })
        log.info("phase.end", extra={
            "phase": "scrape.total",
            "elapsed_ms": round((time.perf_counter() - _t_scrape_total) * 1000.0, 3),
            "manifest": manifest_path,
        })

        return manifest_path

    def parse_manifest(self, manifest_path: str) -> ScrapeManifest:
        """Read and validate the scrape manifest at `manifest_path`.

        Contract unchanged from v5.0 — the file-bridge refactor only
        affected transport, not manifest shape.

        - Parses as ScrapeManifest (Pydantic v2).
        - Logs all entries from the top-level `errors` array (v5 only).
        - Logs per-layer `_v5_errors` if present.
        - Raises ValueError if the manifest JSON is malformed or the
          status field is not "OK".
        """
        _t_parse_total = time.perf_counter()
        _t_load = time.perf_counter()
        with open(manifest_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        log.info("phase.end", extra={
            "phase": "parse.json_load",
            "elapsed_ms": round((time.perf_counter() - _t_load) * 1000.0, 3),
        })

        if raw.get("status") not in ("OK", "ok"):
            raise ValueError(
                f"Manifest status is not OK: {raw.get('status')} — {raw.get('error', '')}"
            )

        if is_fresh_jsx_manifest(raw):
            try:
                parse_jsx_wire_manifest(raw)
                log.info(
                    "JSX wire manifest validation passed",
                    extra={"path": manifest_path, "layers": len(raw.get("layers", []))},
                )
            except ValidationError as e:
                log.error(
                    "JSX wire manifest validation failed",
                    extra={
                        "path": manifest_path,
                        "errors": e.error_count(),
                        "detail": e.errors()[:5],
                    },
                )
                raise ValueError(
                    f"JSX wire manifest validation failed "
                    f"({e.error_count()} error(s)): "
                    f"{e.errors()[0]['msg'] if e.errors() else 'unknown'}"
                ) from e

        _t_pyd = time.perf_counter()
        manifest = ScrapeManifest(**raw)
        log.info("phase.end", extra={
            "phase": "parse.pydantic_validate",
            "elapsed_ms": round((time.perf_counter() - _t_pyd) * 1000.0, 3),
            "layers": len(manifest.layers),
        })

        log.info(
            "Manifest parsed",
            extra={
                "schema_version": manifest.schema_version,
                "layers": len(manifest.layers),
                "comp": manifest.project_info.name if manifest.project_info else "?",
            },
        )

        # Slot 4 — cross-session layer-count drop detector. Compare
        # the current parse against the most recent archived session
        # for the same comp. Logs WARN on drop; silent skip when no
        # prior session exists (first conform of this comp).
        if manifest.project_info is not None:
            prior = _prior_layer_count_for_comp(
                manifest.project_info.name,
                Path(self.project_root),
            )
            if prior is not None and len(manifest.layers) < prior:
                log.warning(
                    "Layer count drop detected",
                    extra={
                        "comp": manifest.project_info.name,
                        "prior_count": prior,
                        "current_count": len(manifest.layers),
                    },
                )

        # Surface top-level errors (v5 — List[ScrapeError] Pydantic models).
        # PR-X Stage A — severity="info" entries route to log.info
        # so the deny-list parallel-run telemetry surfaces at the
        # right level. Pre-PR-X this branch logged everything
        # non-fatal/error to log.warning, which would have made
        # Stage A noise indistinguishable from real warnings.
        if manifest.errors:
            for err in manifest.errors:
                if isinstance(err, dict):
                    sev = err.get("severity", "unknown")
                    msg = err.get("error") or err.get("message") or str(err)
                    op = err.get("operation", "scrape")
                    layer_idx = err.get("layer_index")
                else:
                    sev = err.severity.value if hasattr(err.severity, "value") else str(err.severity)
                    msg = err.error
                    op = err.operation
                    layer_idx = err.layer_index
                if sev in ("fatal", "error"):
                    level = log.error
                elif sev == "info":
                    level = log.info
                else:
                    level = log.warning
                extras = {"severity": sev, "message": msg, "operation": op}
                if layer_idx is not None:
                    extras["layer_index"] = layer_idx
                level("Scrape error", extra=extras)

        # Surface per-layer v5 errors (raw dicts from JSX).
        for layer in manifest.layers:
            v5_errs = getattr(layer, "_v5_errors", None) or []
            if isinstance(v5_errs, list):
                for err in v5_errs:
                    if isinstance(err, dict):
                        sev = err.get("severity", "unknown")
                        msg = err.get("error") or err.get("message") or str(err)
                    else:
                        sev = err.severity.value if hasattr(err.severity, "value") else str(err.severity)
                        msg = err.error
                    if sev in ("fatal", "error"):
                        log.error(
                            "Layer scrape error",
                            extra={"layer": layer.name, "severity": sev, "message": msg},
                        )

        # PR-E.1 — Comment garden pre-flight. Classify every layer's
        # comment field, attach the report to the manifest, log a
        # one-line summary at INFO. Read-only; never blocks the
        # scrape; failure is non-fatal (the manifest still returns
        # without a report attached).
        _t_garden = time.perf_counter()
        try:
            report = scan_comp(manifest)
            manifest.comment_report = report
            log.info(
                "Comment garden",
                extra={
                    "foreign":   report.foreign_count,
                    "malformed": report.malformed_count,
                    "legacy":    report.legacy_count,
                    "mixed":     report.mixed_count,
                    "stamped":   report.stamped_count,
                    "total":     report.total_layers,
                    "warnings":  report.has_warnings,
                },
            )
        except Exception as e:  # noqa: BLE001 — gardener must never
                                # block the scrape on its own bug
            log.warning(
                "Comment garden scan failed — manifest returned without report",
                extra={"error": str(e)},
            )
        log.info("phase.end", extra={
            "phase": "parse.comment_garden",
            "elapsed_ms": round((time.perf_counter() - _t_garden) * 1000.0, 3),
        })
        log.info("phase.end", extra={
            "phase": "parse.total",
            "elapsed_ms": round((time.perf_counter() - _t_parse_total) * 1000.0, 3),
            "layers": len(manifest.layers),
        })

        return manifest

    # ── v5.2.4 Tagging Manager — manual-tag round-trip ──────────────────

    def apply_manual_tag(
        self,
        uid: str,
        tag: str,
        *,
        layer_index: Optional[int] = None,
        layer_name: Optional[str] = None,
        comp_name: Optional[str] = None,
        timeout_s: float = 5.0,
    ) -> dict:
        """Set a manual tag on the layer with the given UID.

        Routes through the file-bridge as a `tag-write` job. JSX side
        (Dimension_Launcher._handleTagWriteJob) calls
        `Layer.applyManualTag(uid, tag, index, layer_name, comp_name)`
        which strips any existing `#TAG` hashtag, appends the new one,
        and sets `layer.label` per the bimap.

        v5.8.13: `layer_index`, `layer_name`, and `comp_name` are
        passed as FALLBACK identifiers used by the JSX side when
        `findLayerByUID` misses (typical first-time tag-write on a
        freshly-scraped layer whose UID hasn't been stamped into the
        AE comment yet). The JSX anchors on `comp_name` (looked up in
        `app.project.items`) rather than `app.project.activeItem` so
        the lookup survives active-item drift between Send To
        Dimension and the +TAG click. `layer_name` is the defensive
        check against intervening reorders.

        Returns the JSX result dict (status + uid + tag + source +
        label) on success. Raises ScrapeEngineError on AE-side
        failure, ScrapeTimeoutError on no result.
        """
        if not uid:
            raise ValueError("uid is required")
        if not tag:
            raise ValueError("tag is required (use clear_manual_tag to clear)")
        job = {
            "type":    "tag-write",
            "uid":     uid,
            "tag":     str(tag),
        }
        if layer_index is not None:
            job["layer_index"] = int(layer_index)
        if layer_name:
            job["layer_name"] = str(layer_name)
        if comp_name:
            job["comp_name"] = str(comp_name)
            
        payload = self._execute_bridge_job(job, timeout_s)

        if (payload.get("status") or "").upper() != "OK":
            err = payload.get("error") or f"apply_manual_tag failed ({payload!r})"
            log.error("Tag-write reported error", extra={"error": err})
            raise ScrapeEngineError(err)
        log.info("Manual tag applied", extra={"uid": uid, "tag": tag})
        return payload

    def clear_manual_tag(
        self,
        uid: str,
        *,
        layer_index: Optional[int] = None,
        layer_name: Optional[str] = None,
        comp_name: Optional[str] = None,
        timeout_s: float = 5.0,
    ) -> dict:
        """Clear all manual tag markers from the layer with the given
        UID — strips `#TAG` hashtags and resets `layer.label` to 0."""
        if not uid:
            raise ValueError("uid is required")
        job = {
            "type":    "tag-write",
            "uid":     uid,
            "tag":     None,
        }
        if layer_index is not None:
            job["layer_index"] = int(layer_index)
        if layer_name:
            job["layer_name"] = str(layer_name)
        if comp_name:
            job["comp_name"] = str(comp_name)
            
        payload = self._execute_bridge_job(job, timeout_s)

        if (payload.get("status") or "").upper() != "OK":
            err = payload.get("error") or f"clear_manual_tag failed ({payload!r})"
            log.error("Tag-clear reported error", extra={"error": err})
            raise ScrapeEngineError(err)
        log.info("Manual tag cleared", extra={"uid": uid})
        return payload

    def select_layer(
        self,
        uid: str,
        *,
        layer_index: Optional[int] = None,
        layer_name: Optional[str] = None,
        comp_name: Optional[str] = None,
        timeout_s: float = 3.0,
    ) -> dict:
        """v5.5.5 Move 4 — request AE to select + scroll-to the layer with the given UID."""
        if not uid:
            raise ValueError("uid is required")
        job = {
            "type":    "select-layer",
            "uid":     uid,
        }
        if layer_index is not None:
            job["layer_index"] = int(layer_index)
        if layer_name:
            job["layer_name"] = str(layer_name)
        if comp_name:
            job["comp_name"] = str(comp_name)
            
        try:
            payload = self._execute_bridge_job(job, timeout_s)
        except Exception as e:
            log.warning("AE selection: socket execution failed",
                        extra={"uid": uid, "error": str(e)})
            return {"status": "TIMEOUT"}
        log.info("AE selection complete", extra={
            "uid": uid,
            "status": payload.get("status"),
            "resolved_via": payload.get("resolved_via"),
        })
        return payload

    def query_layer_state(
        self,
        uid: str,
        property_paths: list[str],
        *,
        time_s: Optional[float] = None,
        layer_index: Optional[int] = None,
        layer_name: Optional[str] = None,
        comp_name: Optional[str] = None,
        timeout_s: float = 3.0,
    ) -> dict:
        """Harness Slice 1 — read-only property-value query against a live
        AE layer. Never mutates AE state. Best-effort like select_layer():
        bridge dispatch failures return {"status": "TIMEOUT"} rather than
        raising."""
        if not uid:
            raise ValueError("uid is required")
        if not property_paths:
            raise ValueError("property_paths is required (non-empty list)")
        job = {
            "type": "query-layer-state",
            "uid": uid,
            "property_paths": list(property_paths),
        }
        if time_s is not None:
            job["time_s"] = float(time_s)
        if layer_index is not None:
            job["layer_index"] = int(layer_index)
        if layer_name:
            job["layer_name"] = str(layer_name)
        if comp_name:
            job["comp_name"] = str(comp_name)

        try:
            payload = self._execute_bridge_job(job, timeout_s)
        except Exception as e:
            log.warning("AE query-layer-state: socket execution failed",
                        extra={"uid": uid, "error": str(e)})
            return {"status": "TIMEOUT"}
        log.info("AE query-layer-state complete", extra={
            "uid": uid,
            "status": payload.get("status"),
            "unresolved_paths": payload.get("unresolved_paths"),
        })
        return payload

    def apply_duplication_plan(
        self,
        plan,                       # DuplicationPlan
        conformed_comp_id: Optional[int] = None,
        log_path: str = "",
        *,
        conformed_comp_name: Optional[str] = None,
        timeout_s: float = 8.0,
    ) -> dict:
        """v5.8 — execute a flat-duplication plan against the active AE project."""
        if plan is None:
            raise ValueError("plan is required")
        if not log_path:
            raise ValueError("log_path is required")
        has_id = isinstance(conformed_comp_id, int)
        has_name = bool(conformed_comp_name)
        if not has_id and not has_name:
            raise ValueError(
                "exactly one of conformed_comp_id or conformed_comp_name is required"
            )
        if has_id and has_name:
            raise ValueError(
                "specify conformed_comp_id OR conformed_comp_name, not both"
            )

        if hasattr(plan, "model_dump"):
            plan_dict = plan.model_dump(mode="json")
        else:
            plan_dict = dict(plan)

        log_path_abs = os.path.abspath(log_path)
        os.makedirs(os.path.dirname(log_path_abs), exist_ok=True)

        job = {
            "type":              "duplicate-plan",
            "plan":              plan_dict,
            "log_path":          log_path_abs,
            "progress_log_path": TRANSFER_LOG,
            "babysitter":        os.path.join(self._assets_dir(), "Babysitter.jsx"),
        }
        if has_id:
            job["conformed_comp_id"] = int(conformed_comp_id)
        else:
            job["conformed_comp_name"] = str(conformed_comp_name)
            
        try:
            payload = self._execute_bridge_job(job, timeout_s)
        except Exception as e:
            raise ScrapeEngineError(f"Duplicate-plan socket execution failed: {e}") from e

        log.info(
            "applyDuplicationPlan complete",
            extra={
                "status":          payload.get("status"),
                "duplicates_made": payload.get("duplicates_made"),
                "rewires_made":    payload.get("rewires_made"),
                "skipped":         payload.get("skipped"),
                "errors":          payload.get("errors"),
            },
        )
        return payload

    def apply_panel_slicing_plan(
        self,
        plan,
        log_path: str,
        *,
        conformed_comp_id: Optional[int] = None,
        conformed_comp_name: Optional[str] = None,
        timeout_s: float = 30.0,
    ) -> dict:
        """#346 Part C — execute a multi-panel OOH slicing plan against AE.

        Lazy-imports `bridge.panel_slicing` so this module stays loadable
        without a circular import (`panel_slicing` imports SovereignBridge).
        """
        from bridge.panel_slicing import apply_panel_slicing_plan as _apply
        return _apply(
            self,
            plan,
            log_path,
            conformed_comp_id=conformed_comp_id,
            conformed_comp_name=conformed_comp_name,
            timeout_s=timeout_s,
        )

    def maybe_apply_panel_slicing_plan(
        self,
        plan,
        log_path: str,
        *,
        conformed_comp_id: Optional[int] = None,
        conformed_comp_name: Optional[str] = None,
        timeout_s: float = 30.0,
    ):
        """Gated caller: no job if plan is None (263 non-multi_panel targets)."""
        from bridge.panel_slicing import maybe_apply_panel_slicing_plan as _maybe
        return _maybe(
            self,
            plan,
            log_path,
            conformed_comp_id=conformed_comp_id,
            conformed_comp_name=conformed_comp_name,
            timeout_s=timeout_s,
        )

    def toggle_safe_zone_mask(
        self,
        action: str,
        mask_path: Optional[str] = None,
        *,
        timeout_s: float = 5.0,
    ) -> dict:
        """Toggle the SOE safe-zone debug overlay in the active comp."""
        if action not in ("import", "remove"):
            raise ValueError(f"action must be 'import' or 'remove', got {action!r}")
        if action == "import" and not mask_path:
            raise ValueError("mask_path is required when action='import'")

        job = {
            "type":       "mask-toggle",
            "action":     action,
            "mask_path":  os.path.abspath(mask_path) if mask_path else None,
            "babysitter": os.path.join(self._assets_dir(), "Babysitter.jsx"),
        }
        
        try:
            payload = self._execute_bridge_job(job, timeout_s)
        except Exception as e:
            raise ScrapeEngineError(f"Mask-toggle socket execution failed: {e}") from e

        if (payload.get("status") or "").upper() != "OK":
            err = payload.get("error") or f"mask-toggle failed ({payload!r})"
            log.error("Mask-toggle reported error", extra={"error": err})
            raise ScrapeEngineError(err)
        log.info("Safe-zone mask toggled",
                 extra={"action": action, "mask_path": mask_path or ""})
        return payload

    def handle_create_unit_job(self, job: CreateUnitJob) -> dict:
        """
        Phase 3 of "Enhance Unit Management UI".
        Handles the 'create-unit' job from the CEP panel.

        Reads .dimension/unit_overrides.json, adds a new 'create' action
        with the provided UIDs, saves the file, and triggers a re-survey
        to send the updated manifest back to the panel.
        """
        from stages.tag import load_unit_overrides, save_unit_overrides
        import uuid

        try:
            if not self.current_manifest_path:
                raise RuntimeError("No active manifest to apply unit overrides to.")

            overrides = load_unit_overrides(self.current_manifest_path) or {}
            
            # Ensure 'unit_overrides' key exists
            if "unit_overrides" not in overrides:
                overrides["unit_overrides"] = {}

            unit_id = f"manual_{uuid.uuid4().hex[:8]}"
            overrides["unit_overrides"][unit_id] = {
                "action": "create",
                "members": job.uids,
            }

            save_unit_overrides(self.current_manifest_path, overrides)
            log.info("Manual unit override created", extra={"unit_id": unit_id, "members": job.uids})

            # Trigger re-survey and send updated manifest to panel
            manifest = self.scrape_and_parse(self.current_manifest_path)
            self.send_manifest_to_panel(manifest)

            return {"status": "OK", "message": f"Unit {unit_id} created."}
        except Exception as e:
            log.error("Failed to handle create-unit job", extra={"error": str(e)})
            return {"status": "ERROR", "error": str(e)}

    def handle_dissolve_unit_job(self, job: DissolveUnitJob) -> dict:
        """
        Handles the 'dissolve-unit' job from the CEP panel.
        
        Writes a 'dissolve' action to the unit_overrides.json sidecar,
        then triggers a re-survey to update the panel.
        """
        from stages.tag import load_unit_overrides, save_unit_overrides

        try:
            if not self.current_manifest_path:
                raise RuntimeError("No active manifest to apply unit overrides to.")

            overrides = load_unit_overrides(self.current_manifest_path) or {}
            
            if "unit_overrides" not in overrides:
                overrides["unit_overrides"] = {}

            overrides["unit_overrides"][job.unit_id] = {"action": "dissolve"}

            save_unit_overrides(self.current_manifest_path, overrides)
            log.info("Unit dissolve override created", extra={"unit_id": job.unit_id})

            # Trigger re-survey and send updated manifest to panel
            manifest = self.scrape_and_parse(self.current_manifest_path)
            self.send_manifest_to_panel(manifest)

            return {"status": "OK", "message": f"Unit {job.unit_id} dissolved."}
        except Exception as e:
            log.error("Failed to handle dissolve-unit job", extra={"error": str(e)})
            return {"status": "ERROR", "error": str(e)}


    def scrape_and_parse(
        self,
        manifest_path: Optional[str] = None,
        *,
        mode: str = "standard",
        timeout_s: float = DEFAULT_SCRAPE_TIMEOUT_S,
    ) -> ScrapeManifest:
        """Convenience: trigger_scrape() + parse_manifest() in one call."""
        path = self.trigger_scrape(manifest_path, mode=mode, timeout_s=timeout_s)
        return self.parse_manifest(path)
