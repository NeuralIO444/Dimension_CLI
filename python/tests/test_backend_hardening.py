# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_backend_hardening.py — TASK-ENG-05 / ADR 03 (#260)
Unit tests for parent PID watchdog and robust_read_json retry wrapper.
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.io_utils import robust_read_json
from core.process_watchdog import (
    ParentWatchdog,
    _name_matches,
    _normalize_name,
)


class TestRobustReadJson:
    """Verifies exponential-backoff retry mechanics on file reads."""

    def test_clean_read_first_try(self, tmp_path: Path):
        f = tmp_path / "valid.json"
        f.write_text(json.dumps({"status": "OK", "count": 42}))
        res = robust_read_json(str(f))
        assert res == {"status": "OK", "count": 42}

    def test_retries_on_json_decode_error_then_succeeds(self, tmp_path: Path):
        f = tmp_path / "torn.json"
        # Start half-written
        f.write_text('{"status": "OK", "c')

        mock_sleep = MagicMock()
        attempts = 0

        def fake_sleep(duration):
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                # Flush the rest on 2nd retry
                f.write_text(json.dumps({"status": "OK", "complete": True}))

        res = robust_read_json(str(f), _sleep=fake_sleep)
        assert res == {"status": "OK", "complete": True}
        assert attempts >= 2

    def test_does_not_retry_file_not_found(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.json"
        mock_sleep = MagicMock()
        with pytest.raises(FileNotFoundError):
            robust_read_json(str(missing), _sleep=mock_sleep)
        assert mock_sleep.call_count == 0

    def test_exhaustion_raises_last_exception(self, tmp_path: Path):
        f = tmp_path / "permanently_broken.json"
        f.write_text('{"unclosed')
        mock_sleep = MagicMock()
        with pytest.raises(json.JSONDecodeError):
            robust_read_json(str(f), max_attempts=3, _sleep=mock_sleep)
        assert mock_sleep.call_count == 2


class TestParentWatchdog:
    """Verifies watchdog parent process polling and identity verification."""

    def test_normalize_name(self):
        assert _normalize_name("/Applications/Adobe After Effects 2026/After Effects") == "aftereffects"
        assert _normalize_name("C:\\Program Files\\Adobe\\AfterFX.exe") == "afterfx"
        assert _normalize_name("CEPHtmlEngine") == "cephtmlengine"

    def test_name_matches(self):
        assert _name_matches("Adobe After Effects 2026", "After Effects")
        assert _name_matches("AfterFX.exe", "AfterFX")
        assert not _name_matches("Spotify.exe", "After Effects")

    def test_watchdog_inactive_by_default(self):
        dog = ParentWatchdog(parent_pid=os.getpid())
        assert not dog.is_alive
        assert not dog.armed

    def test_watchdog_arm_and_trigger_on_dead_parent(self):
        triggered = False

        def on_dead():
            nonlocal triggered
            triggered = True

        # Use an invalid high PID that doesn't exist
        dead_pid = 99999999
        dog = ParentWatchdog(
            parent_pid=dead_pid,
            expected_name="NonExistentProcess_12345",
            poll_interval_s=0.05,
            on_parent_death=on_dead,
        )
        assert dog.arm() is True
        dog.start()
        time.sleep(0.2)
        dog.stop()
        assert triggered is True
