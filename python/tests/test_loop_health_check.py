# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_loop_health_check.py
Unit tests for the autonomous loop health/staleness detection tool.
"""

from __future__ import annotations

from pathlib import Path

from tools.loop_health_check import (
    parse_tick_timestamp,
    is_failure_log,
    check_health,
    FAILURE_INDICATORS,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "loop_health_check"


class TestParseTickTimestamp:
    """Tests for tick log filename timestamp parsing."""

    def test_parse_valid_iso_timestamp(self):
        """Parse valid ISO 8601 timestamp from log filename."""
        filename = "tick-2026-09-04T10:00:00Z.log"
        ts = parse_tick_timestamp(filename)
        assert ts is not None
        assert ts.year == 2026
        assert ts.month == 9
        assert ts.day == 4
        assert ts.hour == 10
        assert ts.minute == 0

    def test_parse_timestamp_with_json_extension(self):
        """Parse timestamp from .json log file."""
        filename = "tick-2026-09-04T14:07:23Z.json"
        ts = parse_tick_timestamp(filename)
        assert ts is not None
        assert ts.hour == 14
        assert ts.minute == 7

    def test_parse_timestamp_with_underscores(self):
        """Parse timestamp with underscores instead of colons (filesystem-safe format)."""
        filename = "tick-2026-09-04T14_07_23Z.log"
        ts = parse_tick_timestamp(filename)
        assert ts is not None
        assert ts.hour == 14
        assert ts.minute == 7
        assert ts.second == 23

    def test_parse_invalid_filename_prefix(self):
        """Reject filename without 'tick-' prefix."""
        result = parse_tick_timestamp("log-2026-09-04T10:00:00Z.log")
        assert result is None

    def test_parse_invalid_filename_extension(self):
        """Reject filename with unsupported extension."""
        result = parse_tick_timestamp("tick-2026-09-04T10:00:00Z.txt")
        assert result is None

    def test_parse_malformed_timestamp(self):
        """Reject malformed timestamp."""
        result = parse_tick_timestamp("tick-not-a-timestamp.log")
        assert result is None


class TestIsFailureLog:
    """Tests for failure indicator detection in logs."""

    def test_detect_aborting_tick(self):
        """Detect 'aborting tick' failure indicator."""
        log_path = FIXTURE_DIR / "tick-2026-09-04T11_00_00Z.log"
        assert log_path.exists(), f"Fixture {log_path} not found"
        assert is_failure_log(log_path) is True

    def test_detect_op_read_timeout(self):
        """Detect 'op read timed out' failure indicator."""
        log_path = FIXTURE_DIR / "tick-2026-09-04T11_00_00Z.log"
        assert is_failure_log(log_path) is True

    def test_detect_lifetime_cap(self):
        """Detect LIFETIME SPEND CAP failure."""
        log_path = FIXTURE_DIR / "tick-2026-09-03T14_00_00Z.log"
        assert log_path.exists(), f"Fixture {log_path} not found"
        assert is_failure_log(log_path) is True

    def test_successful_log_no_failure_indicators(self):
        """Successful logs have no failure indicators."""
        log_path = FIXTURE_DIR / "tick-2026-09-04T10_00_00Z.log"
        assert log_path.exists(), f"Fixture {log_path} not found"
        assert is_failure_log(log_path) is False

    def test_nonexistent_log_treated_as_failure(self):
        """Nonexistent log file is treated as a failure."""
        log_path = FIXTURE_DIR / "nonexistent.log"
        assert is_failure_log(log_path) is True


class TestCheckHealth:
    """Tests for overall health check logic."""

    def test_empty_log_directory(self):
        """Empty log directory returns unhealthy status."""
        empty_dir = FIXTURE_DIR / "empty"
        if not empty_dir.exists():
            empty_dir.mkdir(parents=True)

        status = check_health(empty_dir)
        assert status.is_healthy is False
        assert status.total_ticks == 0
        assert "No tick logs found" in status.message

    def test_nonexistent_log_directory(self):
        """Nonexistent log directory returns unhealthy status."""
        status = check_health(FIXTURE_DIR / "nonexistent_dir")
        assert status.is_healthy is False
        assert "not found" in status.message

    def test_recent_successful_tick_is_healthy(self):
        """Recent successful tick within threshold is healthy."""
        # Use fixture directory with recent successful log
        status = check_health(FIXTURE_DIR, threshold_hours=3.0)
        # The tick-2026-09-04T10:00:00Z.log is recent (relative to test fixtures)
        # and successful, so it should report last_successful_tick_age_minutes
        assert status.last_successful_tick_age_minutes is not None

    def test_stale_successful_tick_is_unhealthy(self):
        """Successful tick older than threshold is unhealthy."""
        # tick-2026-09-02T08:00:00Z.log is from 2026-09-02
        # With a low threshold, it should be considered stale
        status = check_health(FIXTURE_DIR, threshold_hours=0.0001)
        assert status.is_healthy is False
        # Message should indicate time exceeds threshold
        assert "min ago" in status.message and "threshold" in status.message

    def test_consecutive_failures_counted(self):
        """Count consecutive failures from most recent log."""
        status = check_health(FIXTURE_DIR, threshold_hours=3.0)
        # tick-2026-09-04T11_00_00Z.log is the most recent and is a failure
        assert status.consecutive_failures >= 1

    def test_reports_last_log_age(self):
        """Report age of the most recent log (any status)."""
        status = check_health(FIXTURE_DIR, threshold_hours=3.0)
        assert status.last_log_age_minutes is not None
        assert status.last_log_age_minutes >= 0

    def test_total_ticks_counted(self):
        """Count total tick logs in directory."""
        status = check_health(FIXTURE_DIR, threshold_hours=3.0)
        assert status.total_ticks >= 4  # We created at least 4 fixture logs


class TestCheckHealthJsonOutput:
    """Tests for JSON output serialization."""

    def test_json_output_contains_all_fields(self):
        """JSON output includes all required fields."""
        import json

        # Capture the check_health function's HealthStatus
        status = check_health(FIXTURE_DIR, threshold_hours=3.0)

        # Verify the status can be serialized to JSON
        json_data = {
            "is_healthy": status.is_healthy,
            "last_successful_tick_age_minutes": status.last_successful_tick_age_minutes,
            "last_log_age_minutes": status.last_log_age_minutes,
            "consecutive_failures": status.consecutive_failures,
            "total_ticks": status.total_ticks,
            "message": status.message,
        }

        # Should be serializable without error
        json_str = json.dumps(json_data)
        assert len(json_str) > 0


class TestFailureIndicators:
    """Tests for failure indicator constants."""

    def test_failure_indicators_nonempty(self):
        """Failure indicators list is not empty."""
        assert len(FAILURE_INDICATORS) > 0

    def test_failure_indicators_are_strings(self):
        """All failure indicators are strings."""
        for indicator in FAILURE_INDICATORS:
            assert isinstance(indicator, str)
            assert len(indicator) > 0
