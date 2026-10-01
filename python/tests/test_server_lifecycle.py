# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
TASK-ENG-05 / TASK-ENG-QA-01 — parent-process watchdog + robust JSON reads.

Covers ADR 03 and Pre-Mortem Scenario 3 (PID recycling). The scenario that
matters most is NOT "does it notice a dead parent" — it is the pair of
opposite failures either side of that:

  * a recycled PID (parent died, OS handed the integer to Chrome) must be
    seen as DEAD, or the server zombies forever holding port 4444;
  * a transient `ps` failure must NOT be seen as dead, or a hiccup kills a
    server whose AE is running fine.

Both are asserted below. Everything is dependency-injected — no real
subprocesses spawned, no real processes killed, no sleeping.
"""

import json
import os
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.io_utils import robust_read_json  # noqa: E402
from core.process_watchdog import (  # noqa: E402
    DEFAULT_POLL_INTERVAL_S,
    ParentWatchdog,
    _name_matches,
    _normalize_name,
    _query_process_name,
    probe_parent,
)


# ── name normalization (the cross-platform anchor) ─────────────────────

class TestNameNormalization:
    def test_macos_comm_path_reduces_to_basename(self):
        raw = "/Applications/Adobe After Effects 2024/After Effects.app/Contents/MacOS/After Effects"
        assert _normalize_name(raw) == "aftereffects"

    def test_windows_exe_suffix_stripped(self):
        assert _normalize_name("AfterFX.exe") == "afterfx"

    def test_matches_macos_process_against_default_anchor(self):
        assert _name_matches("/Contents/MacOS/After Effects", "After Effects")

    def test_matches_windows_process_against_windows_anchor(self):
        assert _name_matches("AfterFX.exe", "AfterFX")

    def test_unrelated_process_does_not_match(self):
        # The recycled-PID impostors from Pre-Mortem Scenario 3.
        for impostor in ("Google Chrome", "Spotify", "python3.13", "node"):
            assert not _name_matches(impostor, "After Effects"), impostor


# ── probe_parent tri-state ─────────────────────────────────────────────

class TestProbeParent:
    def test_alive_when_pid_exists_and_name_matches(self):
        state = probe_parent(
            4242, "After Effects",
            _exists=lambda pid: True,
            _name=lambda pid: "/Contents/MacOS/After Effects",
        )
        assert state is True

    def test_definitively_dead_when_pid_absent(self):
        state = probe_parent(
            4242, "After Effects",
            _exists=lambda pid: False,
            _name=lambda pid: pytest.fail("name query must be skipped when pid is gone"),
        )
        assert state is False

    def test_recycled_pid_reads_as_dead_not_alive(self):
        """Pre-Mortem Scenario 3, the headline case.

        AE died; the OS handed PID 4242 to Chrome. Existence alone says
        "alive" — only the identity check catches it. A False here is what
        stops the server zombie-ing forever.
        """
        state = probe_parent(
            4242, "After Effects",
            _exists=lambda pid: True,
            _name=lambda pid: "Google Chrome",
        )
        assert state is False

    def test_unknown_when_name_query_fails_but_pid_exists(self):
        """The inverse trap: a failed `ps` must never read as death."""
        state = probe_parent(
            4242, "After Effects",
            _exists=lambda pid: True,
            _name=lambda pid: None,
        )
        assert state is None

    def test_unknown_when_both_probes_are_inconclusive(self):
        state = probe_parent(
            4242, "After Effects",
            _exists=lambda pid: None,
            _name=lambda pid: None,
        )
        assert state is None


# ── watchdog behavior ──────────────────────────────────────────────────

class TestParentWatchdog:
    def test_fires_death_callback_on_definitive_death(self):
        fired = []
        wd = ParentWatchdog(
            4242, on_parent_death=lambda: fired.append(True),
            _probe=lambda pid, name: False,
        )
        wd.check_once()
        assert fired == [True]

    def test_does_not_fire_while_parent_is_alive(self):
        fired = []
        wd = ParentWatchdog(
            4242, on_parent_death=lambda: fired.append(True),
            _probe=lambda pid, name: True,
        )
        for _ in range(10):
            wd.check_once()
        assert fired == []

    def test_does_not_fire_on_unknown_however_long_it_persists(self):
        """A sandbox that blocks `ps` forever must not kill a healthy server."""
        fired = []
        wd = ParentWatchdog(
            4242, on_parent_death=lambda: fired.append(True),
            _probe=lambda pid, name: None,
        )
        for _ in range(50):
            wd.check_once()
        assert fired == []
        assert wd.unknown_streak == 50

    def test_unknown_streak_resets_once_the_parent_answers_again(self):
        states = [None, None, True]
        wd = ParentWatchdog(4242, on_parent_death=lambda: None,
                            _probe=lambda pid, name: states.pop(0))
        wd.check_once(); wd.check_once()
        assert wd.unknown_streak == 2
        wd.check_once()
        assert wd.unknown_streak == 0

    def test_expected_name_is_forwarded_to_the_probe(self):
        seen = {}

        def _probe(pid, name):
            seen["pid"], seen["name"] = pid, name
            return True

        ParentWatchdog(99, expected_name="AfterFX", _probe=_probe).check_once()
        assert seen == {"pid": 99, "name": "AfterFX"}


    def test_detects_death_within_the_2s_budget_on_a_live_thread(self):
        """Issue #260 requires self-termination within 2.0s of parent death;
        issue #262's harness allows 3.0s. Runs the real thread with a short
        interval so the assertion is about wiring, not wall-clock patience."""
        fired = threading.Event()
        wd = ParentWatchdog(
            4242, poll_interval_s=0.05,
            on_parent_death=fired.set,
            _probe=lambda pid, name: False,
            _name=lambda pid: "CEPHtmlEngine",
        )
        wd.start()
        try:
            assert fired.wait(timeout=2.0), "watchdog never fired on a dead parent"
        finally:
            wd.stop()

    def test_default_poll_interval_meets_the_2s_requirement(self):
        # One definitive reading is enough to act, so worst-case detection
        # is a single interval. Guards against someone widening the poll
        # past the budget without noticing.
        assert DEFAULT_POLL_INTERVAL_S <= 2.0

    def test_stop_is_prompt_and_idempotent(self):
        wd = ParentWatchdog(4242, poll_interval_s=30.0,
                            on_parent_death=lambda: None,
                            _probe=lambda pid, name: True,
                            _name=lambda pid: "CEPHtmlEngine")
        wd.start()
        started = time.monotonic()
        wd.stop()
        # Event.wait-based sleeping means stop() must not block for a full
        # 30s poll interval.
        assert time.monotonic() - started < 2.0
        wd.stop()  # second call must not raise

    def test_probe_exception_does_not_kill_the_watchdog_thread(self):
        calls = []

        def _boom(pid, name):
            calls.append(1)
            raise RuntimeError("ps exploded")

        wd = ParentWatchdog(4242, poll_interval_s=0.02,
                            on_parent_death=lambda: pytest.fail("must not fire"),
                            _probe=_boom,
                            _name=lambda pid: "CEPHtmlEngine")
        wd.start()
        try:
            time.sleep(0.2)
            assert len(calls) > 1, "thread died after the first probe exception"
        finally:
            wd.stop()

    def test_start_is_idempotent(self):
        wd = ParentWatchdog(4242, poll_interval_s=30.0,
                            on_parent_death=lambda: None,
                            _probe=lambda pid, name: True,
                            _name=lambda pid: "CEPHtmlEngine")
        try:
            assert wd.start() is wd.start()
        finally:
            wd.stop()


# ── real-process smoke (no mocks) ──────────────────────────────────────


class TestIdentityAnchorAutoSnapshot:
    """arm() resolves the identity anchor instead of trusting a caller's guess.

    This exists because the CEP panel spawns the server from CEPHtmlEngine,
    not from the After Effects process. A hardcoded "After Effects" anchor
    would mismatch on the very first poll and kill the server on every
    single launch — a self-inflicted outage strictly worse than the zombie
    bug being fixed.
    """

    def test_snapshots_the_parent_name_when_none_is_supplied(self):
        wd = ParentWatchdog(4242, _name=lambda pid: "CEPHtmlEngine",
                            _probe=lambda pid, name: True)
        assert wd.arm() is True
        assert wd.expected_name == "CEPHtmlEngine"

    def test_an_explicit_anchor_is_respected_and_not_overwritten(self):
        wd = ParentWatchdog(
            4242, expected_name="AfterFX",
            _name=lambda pid: pytest.fail("must not query when told the name"),
            _probe=lambda pid, name: True,
        )
        assert wd.arm() is True
        assert wd.expected_name == "AfterFX"

    def test_refuses_to_arm_when_the_parent_name_is_unreadable(self):
        wd = ParentWatchdog(4242, _name=lambda pid: None,
                            _probe=lambda pid, name: True)
        assert wd.arm() is False
        assert wd.armed is False

    def test_start_fails_open_when_it_cannot_arm(self):
        """No anchor means we cannot tell a recycled PID from the real
        parent. Running unwatched is recoverable; killing a live server
        mid-conform is not."""
        wd = ParentWatchdog(
            4242, _name=lambda pid: None,
            on_parent_death=lambda: pytest.fail("must never fire unarmed"),
            _probe=lambda pid, name: False,
        )
        wd.start()
        try:
            assert wd.armed is False
            assert wd._thread is None, "no polling thread should be running"
            time.sleep(0.15)
        finally:
            wd.stop()

    def test_a_cephtmlengine_parent_is_watched_correctly_end_to_end(self):
        """The real CEP shape: anchor auto-snapshotted, then the same name
        keeps reading alive, and an impostor on the recycled PID reads dead."""
        wd = ParentWatchdog(4242, _name=lambda pid: "CEPHtmlEngine")
        assert wd.arm()
        assert probe_parent(4242, wd.expected_name,
                            _exists=lambda pid: True,
                            _name=lambda pid: "CEPHtmlEngine") is True
        assert probe_parent(4242, wd.expected_name,
                            _exists=lambda pid: True,
                            _name=lambda pid: "Spotify") is False

class TestQueryProcessNameAgainstRealProcesses:
    def test_resolves_this_interpreter_by_its_own_pid(self):
        name = _query_process_name(os.getpid())
        assert name, "could not read this process's own name"
        assert "python" in _normalize_name(name)

    def test_returns_none_for_a_reaped_child(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        assert _query_process_name(proc.pid) is None

    def test_end_to_end_death_detection_on_a_real_child(self):
        """No mocks at all: spawn a child, watch it, kill it, expect a fire."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        fired = threading.Event()
        wd = ParentWatchdog(
            proc.pid, expected_name="python", poll_interval_s=0.1,
            on_parent_death=fired.set,
        )
        wd.start()
        try:
            assert not fired.wait(timeout=0.5), "fired while the child was alive"
            proc.kill()
            proc.wait()
            assert fired.wait(timeout=3.0), "did not detect the child's death"
        finally:
            wd.stop()
            if proc.poll() is None:
                proc.kill(); proc.wait()


# ── robust_read_json ───────────────────────────────────────────────────

class TestRobustReadJson:
    def test_reads_valid_json(self, tmp_path):
        p = tmp_path / "ok.json"
        p.write_text(json.dumps({"layers": [1, 2, 3]}), encoding="utf-8")
        assert robust_read_json(str(p)) == {"layers": [1, 2, 3]}

    def test_does_not_sleep_on_a_first_try_success(self, tmp_path):
        p = tmp_path / "ok.json"
        p.write_text("{}", encoding="utf-8")
        slept = []
        robust_read_json(str(p), _sleep=slept.append)
        assert slept == []

    def test_retries_then_succeeds_on_a_torn_read(self, tmp_path, monkeypatch):
        """A JSX-side writer that is not atomic can be observed mid-flush."""
        p = tmp_path / "torn.json"
        p.write_text('{"ok": true}', encoding="utf-8")
        real_open, calls = open, {"n": 0}

        def flaky_open(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] <= 2:
                # Simulate a partial file rather than faking the exception,
                # so the real json parser produces the real error type.
                import io
                return io.StringIO('{"ok": tr')
            return real_open(*args, **kwargs)

        monkeypatch.setattr("builtins.open", flaky_open)
        slept = []
        assert robust_read_json(str(p), _sleep=slept.append) == {"ok": True}
        assert calls["n"] == 3
        assert slept == [0.02, 0.04], "expected exponential backoff between retries"

    def test_retries_on_permission_error_then_succeeds(self, tmp_path, monkeypatch):
        """The WinError 32 case ADR 03 names explicitly."""
        p = tmp_path / "locked.json"
        p.write_text('{"v": 1}', encoding="utf-8")
        real_open, calls = open, {"n": 0}

        def flaky_open(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError(32, "The process cannot access the file")
            return real_open(*args, **kwargs)

        monkeypatch.setattr("builtins.open", flaky_open)
        assert robust_read_json(str(p), _sleep=lambda d: None) == {"v": 1}

    def test_reraises_the_original_exception_after_exhausting_attempts(self, tmp_path, monkeypatch):
        """Callers already branch on PermissionError/WinError numbers — the
        retry wrapper must not swap in a wrapper type and break them."""
        p = tmp_path / "locked.json"
        p.write_text("{}", encoding="utf-8")

        def always_locked(*args, **kwargs):
            raise PermissionError(32, "The process cannot access the file")

        monkeypatch.setattr("builtins.open", always_locked)
        with pytest.raises(PermissionError) as exc:
            robust_read_json(str(p), _sleep=lambda d: None)
        assert exc.value.errno == 32

    def test_total_backoff_budget_is_about_300ms(self, tmp_path, monkeypatch):
        p = tmp_path / "locked.json"
        p.write_text("{}", encoding="utf-8")

        def always_locked(*args, **kwargs):
            raise PermissionError(32, "locked")

        monkeypatch.setattr("builtins.open", always_locked)
        slept = []
        with pytest.raises(PermissionError):
            robust_read_json(str(p), _sleep=slept.append)
        assert slept == [0.02, 0.04, 0.08, 0.16]
        assert abs(sum(slept) - 0.30) < 1e-9

    def test_missing_file_fails_fast_without_retrying(self, tmp_path):
        """FileNotFoundError is control flow across this codebase ("no
        sidecar yet"). Retrying it would add 300ms to every such check."""
        slept = []
        with pytest.raises(FileNotFoundError):
            robust_read_json(str(tmp_path / "nope.json"), _sleep=slept.append)
        assert slept == []

    def test_rejects_a_nonsensical_attempt_count(self, tmp_path):
        p = tmp_path / "ok.json"
        p.write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError):
            robust_read_json(str(p), max_attempts=0)
