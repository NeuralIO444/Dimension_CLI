import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# Ensure the app paths are correctly resolved when frozen by PyInstaller
if getattr(sys, 'frozen', False):
    application_path = sys._MEIPASS
else:
    application_path = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, application_path)

from bridge.sovereign_bridge import SovereignBridge
from core.io_utils import atomic_write_json
from core.process_watchdog import DEFAULT_EXPECTED_NAME, ParentWatchdog
from logic.license_status import get_current_status
from logic.studio_profile_registry import REGISTRY as ProfileRegistry
from logic.target_store import TargetStoreManager
from stages.batch import run_batch_conform
from stages.inject import (
    dispatch_file_bridge_inject,
    process_inject_logs,
)

# Repo root for the license dev-bypass .git check — distinct from the AE
# PROJECT's project_root (computed per-request from self.manifest_path).
# This one is fixed at process start: the Dimension codebase's own root,
# one level up from this file's directory when unfrozen, unresolvable
# (and therefore never bypass-eligible) when frozen into a packaged ZXP.
_DIMENSION_REPO_ROOT = (
    None if getattr(sys, "frozen", False) else os.path.dirname(application_path)
)


def _version_info() -> dict:
    """Dashboard equivalent of cep/js/main.js::initVersionFooter(). The
    Dashboard has no Node/JS runtime (it's a plain browser page served by
    this http.server process), so this is a Python re-implementation of
    the same idea: read ExtensionBundleVersion from the CEP manifest, and
    in a dev checkout resolve today's date + current git branch/short
    hash so the footer correlates to whatever's actually checked out —
    same fallback-to-static behavior as the CEP footer when git/manifest
    aren't available (frozen builds, or a shallow/detached checkout)."""
    bundle_ver = None
    ref = None
    build_date = None

    if _DIMENSION_REPO_ROOT:
        manifest_path = os.path.join(_DIMENSION_REPO_ROOT, "cep", "CSXS", "manifest.xml")
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                m = re.search(r'ExtensionBundleVersion="([^"]+)"', f.read())
            if m:
                bundle_ver = m.group(1)
        except OSError:
            pass

        try:
            branch = subprocess.run(
                ["git", "-C", _DIMENSION_REPO_ROOT, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=2, check=True,
            ).stdout.strip()
            short_hash = subprocess.run(
                ["git", "-C", _DIMENSION_REPO_ROOT, "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=2, check=True,
            ).stdout.strip()
            if branch and short_hash:
                ref = f"{branch}@{short_hash}"
            build_date = date.today().isoformat()
        except (OSError, subprocess.SubprocessError):
            pass

    return {
        "version": f"v{bundle_ver}-DASH" if bundle_ver else "v6.0.0-DASH",
        "build_date": build_date or "2026-08-23",
        "ref": ref or "main",
    }


def _activate_after_effects() -> None:
    """Best-effort: bring After Effects to the foreground when a batch
    conform kicks off, so a single-monitor user watching the Dashboard
    sees the work happen without alt-tabbing manually. Gated by the
    Dashboard's own "Focus AE on Execute" toggle (frontend, off by
    default on a second monitor) — never called unconditionally.

    macOS only (Dashboard is macOS-only for this beta per README/
    INSTALL). Matches on process name prefix via System Events rather
    than a hardcoded app name, so it doesn't need updating across AE
    version bumps (2024/2025/2026/...). Never raises — a focus nudge
    failing is not worth interrupting or even warning about mid-batch;
    the file-bridge dispatch this wraps around does not depend on it.
    """
    if sys.platform != "darwin":
        return
    script = (
        'tell application "System Events" to set aeProcs to '
        '(every process whose name begins with "Adobe After Effects")\n'
        'if (count of aeProcs) > 0 then\n'
        '    tell application "System Events" to set frontmost of (item 1 of aeProcs) to true\n'
        'end if'
    )
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        pass


# Report filenames the exporter/report_generator writes, e.g.
# "conform_report__87N_Reels_DEV__1080x1920.html". Anchored, so a
# caller-supplied name can be validated before it's ever joined onto a
# filesystem path (see _report_route below).
_REPORT_FILENAME_RE = re.compile(r"^conform_report__(?P<slug>.+)__(?P<dims>\d+x\d+)\.html$")
_HISTORY_SESSION_WINDOW_S = 60.0  # mtimes within 60s = one session


def _list_reports(project_root: str) -> list:
    """Python port of cep/js/reports_ui.js's _listReports(): scan
    project_root (the Dashboard's equivalent of the CEP panel's
    WORKING_DIR) for conform_report__*.html files. Deliberately reads
    the filesystem directly rather than maintaining a separate history
    index — the report files themselves are already the source of
    truth the CEP panel's History tab relies on, so this stays in sync
    with zero extra state to drift."""
    reports = []
    try:
        with os.scandir(project_root) as it:
            for entry in it:
                if not entry.is_file():
                    continue
                m = _REPORT_FILENAME_RE.match(entry.name)
                if not m:
                    continue
                st = entry.stat()
                reports.append({
                    "name": entry.name,
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                    "slug": m.group("slug"),
                    "dims": m.group("dims"),
                })
    except OSError:
        return []
    reports.sort(key=lambda r: r["mtime"], reverse=True)
    return reports


def _group_reports_by_session(reports: list) -> list:
    """Python port of cep/js/reports_ui.js's _groupReportsBySession():
    cluster mtimes into session buckets using the same 60s window, so
    a multi-target batch's several report files show up in the
    Dashboard as the one run they actually were, matching how the CEP
    panel's History tab already presents them."""
    sessions = []
    current = None
    for r in reports:
        if current is None or (current["last_mtime"] - r["mtime"]) > _HISTORY_SESSION_WINDOW_S:
            current = {"first_mtime": r["mtime"], "last_mtime": r["mtime"], "reports": []}
            sessions.append(current)
        current["reports"].append(r)
        current["last_mtime"] = min(current["last_mtime"], r["mtime"])
    return sessions


# Safety bailout for the transfer_status.log tail loop below — mirrors
# cep/js/main.js::_injectOneTarget's 30-minute bailout (195-layer/
# 46-precomp project took ~11 min per target).
TRANSFER_LOG_TAIL_TIMEOUT_S = 30 * 60.0
# Mirrors cep/js/backend_bridge.js::BB.startInjectTail's fs.watchFile
# poll interval (250ms) — this is a Python-process re-implementation of
# the same "read new bytes only" tailing approach, since dimension_server.py
# has no Node/JS runtime available to reuse BB.startInjectTail directly.
TRANSFER_LOG_TAIL_POLL_S = 0.25

# Statuses that stop the transfer_status.log tail loop immediately
# instead of waiting for TRANSFER_LOG_TAIL_TIMEOUT_S to elapse. Defined
# once here — do not scatter "COMPLETE"/"FAILED"/"ABORTED" literals at
# call sites — so a future fourth terminal status cannot be added to
# some sites and missed at others. ABORTED added as a consumer in Track A
# Stage 1 (2026-08-26); the producer (Babysitter.jsx) landed in Phase A3.
# See docs/architecture/track-a-beacon-contract.md §7.4.
TERMINAL_INJECT_STATUSES: tuple = ("COMPLETE", "FAILED", "ABORTED")

# Cooperative abort flag written next to transfer_status.log. Must match
# Babysitter.jsx::_abortRequestPath and
# cep/js/backend_bridge.js::BB.INJECT_ABORT_REQUEST_FILENAME.
# Presence of the file is the signal; contents are diagnostic.
INJECT_ABORT_REQUEST_FILENAME = "inject_abort_request.json"


def abort_request_path(working_dir: str) -> str:
    return os.path.join(working_dir, INJECT_ABORT_REQUEST_FILENAME)


def write_inject_abort_request(working_dir: str, source: str = "dashboard") -> str:
    """Write the A3 abort flag. Babysitter checks this at the start of
    each rewire / chunk / audit tick. Returns the path written.

    2026-08-27 audit fix: was a plain in-place `open(path, "w")` — the
    only JSON-write site in the Track A/B diffs that didn't go through
    `atomic_write_json`, against io_utils.py's own stated project-wide
    policy ("All disk writes in the pipeline use atomic_write()").
    Babysitter's `_abortRequested` only checks `File(p).exists` and never
    parses this file's contents (confirmed against
    Babysitter_src/65_abort.jsx and backend_bridge.js — neither consumer
    reads the JSON back today), so this closes a policy-consistency gap
    rather than a live defect. Atomicity does matter the moment any
    future consumer starts reading `source`/`t` back for diagnostics.
    """
    path = abort_request_path(working_dir)
    payload = {"requested": True, "source": source, "t": time.time()}
    atomic_write_json(path, payload)
    return path


def clear_inject_abort_request(working_dir: str) -> None:
    path = abort_request_path(working_dir)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def tail_transfer_log_until_terminal(
    log_path: str,
    timeout_s: float = TRANSFER_LOG_TAIL_TIMEOUT_S,
    poll_interval_s: float = TRANSFER_LOG_TAIL_POLL_S,
) -> list:
    """Tail `transfer_log` for new JSON lines until a terminal status
    line appears (one of `TERMINAL_INJECT_STATUSES` — currently
    COMPLETE, FAILED, or ABORTED), or `timeout_s` elapses.

    Ports, in Python, the exact tailing pattern
    `cep/js/backend_bridge.js::BB.startInjectTail` already uses in JS
    (read only the newly-appended bytes since the last read, split on
    newlines, parse each non-empty line as JSON) — `dimension_server.py`
    is a standalone Python process with no access to that JS helper, so
    this is a faithful re-implementation, not a novel design.

    Returns the list of parsed JSON-line dicts observed, in order
    (terminating early on the first terminal-status line — see
    TERMINAL_INJECT_STATUSES). Malformed (non-JSON) lines are skipped,
    matching the JS tailer's `catch (e) {/* not JSON */}` behavior.
    """
    deadline = time.time() + timeout_s
    last_size = 0
    logs: list = []
    while time.time() < deadline:
        try:
            size = os.path.getsize(log_path)
        except OSError:
            size = 0
        if size > last_size:
            try:
                with open(log_path, "rb") as f:
                    f.seek(last_size)
                    chunk = f.read(size - last_size)
                last_size = size
            except OSError:
                chunk = b""
            for raw_line in chunk.decode("utf-8", errors="replace").split("\n"):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                logs.append(msg)
                if msg.get("status") in TERMINAL_INJECT_STATUSES:
                    return logs
        time.sleep(poll_interval_s)
    return logs

WEB_DIR = os.path.join(application_path, "web")

# Global state for background task execution
execution_lock = threading.Lock()
execution_state = {
    "status": "idle",       # "idle", "running", "success", "failed", "cancelled"
    "current_target": 0,
    "total_targets": 0,
    "progress_pct": 0,
    "progress_text": "Waiting...",
    "logs": [],
    "warnings": {"skips": 0, "rewire": 0},
    "error": None,
    # Per-target conform outcome — populated as run_batch_thread works
    # through the queue. Each entry: {"target": <raw dict as POSTed>,
    # "label": str, "success": bool, "error": str|None}. Lets the
    # frontend offer "retry failed only" instead of re-queuing a whole
    # batch for one bad target.
    "target_results": [],
    # Set by POST /api/execute-batch/cancel. Honored between targets
    # during the conform phase (progress_pct < 90). Once inject has
    # started, the same flag is still set AND write_inject_abort_request
    # drops inject_abort_request.json next to transfer_status.log so
    # Babysitter stops at the next rewire/chunk/audit boundary (Track A
    # Phase A3). Cancel does not roll back already-applied chunks.
    "cancel_requested": False,
    # Working dir of the in-flight batch, so the cancel route can write
    # the abort flag without the POST body carrying a path.
    "project_root": None,
}


def log_to_state(msg: str) -> None:
    """Append a line to the dashboard batch log (thread-safe)."""
    with execution_lock:
        execution_state["logs"].append(msg)


def _license_block_message(status: dict) -> str:
    """User-facing message for a blocked /api/execute-batch call. The
    Dashboard has no activation UI of its own (2026-07-21 redesign scope
    decision) — license state is shared with the CEP panel via the same
    state.json, so the fix is always "activate over there", not here."""
    if status["status"] == "revoked":
        return "This license was refunded or disputed. Contact support if this is unexpected."
    if status["status"] == "expired":
        return "Your Dimension trial has expired. Open the Dimension panel in After Effects to see upgrade options."
    return "Activate your Dimension trial in the AE panel (Window → Extensions → Dimension) to run batch conforms from the Dashboard."


def run_batch_thread(manifest_path, profile, mode, bleed, targets, project_root, layout="auto", focus_ae=False):
    global execution_state

    total = len(targets)
    with execution_lock:
        execution_state["project_root"] = project_root
    log_to_state(
        f"Starting batch conform of {total} target(s) "
        f"using profile={profile}, mode={mode}, bleed={bleed}%, layout={layout}"
    )

    def _on_target_progress(line: str) -> None:
        log_to_state(line.strip())

    def _on_engine_event(event: dict) -> None:
        log_to_state(json.dumps(event))

    def _target_label(raw: dict) -> str:
        return raw.get("name") or raw.get("preset") or f"{raw.get('width')}x{raw.get('height')}"

    # Conform each target individually (rather than handing the whole
    # list to run_batch_conform in one call, per its normal batch API)
    # so this loop can (a) check for a mid-batch cancel between targets
    # and (b) record a per-target result — stages/batch.py's own return
    # shape is just (success_count, logs), with no way to tell which
    # target(s) failed. This keeps that shared function's contract
    # untouched (it has callers beyond the Dashboard) and puts the
    # per-target bookkeeping only where it's needed.
    success_count = 0
    cancelled = False
    for i, raw_target in enumerate(targets):
        with execution_lock:
            if execution_state["cancel_requested"]:
                cancelled = True
        if cancelled:
            log_to_state(f"Batch cancelled — {i}/{total} target(s) had already conformed.")
            break

        label = _target_label(raw_target)
        with execution_lock:
            execution_state["current_target"] = i + 1
            execution_state["total_targets"] = total
            execution_state["progress_pct"] = int((i / total) * 90) if total else 0
            execution_state["progress_text"] = f"Conforming target {i + 1} of {total}: {label}"
        log_to_state(f"--- Conforming Target {i + 1}/{total}: {label} ---")

        target_error = None
        try:
            sc, _logs = run_batch_conform(
                manifest_path,
                [raw_target],
                profile=profile,
                mode=mode,
                bleed=bleed,
                layout=layout,
                on_log=_on_target_progress,
                on_engine_event=_on_engine_event,
            )
            ok = sc == 1
            if not ok:
                target_error = "Conform failed — see console logs above for the target's error."
        except Exception as e:
            ok = False
            target_error = str(e)
            log_to_state(f"Error running conform for {label}: {e}")

        if ok:
            success_count += 1
        with execution_lock:
            execution_state["target_results"].append({
                "target": raw_target,
                "label": label,
                "success": ok,
                "error": target_error,
            })

    with execution_lock:
        execution_state["current_target"] = total
        execution_state["progress_pct"] = 90 if success_count else 0
        execution_state["progress_text"] = (
            f"Conform complete — {success_count}/{total} target(s) succeeded"
        )

    if cancelled and success_count == 0:
        with execution_lock:
            execution_state["status"] = "cancelled"
            execution_state["progress_text"] = "Batch cancelled — no targets had conformed yet."
        return

    if success_count == 0:
        log_to_state("All targets failed to conform. Skipping injection phase.")
        with execution_lock:
            execution_state["status"] = "failed"
            execution_state["progress_text"] = "Batch conform failed."
            execution_state["error"] = "All targets failed to conform."
        return

    if cancelled:
        # Some targets had already conformed before the cancel landed —
        # inject what succeeded rather than discarding completed work.
        log_to_state(f"Batch cancelled after {success_count}/{total} target(s) conformed — injecting the completed ones.")

    # 2. Inject conformed manifest via file-bridge (Babysitter chunk-pump)
    with execution_lock:
        execution_state["progress_pct"] = 92
        execution_state["progress_text"] = "Sending inject job to After Effects via file-bridge..."
        
    log_to_state("--- Starting ExtendScript Injection (Babysitter) ---")
    
    chunk_manifest = os.path.join(project_root, "chunk_manifest.json")
    transfer_log = os.path.join(project_root, "transfer_status.log")
    
    # Archive any prior transfer log instead of truncating in place —
    # a concurrent reader may still be tailing the file.
    if os.path.exists(transfer_log):
        archived = f"{transfer_log}.{int(time.time())}"
        try:
            os.rename(transfer_log, archived)
        except OSError:
            pass

    # Audit fix (2026-08-27) — the "clear a leftover abort flag from a
    # prior cancelled run" call used to live here. Removed: it created a
    # real, if narrow, TOCTOU race with the cancel route just above —
    # progress_pct hits 90 (>= 90 is this route's "past conform, write
    # the flag" threshold) several statements before this line runs, so
    # a cancel click landing in that window wrote a flag for THIS run
    # that this same call then unconditionally deleted, before Babysitter
    # ever polled for it. The Dashboard would report "cancel requested"
    # while inject silently ran to completion anyway. The clear now
    # happens once, at the true start of a fresh batch run in the
    # /api/execute-batch handler (before this run's own cancel window can
    # possibly open), so nothing at this point in the run should ever
    # touch the flag again — if it's set here, a real cancel for this
    # exact run wrote it, and Babysitter is meant to see it.
    if focus_ae:
        _activate_after_effects()

    try:
        log_to_state("Dispatching inject job to After Effects via file-bridge...")
        dispatch_file_bridge_inject(project_root, chunk_manifest, transfer_log)
    except Exception as ex:
        with execution_lock:
            execution_state["status"] = "failed"
            execution_state["progress_text"] = "AE bridge injection failed."
            execution_state["error"] = f"AE bridge injection failed: {str(ex)}"
        return

    log_to_state("Job dispatched — waiting for Babysitter to finish (tailing transfer_status.log)...")
    logs = tail_transfer_log_until_terminal(transfer_log)

    # Highest percentage the inject bar has reached for THIS batch run.
    # Track A Stage 2b (2026-08-26): mirrors the monotonic clamp
    # `_setInjectBarPct` applies in cep/js/main.js. Needed because
    # correcting stages/inject.py's INJECT_PHASE_PROGRESS dead keys (this
    # same stage) makes a real Babysitter emission — `{ phase: "INJECT",
    # event: "skip_inject_summary" }` firing mid-chunk-loop — visible as a
    # backward jump for the first time: before the dead-key fix, INJECT
    # was the only key that ever matched, so the bar was frozen at 93%
    # the whole time and no regression was observable. Declared as a
    # local variable inside run_batch_thread (not module-level) so it
    # resets to 0 on every new call — each Dashboard batch execution runs
    # in its own thread via a fresh run_batch_thread() invocation (see
    # dimension_server.py's execute-batch route), and this whole
    # dispatch/tail/process_inject_logs sequence fires exactly once per
    # such invocation (after ALL targets have been conformed), so there
    # is no multi-target loop to stale-carry this value across — but
    # scoping it here rather than at module level keeps that true even if
    # a future change adds one.
    _inject_bar_max_pct = 0

    def _on_phase(phase: str, pct: int) -> None:
        nonlocal _inject_bar_max_pct
        with execution_lock:
            if pct <= _inject_bar_max_pct:
                return
            _inject_bar_max_pct = pct
            execution_state["progress_pct"] = pct
            execution_state["progress_text"] = f"AE Injection · {phase}..."

    def _on_inject_log(line: str) -> None:
        log_to_state(f"[AE Progress] {line}")

    inject_outcome = process_inject_logs(
        logs,
        on_phase=_on_phase,
        on_log_line=_on_inject_log,
    )
    inject_completed = inject_outcome.completed
    inject_failed = inject_outcome.failed
    inject_aborted = inject_outcome.aborted
    warnings = inject_outcome.warnings
    if inject_failed:
        with execution_lock:
            for msg in reversed(inject_outcome.logs):
                if msg.get("status") == "FAILED":
                    err_msg = msg.get("error") or "AE injection failed."
                    if msg.get("failed_layer"):
                        err_msg += f" (Layer: {msg['failed_layer']}"
                        if msg.get("failed_detail"):
                            err_msg += f" — {msg['failed_detail']}"
                        err_msg += ")"
                    execution_state["error"] = err_msg
                    break

    if inject_completed:
        log_to_state("--- ExtendScript Injection Completed Successfully ---")
        with execution_lock:
            execution_state["status"] = "cancelled" if cancelled else "success"
            execution_state["progress_pct"] = 100
            execution_state["progress_text"] = (
                f"Cancelled after injecting {success_count}/{total} targets."
                if cancelled else
                f"Pipeline complete. Conformed {success_count}/{total} targets."
            )
            execution_state["warnings"] = warnings
    elif inject_failed:
        log_to_state("--- ExtendScript Injection Failed ---")
        with execution_lock:
            execution_state["status"] = "failed"
            execution_state["progress_pct"] = 100
            execution_state["progress_text"] = "ExtendScript injection failed."
    elif inject_aborted:
        log_to_state("--- ExtendScript Injection Cancelled ---")
        with execution_lock:
            execution_state["status"] = "cancelled"
            execution_state["progress_pct"] = 100
            execution_state["progress_text"] = (
                "Inject cancelled. Output comps may be incomplete — discard or retry. "
                "Cancel does not roll the whole job back; each finished chunk is its own undo group."
            )
            execution_state["error"] = None
    else:
        log_to_state("--- ExtendScript Injection Timed Out (30m) ---")
        with execution_lock:
            execution_state["status"] = "failed"
            execution_state["progress_pct"] = 100
            execution_state["progress_text"] = "ExtendScript injection timed out."
            execution_state["error"] = "Injection timed out after 30 minutes."

class DimensionAPIHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, manifest_path=None, **kwargs):
        self.manifest_path = manifest_path
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        parsed_path = urlparse(self.path)
        
        if parsed_path.path.startswith("/api/"):
            self.handle_api_get(parsed_path)
            return
            
        super().do_GET()

    def do_POST(self):
        parsed_path = urlparse(self.path)
        
        if parsed_path.path.startswith("/api/"):
            self.handle_api_post(parsed_path)
            return

        self.send_error(404, "Not Found")

    def handle_api_get(self, parsed_path):
        global execution_state
        
        if parsed_path.path == "/api/ping":
            self.send_json({"status": "ok", "message": "Dimension Server Running"})
        elif parsed_path.path == "/api/version":
            self.send_json({"status": "ok", **_version_info()})
        elif parsed_path.path == "/api/manifest":
            if not self.manifest_path or not os.path.exists(self.manifest_path):
                self.send_error(404, "Manifest not found")
                return
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    manifest_data = json.load(f)
                self.send_json({"status": "ok", "manifest": manifest_data})
            except Exception as e:
                self.send_error(500, f"Error reading manifest: {str(e)}")
        elif parsed_path.path == "/api/data":
            try:
                profiles = []
                for p in ProfileRegistry.list_profiles():
                    profiles.append({
                        "id": p.id,
                        "name": p.display_name or p.id,
                        "description": p.description
                    })
                tsm = TargetStoreManager()
                formats_grouped = {}
                for target in tsm.all_targets():
                    cat = target.subcategory or "General"
                    if cat not in formats_grouped:
                        formats_grouped[cat] = []
                    formats_grouped[cat].append({
                        "id": target.id,
                        "name": target.label,
                        "width": target.width,
                        "height": target.height,
                        "aspect_ratio": target.aspect_ratio,
                        "aspect_label": target.aspect_label,
                        "category": target.category.value if hasattr(target.category, "value") else str(target.category),
                        "subcategory": target.subcategory,
                        "duration": target.duration,
                        "fps": target.fps,
                        "metadata": target.metadata or {},
                    })
                self.send_json({
                    "status": "ok",
                    "profiles": profiles,
                    "formats_grouped": formats_grouped
                })
            except Exception as e:
                self.send_error(500, f"Error dumping data: {str(e)}")
        elif parsed_path.path == "/api/status":
            with execution_lock:
                self.send_json(dict(execution_state))
        elif parsed_path.path == "/api/history":
            if not self.manifest_path:
                self.send_json({"status": "ok", "sessions": []})
                return
            project_root = os.path.dirname(os.path.abspath(self.manifest_path))
            sessions = _group_reports_by_session(_list_reports(project_root))
            self.send_json({"status": "ok", "sessions": sessions})
        elif parsed_path.path == "/api/report":
            if not self.manifest_path:
                self.send_error(404, "No project loaded")
                return
            name = parse_qs(parsed_path.query).get("name", [""])[0]
            # Validate against the exact filename shape reports are
            # written as before ever joining it onto a filesystem path —
            # this is the only thing standing between a query param and
            # a path-traversal read, so the regex anchors are load-bearing.
            if not _REPORT_FILENAME_RE.match(name):
                self.send_error(400, "Invalid report filename")
                return
            project_root = os.path.dirname(os.path.abspath(self.manifest_path))
            report_path = os.path.join(project_root, name)
            if not os.path.isfile(report_path):
                self.send_error(404, "Report not found")
                return
            try:
                with open(report_path, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError as e:
                self.send_error(500, f"Error reading report: {str(e)}")
        elif parsed_path.path == "/api/license/status":
            try:
                status = get_current_status(_DIMENSION_REPO_ROOT, getattr(sys, "frozen", False))
                self.send_json({"status": "ok", "license": status})
            except Exception as e:
                self.send_error(500, f"Error reading license status: {str(e)}")
        else:
            self.send_error(404, "API endpoint not found")

    def handle_api_post(self, parsed_path):
        global execution_state
        
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        
        try:
            data = json.loads(body.decode('utf-8'))
        except Exception:
            self.send_error(400, "Invalid JSON")
            return

        project_root = "."
        if self.manifest_path:
            project_root = os.path.dirname(os.path.abspath(self.manifest_path))

        if parsed_path.path == "/api/select-layer":
            uid = data.get("uid")
            layer_index = data.get("layer_index")
            layer_name = data.get("layer_name")
            comp_name = data.get("comp_name")
            
            try:
                bridge = SovereignBridge(project_root)
                res = bridge.select_layer(
                    uid,
                    layer_index=layer_index,
                    layer_name=layer_name,
                    comp_name=comp_name
                )
                self.send_json({"status": "ok", "result": res})
            except Exception as e:
                self.send_error(500, f"Error selecting layer: {str(e)}")
                
        elif parsed_path.path == "/api/apply-tag":
            uid = data.get("uid")
            tag = data.get("tag")
            layer_index = data.get("layer_index")
            layer_name = data.get("layer_name")
            comp_name = data.get("comp_name")
            
            try:
                bridge = SovereignBridge(project_root)
                res = bridge.apply_manual_tag(
                    uid,
                    tag,
                    layer_index=layer_index,
                    layer_name=layer_name,
                    comp_name=comp_name
                )
                self.send_json({"status": "ok", "result": res})
            except Exception as e:
                self.send_error(500, f"Error applying tag: {str(e)}")
                
        elif parsed_path.path == "/api/clear-tag":
            uid = data.get("uid")
            layer_index = data.get("layer_index")
            layer_name = data.get("layer_name")
            comp_name = data.get("comp_name")
            
            try:
                bridge = SovereignBridge(project_root)
                res = bridge.clear_manual_tag(
                    uid,
                    layer_index=layer_index,
                    layer_name=layer_name,
                    comp_name=comp_name
                )
                self.send_json({"status": "ok", "result": res})
            except Exception as e:
                self.send_error(500, f"Error clearing tag: {str(e)}")
                
        elif parsed_path.path == "/api/save-conform-option":
            key = data.get("key")
            value = data.get("value")
            if not key:
                self.send_error(400, "Missing 'key'")
                return
            if not self.manifest_path:
                self.send_error(400, "No manifest loaded")
                return
            try:
                from pathlib import Path
                from core.conform_options import load_conform_options, save_conform_options

                dimension_dir = Path(self.manifest_path).parent
                opts = load_conform_options(dimension_dir)
                opts[key] = value
                save_conform_options(dimension_dir, opts)
                self.send_json({"status": "ok", "options": opts})
            except Exception as e:
                self.send_error(500, f"Error saving conform option: {str(e)}")



        elif parsed_path.path == "/api/execute-batch":
            license_status = get_current_status(_DIMENSION_REPO_ROOT, getattr(sys, "frozen", False))
            if not license_status["canUsePipeline"]:
                self.send_json({
                    "status": "error",
                    "code": "license_blocked",
                    "license_status": license_status["status"],
                    "message": _license_block_message(license_status),
                }, 403)
                return

            profile = data.get("profile", "default")
            mode = data.get("mode", "Fit")
            bleed = data.get("bleed", 0.0)
            layout = data.get("layout", "auto")
            focus_ae = bool(data.get("focus_ae", False))
            targets = data.get("targets", [])

            if not targets:
                self.send_error(400, "No targets specified")
                return

            try:
                bridge = SovereignBridge(project_root)
                ok, reason = bridge._poller_is_alive()
                if not ok:
                    self.send_json({
                        "status": "error",
                        "message": f"AE engine not responding: {reason} Please open After Effects, ensure the Dimension_Launcher panel is open, and click LAUNCH ENGINE."
                    }, 400)
                    return
            except Exception as e:
                self.send_json({
                    "status": "error",
                    "message": f"AE engine heartbeat check failed: {str(e)}"
                }, 400)
                return
                
            with execution_lock:
                if execution_state["status"] == "running":
                    self.send_json({"status": "error", "message": "A batch is already running."}, 400)
                    return
                execution_state["status"] = "running"
                execution_state["current_target"] = 0
                execution_state["total_targets"] = len(targets)
                execution_state["progress_pct"] = 0
                execution_state["progress_text"] = "Initializing batch..."
                execution_state["logs"] = []
                execution_state["warnings"] = {"skips": 0, "rewire": 0}
                execution_state["error"] = None
                execution_state["target_results"] = []
                execution_state["cancel_requested"] = False
                execution_state["project_root"] = None

            # Audit fix (2026-08-27) — clear any stale abort flag from a
            # prior run HERE, before this run's own cancel window opens,
            # not right before dispatch inside run_batch_thread (moved
            # from there — see the comment at that former call site).
            # This is the one point in the whole request lifecycle where
            # "no cancel for THIS run could possibly exist yet" is
            # actually true: status was just set to "running" under the
            # lock above, and the client hasn't even received this
            # response yet, so no Cancel click for this run is possible
            # before this line runs.
            clear_inject_abort_request(project_root)

            t = threading.Thread(
                target=run_batch_thread,
                args=(self.manifest_path, profile, mode, bleed, targets, project_root),
                kwargs={"layout": layout, "focus_ae": focus_ae},
                daemon=True
            )
            t.start()
            self.send_json({"status": "ok", "message": "Batch execution started."})

        elif parsed_path.path == "/api/execute-batch/cancel":
            with execution_lock:
                if execution_state["status"] != "running":
                    self.send_json({"status": "error", "message": "No batch is currently running."}, 400)
                    return
                already_past_conform = execution_state["progress_pct"] >= 90
                execution_state["cancel_requested"] = True
                project_root = execution_state.get("project_root")
            if already_past_conform:
                wrote = False
                if project_root:
                    try:
                        write_inject_abort_request(project_root, source="dashboard")
                        wrote = True
                    except OSError as exc:
                        log_to_state(f"Cancel requested, but abort flag write failed: {exc}")
                log_to_state(
                    "Cancel requested — inject will stop at the next chunk boundary."
                    if wrote else
                    "Cancel requested during inject, but the abort flag could not be written."
                )
                self.send_json({
                    "status": "ok",
                    "message": (
                        "Cancel requested — inject will stop at the next chunk boundary. "
                        "Already-applied chunks stay (each chunk is its own undo group)."
                        if wrote else
                        "Cancel requested, but the abort flag could not be written — inject may run to completion."
                    ),
                })
            else:
                log_to_state("Cancel requested by user — will stop after the current target finishes conforming.")
                self.send_json({"status": "ok", "message": "Cancelling after the current target finishes."})

        else:
            self.send_error(404, "API endpoint not found")

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def send_error(self, code, message=None, explain=None):
        """Always answer /api/ routes with JSON.

        SimpleHTTPRequestHandler.send_error renders http.server's default
        `<!DOCTYPE HTML>` error page. Every Dashboard fetch() calls
        res.json() unconditionally, so an HTML body surfaces in the panel
        as `SyntaxError: Unexpected token '<'` instead of the actual
        error — which is how a failed style-reference add looked like a
        silent no-op. Static file requests keep the normal HTML page.
        """
        if getattr(self, "path", "").startswith("/api/"):
            self.send_json({"status": "error", "message": message or "Error"}, code)
            return
        super().send_error(code, message, explain)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

def handler_factory(manifest_path):
    def create_handler(*args, **kwargs):
        return DimensionAPIHandler(*args, manifest_path=manifest_path, **kwargs)
    return create_handler

def start_server(port=4444, manifest_path=None, parent_pid=None, parent_name=None):
    if not os.path.exists(WEB_DIR):
        os.makedirs(WEB_DIR)

    server_address = ('', port)
    # ThreadingHTTPServer, not HTTPServer (TASK-ENG-05 / ADR 03). The
    # single-threaded base class serves one request at a time, so a slow
    # route — a conform kicked off from /api/, a large manifest read —
    # blocks the Dashboard's status polling behind it, which surfaces as
    # a UI that appears frozen rather than busy.
    httpd = ThreadingHTTPServer(server_address, handler_factory(manifest_path))
    print(f"Starting Dimension Dashboard Server on http://localhost:{port}", flush=True)
    if manifest_path:
        print(f"Using manifest: {manifest_path}", flush=True)

    # Parent watchdog: without it this process outlives AE and keeps the
    # port bound, so the NEXT AE launch cannot start its server.
    # Opt-in via --parent-pid; no pid means no watchdog (CLI/test runs).
    if parent_pid:
        # expected_name stays None unless explicitly overridden, so the
        # watchdog snapshots the parent's real identity itself. The panel
        # spawns us from CEPHtmlEngine, not the AE process, so any
        # hardcoded anchor here would mismatch and self-terminate at once.
        watchdog = ParentWatchdog(parent_pid, parent_name).start()
        if watchdog.armed:
            # flush=True because serve_forever() below blocks forever: with
            # stdout redirected to a file (how the CEP panel spawns us) a
            # buffered line would never reach disk, so "is the watchdog
            # actually armed?" would be unanswerable from the log.
            print(
                f"Parent watchdog armed on PID {parent_pid} "
                f"(identity anchor: '{watchdog.expected_name}')",
                flush=True,
            )

    httpd.serve_forever()

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Dimension Dashboard Server")
    parser.add_argument("--port", type=int, default=4444, help="Port to run the server on")
    parser.add_argument("--source", type=str, default=None, help="Path to scrape_manifest.json")
    parser.add_argument(
        "--parent-pid", type=int, default=None,
        help="PID of the launching After Effects process. When given, this "
             "server self-terminates within ~2s of that process exiting "
             "instead of orphaning itself and holding the port.",
    )
    parser.add_argument(
        "--parent-name", type=str, default=None,
        help="Process-name anchor for the parent PID identity check "
             f"(default: {DEFAULT_EXPECTED_NAME!r}; pass 'AfterFX' on Windows). "
             "Guards against the OS recycling the PID onto an unrelated process.",
    )
    args = parser.parse_args()

    start_server(args.port, args.source, args.parent_pid, args.parent_name)

if __name__ == "__main__":
    main()
