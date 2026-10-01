# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_bridge_validation.py
PR-B Layer 3 — Dispatch/receive validation integration tests.

What these tests cover (and how they relate to Layer 1/Layer 2)
----------------------------------------------------------------
Layer 1 (test_bridge_jobs.py) verifies the Pydantic schemas in
isolation — synthetic dicts go in, parse helpers behave correctly.

Layer 2 (test_bridge_contracts_jsx.py — scaffolded in commit 6,
fixtures pending manual AE capture) verifies the schemas accept
what JSX actually writes by parsing real result.json files.

Layer 3 (this file) verifies that the schemas are actually
ENFORCED at the file-bridge boundary in production code paths.
We mock the JSX side (background thread that watches the inbox
and writes a synthetic result) and assert that:

  - Dispatch-side validation fires before write_job_file actually
    writes anything to disk
  - Receive-side validation fires before _wait_for_result returns
    to callers
  - BridgeSchemaVersionError surfaces verbatim on version mismatch
  - pydantic ValidationError on shape gets wrapped as
    ScrapeEngineError so existing callers' broad-except clauses
    still catch the failure
  - Successful round-trips still return raw dicts unchanged
    (existing callers consume payload.get("status") etc.)

Synthetic mocks are appropriate at this layer because the contract
under test here is "does the validation fire at the right edges?",
not "does the schema match wire reality?". Layer 2 is the wire-
reality guard.

Test pattern follows test_duplication_bridge.py and
test_tag_write_index_fallback.py — bridge gets a tmp project root,
poller-alive is patched true, a background thread emulates the
JSX side by watching the inbox and writing synthetic responses.
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

from models.bridge_jobs import (  # noqa: E402
    BRIDGE_SCHEMA_VERSION,
    BridgeSchemaVersionError,
)


# ── Fixtures + helpers ───────────────────────────────────────────


@pytest.fixture()
def bridge_factory(tmp_path, monkeypatch):
    """Yield a builder for SovereignBridge with poller-alive
    patched true and a tmp project root."""
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
                      capture_jobs: list = None):
    """Background thread that watches the inbox for a typed job
    descriptor matching job_type, captures it, then writes the
    response to the result path. Mirrors the pattern in
    test_duplication_bridge.py / test_tag_write_index_fallback.py."""
    if capture_jobs is None:
        capture_jobs = []

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
                capture_jobs.append(desc)
                result_path = Path(desc["result"])
                tmp = result_path.with_suffix(result_path.suffix + ".tmp")
                tmp.write_text(json.dumps(response), encoding="utf-8")
                os.replace(tmp, result_path)
                return
            time.sleep(0.02)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, stop, capture_jobs


# ── Dispatch-side validation ─────────────────────────────────────


class TestDispatchValidation:
    """_write_job_file validates the descriptor through
    parse_typed_job before write. A broken descriptor never
    reaches the inbox — fail-fast on Python-side bugs."""

    def test_dispatch_stamps_schema_version(self, bridge_factory):
        """Callers don't have to remember to stamp schema_version —
        _write_job_file does it via setdefault. Verify the
        dispatched descriptor carries the version stamp."""
        bridge = bridge_factory()
        captured: list = []
        responder, stop, captured = _spawn_responder(
            Path(bridge._inbox_dir()),
            {"schema_version": BRIDGE_SCHEMA_VERSION,
             "job_type": "tag-write", "status": "OK", "uid": "u-1"},
            job_type="tag-write", capture_jobs=captured,
        )
        try:
            bridge.apply_manual_tag("u-1", "HERO", timeout_s=4.0)
        finally:
            stop.set(); responder.join(timeout=2)

        assert len(captured) == 1
        assert captured[0]["schema_version"] == BRIDGE_SCHEMA_VERSION

    def test_dispatch_validation_catches_python_side_bug(
        self, bridge_factory, monkeypatch,
    ):
        """If a Python-side bug builds an invalid descriptor (e.g.
        missing required field), validation fires before the
        atomic write reaches disk. Simulate by monkeypatching
        apply_manual_tag's job-construction step to omit `uid`."""

        bridge = bridge_factory()

        # Build an invalid descriptor by hand and call _write_job_file
        # directly. This is the Python-side-bug shape: missing `uid`.
        invalid_job = {
            "type": "tag-write",
            # uid omitted — required field
            "tag": "HERO",
            "assets": "/tmp/assets",
            "ts": time.time(),
        }
        with pytest.raises(ValueError) as exc_info:
            bridge._write_job_file(invalid_job)
        # Validation fires before write — message includes context.
        assert "tag-write" in str(exc_info.value)

    def test_dispatch_refuses_wrong_schema_version(
        self, bridge_factory, monkeypatch,
    ):
        """If a caller deliberately pre-stamps a bogus schema_version
        (the only path a wrong version reaches dispatch — _write_job_file
        uses setdefault, so a pre-stamped value wins), validation
        refuses with BridgeSchemaVersionError."""
        bridge = bridge_factory()
        bogus_job = {
            "schema_version": "99.99",   # bogus
            "type": "scrape",
            "manifest": "/tmp/m.json",
            "mode": "standard",
            "assets": "/tmp/assets",
            "ts": time.time(),
        }
        with pytest.raises(BridgeSchemaVersionError) as exc_info:
            bridge._write_job_file(bogus_job)
        assert exc_info.value.received == "99.99"

    def test_dispatch_does_not_write_invalid_descriptor_to_disk(
        self, bridge_factory,
    ):
        """Validation fires before any FS state changes. A failed
        validation must NOT leave a .tmp file or a final job_*.json
        in the inbox."""
        bridge = bridge_factory()
        inbox = Path(bridge._inbox_dir())
        invalid_job = {
            "type": "tag-write",
            "tag": "HERO",  # missing uid
            "assets": "/tmp/assets",
            "ts": time.time(),
        }
        try:
            bridge._write_job_file(invalid_job)
        except ValueError:
            pass

        leftover = list(inbox.glob("job_*"))
        assert leftover == [], (
            f"validation failure left files in the inbox: {leftover}"
        )


# ── Receive-side validation ──────────────────────────────────────


class TestReceiveValidation:
    """_wait_for_result validates the JSX result payload through
    parse_result before returning. Contract drift surfaces as either
    BridgeSchemaVersionError (verbatim) or ScrapeEngineError
    (pydantic ValidationError wrapped)."""

    def test_round_trip_with_valid_response_returns_raw_dict(
        self, bridge_factory,
    ):
        """Successful round-trip still returns the raw payload dict
        unchanged so existing callers (apply_manual_tag, etc.) keep
        consuming payload.get("status") as before."""
        bridge = bridge_factory()
        responder, stop, _ = _spawn_responder(
            Path(bridge._inbox_dir()),
            {"schema_version": BRIDGE_SCHEMA_VERSION,
             "job_type": "tag-write", "status": "OK",
             "uid": "u-1", "tag": "HERO",
             "source": "manual_comment", "label": 1},
            job_type="tag-write",
        )
        try:
            result = bridge.apply_manual_tag(
                "u-1", "HERO", timeout_s=4.0,
            )
        finally:
            stop.set(); responder.join(timeout=2)

        # Raw dict, not a Pydantic model — preserves caller contracts.
        assert isinstance(result, dict)
        assert result["status"] == "OK"
        assert result["tag"] == "HERO"

    def test_receive_refuses_missing_schema_version(
        self, bridge_factory,
    ):
        """JSX panel pre-PR-B writes results without schema_version.
        Receive validation must refuse with BridgeSchemaVersionError
        — actionable message tells the user to reload the panel."""
        bridge = bridge_factory()
        responder, stop, _ = _spawn_responder(
            Path(bridge._inbox_dir()),
            # NB: no schema_version, no job_type — pre-PR-B shape
            {"status": "OK", "uid": "u-1", "tag": "HERO"},
            job_type="tag-write",
        )
        try:
            with pytest.raises(BridgeSchemaVersionError) as exc_info:
                bridge.apply_manual_tag("u-1", "HERO", timeout_s=4.0)
        finally:
            stop.set(); responder.join(timeout=2)

        assert exc_info.value.received is None
        assert "Reload the AE Dimension panel" in str(exc_info.value)

    def test_receive_refuses_wrong_schema_version(
        self, bridge_factory,
    ):
        """JSX panel from a future branch with a bumped contract
        version. Receive validation refuses verbatim."""
        bridge = bridge_factory()
        responder, stop, _ = _spawn_responder(
            Path(bridge._inbox_dir()),
            {"schema_version": "2.0",  # future version
             "job_type": "tag-write", "status": "OK", "uid": "u-1"},
            job_type="tag-write",
        )
        try:
            with pytest.raises(BridgeSchemaVersionError) as exc_info:
                bridge.apply_manual_tag("u-1", "HERO", timeout_s=4.0)
        finally:
            stop.set(); responder.join(timeout=2)

        assert exc_info.value.received == "2.0"
        assert exc_info.value.expected == BRIDGE_SCHEMA_VERSION

    def test_receive_wraps_shape_error_as_scrape_engine_error(
        self, bridge_factory,
    ):
        """Pydantic ValidationError (e.g. unknown status, malformed
        field) gets wrapped as ScrapeEngineError so existing callers'
        broad-except clauses still catch it without API changes."""
        from bridge.sovereign_bridge import ScrapeEngineError
        bridge = bridge_factory()
        responder, stop, _ = _spawn_responder(
            Path(bridge._inbox_dir()),
            {"schema_version": BRIDGE_SCHEMA_VERSION,
             "job_type": "tag-write",
             "status": "MAYBE",  # not a valid status enum value
             "uid": "u-1"},
            job_type="tag-write",
        )
        try:
            with pytest.raises(ScrapeEngineError) as exc_info:
                bridge.apply_manual_tag("u-1", "HERO", timeout_s=4.0)
        finally:
            stop.set(); responder.join(timeout=2)

        # Error message includes context for debugging.
        assert "tag-write" in str(exc_info.value)
        assert "MAYBE" in str(exc_info.value)

    def test_receive_refuses_missing_job_type(
        self, bridge_factory,
    ):
        """Without job_type, the discriminated union can't dispatch.
        Pydantic ValidationError → ScrapeEngineError wrapping."""
        from bridge.sovereign_bridge import ScrapeEngineError
        bridge = bridge_factory()
        responder, stop, _ = _spawn_responder(
            Path(bridge._inbox_dir()),
            {"schema_version": BRIDGE_SCHEMA_VERSION,
             # no job_type
             "status": "OK", "uid": "u-1"},
            job_type="tag-write",
        )
        try:
            with pytest.raises(ScrapeEngineError):
                bridge.apply_manual_tag("u-1", "HERO", timeout_s=4.0)
        finally:
            stop.set(); responder.join(timeout=2)


# ── Inject-path validation (launcher.py) ─────────────────────────


class TestInjectDispatchValidation:
    """python/launcher.py inject path stamps + validates through
    parse_inject_job before write. Mirrors the typed-job pattern
    but for the legacy untyped descriptor."""

    def test_inject_validation_accepts_well_formed_descriptor(self):
        """Smoke test: parse_inject_job accepts the exact wire shape
        launcher.py builds. If this breaks, the launcher.py dispatch
        path is broken."""
        from models.bridge_jobs import parse_inject_job
        # Mirrors python/launcher.py:266 verbatim
        job = {
            "manifest":      "/tmp/manifest.json",
            "log":           "/tmp/transfer.log",
            "babysitter":    "/Scripts/Dimension_Assets/Babysitter.jsx",
            "auditor":       "/Scripts/Dimension_Assets/Auditor.jsx",
            "ts":            time.time(),
            "version":       1,
            "schema_version": BRIDGE_SCHEMA_VERSION,
        }
        j = parse_inject_job(job)
        assert j.manifest == "/tmp/manifest.json"
        assert j.assets is None      # inject doesn't set this
        assert j.result is None      # inject is heartbeat-driven

    def test_inject_validation_refuses_typed_descriptor(self):
        """Pass a typed scrape descriptor to parse_inject_job —
        must refuse (extra `type` field on InjectJob with
        extra=forbid)."""
        from models.bridge_jobs import parse_inject_job
        from pydantic import ValidationError

        typed = {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "type": "scrape",
            "manifest": "/tmp/m.json",
            "mode": "standard",
            "assets": "/tmp/a",
            "ts": time.time(),
        }
        with pytest.raises(ValidationError):
            parse_inject_job(typed)


# ── Cleanup-on-timeout (Slot 11 Bug A) ───────────────────────────


class TestWaitForResultCleanup:
    """Slot 11 (2026-05-15) — Bug A fix verification.

    Pre-Slot-11, `_wait_for_result` only cleaned up the result file
    on the success path. Timeout, validation error, unreadable file,
    and any other exception leaked the result file (and any `.tmp`
    sibling left over from a JSX crash mid-rename). Stage A
    quarantined leaks to `results/`; Stage B kills the leak itself
    via try/finally + a best-effort cleanup helper.

    These tests prove cleanup runs on every exit path.
    """

    def test_cleanup_runs_on_timeout(self, bridge_factory, tmp_path):
        """Timeout path must unlink the result file.

        Strategy: pre-write a result file at the path the bridge
        would poll, then call `_wait_for_result` with `timeout_s=0`.
        The poll loop's `while time.monotonic() < deadline` is False
        on entry, so the loop body never reads the file, and the
        function falls through to `raise ScrapeTimeoutError`. The
        finally block must clean up the pre-written file.
        """
        from bridge.sovereign_bridge import ScrapeTimeoutError

        bridge = bridge_factory()
        # Mirror Stage A's layout — result files live in `results/`.
        results_dir = Path(bridge._inbox_dir()) / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        result_path = results_dir / "job_test.result.json"
        # Pre-write the file so cleanup has something to remove.
        result_path.write_text('{"status": "OK"}', encoding="utf-8")
        assert result_path.exists(), "fixture setup"

        with pytest.raises(ScrapeTimeoutError):
            bridge._wait_for_result(str(result_path), timeout_s=0.0)

        assert not result_path.exists(), (
            "Bug A: timeout path leaked the result file"
        )

    def test_cleanup_removes_tmp_sibling_on_timeout(
        self, bridge_factory, tmp_path,
    ):
        """Cleanup must also unlink the `.tmp` sibling if present.

        Models the narrow case where JSX crashed mid-rename and
        left a partial write behind. The cleanup helper checks both
        the final path and `path + ".tmp"`.
        """
        from bridge.sovereign_bridge import ScrapeTimeoutError

        bridge = bridge_factory()
        results_dir = Path(bridge._inbox_dir()) / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        result_path = results_dir / "job_test.result.json"
        tmp_sibling = Path(str(result_path) + ".tmp")
        # Only the .tmp sibling exists — final never landed.
        tmp_sibling.write_text('{"partial": true}', encoding="utf-8")
        assert tmp_sibling.exists(), "fixture setup"

        with pytest.raises(ScrapeTimeoutError):
            bridge._wait_for_result(str(result_path), timeout_s=0.0)

        assert not tmp_sibling.exists(), (
            "Bug A: timeout path leaked the .tmp sibling"
        )
        assert not result_path.exists(), "final path should also be clean"

    def test_cleanup_runs_on_validation_error(self, bridge_factory):
        """Validation-error path must unlink the result file too.

        Strategy: have the responder write a result with a bogus
        schema_version. `parse_result` raises
        BridgeSchemaVersionError; the finally block must still run
        cleanup. Asserted by polling the results dir for emptiness
        after the raise.
        """
        bridge = bridge_factory()
        results_dir = Path(bridge._inbox_dir()) / "results"
        responder, stop, _ = _spawn_responder(
            Path(bridge._inbox_dir()),
            {"schema_version": "99.99",  # bogus → BridgeSchemaVersionError
             "job_type": "tag-write", "status": "OK", "uid": "u-1"},
            job_type="tag-write",
        )
        try:
            with pytest.raises(BridgeSchemaVersionError):
                bridge.apply_manual_tag("u-1", "HERO", timeout_s=4.0)
        finally:
            stop.set(); responder.join(timeout=2)

        # After the raise, the results dir must contain no
        # leftover result files. Pre-Slot-11 this would have a
        # `.result.json` sitting there.
        if results_dir.exists():
            leftover = list(results_dir.glob("*.result.json"))
            assert leftover == [], (
                f"Bug A: validation error left files in results/: {leftover}"
            )

    def test_cleanup_runs_on_success(self, bridge_factory):
        """Success path must still clean up (regression guard for
        the pre-Slot-11 success-only cleanup behavior — Stage B
        moves the cleanup into the finally, so success-path
        cleanup must still happen)."""
        bridge = bridge_factory()
        results_dir = Path(bridge._inbox_dir()) / "results"
        responder, stop, _ = _spawn_responder(
            Path(bridge._inbox_dir()),
            {"schema_version": BRIDGE_SCHEMA_VERSION,
             "job_type": "tag-write", "status": "OK",
             "uid": "u-1", "tag": "HERO",
             "source": "manual_comment", "label": 1},
            job_type="tag-write",
        )
        try:
            bridge.apply_manual_tag("u-1", "HERO", timeout_s=4.0)
        finally:
            stop.set(); responder.join(timeout=2)

        if results_dir.exists():
            leftover = list(results_dir.glob("*.result.json"))
            assert leftover == [], (
                f"success path left files in results/: {leftover}"
            )


class TestPollerPreflight:
    """Socket listen failure workaround — heartbeat counts as alive."""

    def test_poller_is_alive_via_fresh_heartbeat(self, tmp_path, monkeypatch):
        from bridge.sovereign_bridge import SovereignBridge

        b = SovereignBridge(project_root=str(tmp_path))
        monkeypatch.setattr(b, "_socket_is_listening", lambda: False)
        os.makedirs(b._inbox_dir(), exist_ok=True)
        Path(b._heartbeat_path()).write_text(
            "tick_count=1\n", encoding="utf-8"
        )
        ok, reason = b._poller_is_alive()
        assert ok is True
        assert reason == ""

    def test_poller_is_alive_dead_without_socket_or_heartbeat(
        self, tmp_path, monkeypatch
    ):
        from bridge.sovereign_bridge import SovereignBridge

        b = SovereignBridge(project_root=str(tmp_path))
        monkeypatch.setattr(b, "_socket_is_listening", lambda: False)
        ok, reason = b._poller_is_alive()
        assert ok is False
        assert "heartbeat" in reason.lower()


class TestBridgeDispatchFallback:
    """Verify socket direct connect and file-bridge fallback."""

    def test_fallback_on_socket_connection_error(self, tmp_path, monkeypatch):
        from bridge.sovereign_bridge import SovereignBridge
        from core.ipc_client import IPCConnectionError

        b = SovereignBridge(project_root=str(tmp_path))

        # 1. Mock _execute_socket_job to raise IPCConnectionError (simulating closed socket)
        def mock_execute_socket(*args, **kwargs):
            raise IPCConnectionError("Connection refused")
        monkeypatch.setattr(b, "_execute_socket_job", mock_execute_socket)

        # 2. Mock _execute_file_bridge_job to return a dummy successful result
        dummy_result = {"status": "OK", "job_type": "scrape", "schema_version": "5.1.0"}
        monkeypatch.setattr(
            b,
            "_execute_file_bridge_job",
            lambda job, timeout: dummy_result
        )

        # 3. Setup fresh heartbeat so file-bridge is allowed
        os.makedirs(b._inbox_dir(), exist_ok=True)
        Path(b._heartbeat_path()).write_text("tick_count=1\n", encoding="utf-8")

        # 4. Dispatch job and assert it falls back and returns successfully
        job = {"type": "scrape", "manifest": "foo.json"}
        res = b._execute_bridge_job(job, timeout_s=1.0)
        assert res == dummy_result
