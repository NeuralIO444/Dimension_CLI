#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/loop_health_check.py
Health/staleness detection for the governed autonomous loop.

The loop runs hourly via launchd but has no built-in health reporting. This tool
parses tick logs and detects:
- Time since last successful tick
- Time since last log of any kind (catches silent sleep/crash)
- Consecutive failure count
- Exits non-zero if staleness exceeds threshold

Usage:
  python3 python/tools/loop_health_check.py                    # use default log dir
  python3 python/tools/loop_health_check.py --log-dir /path    # custom log dir
  python3 python/tools/loop_health_check.py --threshold-hours 2  # custom threshold
  python3 python/tools/loop_health_check.py --json             # machine-readable output

Typical location (outside repo, on Matt's Mac):
  ~/.dimension_loop/logs/

Tick log filename format:
  tick-<UTC-timestamp>.log       (human-readable log)
  tick-<UTC-timestamp>.json      (raw JSON result from `claude -p`)

Exit codes:
  0: healthy (last tick within threshold)
  1: stale (last successful tick exceeds threshold, or no logs at all)
  2: configuration error
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple


class HealthStatus(NamedTuple):
    is_healthy: bool
    last_successful_tick_age_minutes: float | None
    last_log_age_minutes: float | None
    consecutive_failures: int
    total_ticks: int
    message: str


# Failure indicators to grep for in tick logs
FAILURE_INDICATORS = [
    "aborting tick",
    "op read timed out",
    "LIFETIME SPEND CAP REACHED",
]


def parse_tick_timestamp(filename: str) -> datetime | None:
    """
    Extract and parse UTC timestamp from tick log filename.
    Format: tick-<ISO-8601-timestamp>.log or tick-<ISO-8601-timestamp>.json
    Example: tick-2026-09-04T14:07:23Z.log or tick-2026-09-04T14_07_23Z.log
    Accepts both colons and underscores in time components (colons for display,
    underscores to avoid filesystem issues on some systems).
    Returns None if filename doesn't match expected format.
    """
    if not filename.startswith("tick-"):
        return None
    if not (filename.endswith(".log") or filename.endswith(".json")):
        return None

    # Extract the timestamp part: tick-<TIMESTAMP>.log
    timestamp_part = filename[5:]  # skip "tick-"
    if timestamp_part.endswith(".log"):
        timestamp_part = timestamp_part[:-4]
    elif timestamp_part.endswith(".json"):
        timestamp_part = timestamp_part[:-5]
    else:
        return None

    try:
        # Replace underscores with colons to normalize format (both are valid)
        normalized = timestamp_part.replace("_", ":")
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def is_failure_log(log_path: Path) -> bool:
    """
    Check if a tick log contains any failure indicator strings.
    Returns True if log contains failure indicators, False otherwise.
    """
    try:
        content = log_path.read_text(encoding="utf-8", errors="ignore")
        return any(indicator in content for indicator in FAILURE_INDICATORS)
    except Exception:
        # If we can't read the log, assume it's a failure
        return True


def check_health(log_dir: Path, threshold_hours: float = 3.0) -> HealthStatus:
    """
    Analyze tick logs in log_dir and return health status.

    Args:
        log_dir: Directory containing tick-*.log files
        threshold_hours: Max age (in hours) of last successful tick

    Returns:
        HealthStatus with results and exit code
    """
    if not log_dir.exists():
        return HealthStatus(
            is_healthy=False,
            last_successful_tick_age_minutes=None,
            last_log_age_minutes=None,
            consecutive_failures=0,
            total_ticks=0,
            message=f"Log directory not found: {log_dir}",
        )

    # Collect all tick log files and their timestamps
    tick_logs = []
    for entry in log_dir.iterdir():
        if entry.suffix == ".log" and entry.name.startswith("tick-"):
            ts = parse_tick_timestamp(entry.name)
            if ts:
                tick_logs.append((ts, entry))

    if not tick_logs:
        return HealthStatus(
            is_healthy=False,
            last_successful_tick_age_minutes=None,
            last_log_age_minutes=None,
            consecutive_failures=0,
            total_ticks=0,
            message=f"No tick logs found in {log_dir}",
        )

    # Sort by timestamp, newest first
    tick_logs.sort(key=lambda x: x[0], reverse=True)
    total_ticks = len(tick_logs)

    # Get current time in UTC
    now = datetime.now(timezone.utc)

    # Age of most recent log (any kind)
    most_recent_timestamp = tick_logs[0][0]
    last_log_age = now - most_recent_timestamp
    last_log_age_minutes = last_log_age.total_seconds() / 60

    # Find last successful tick (no failure indicators)
    last_successful_tick_age_minutes = None
    consecutive_failures = 0

    for ts, log_path in tick_logs:
        if not is_failure_log(log_path):
            # Found a successful tick
            last_successful_tick_age_minutes = (now - ts).total_seconds() / 60
            break
        else:
            consecutive_failures += 1

    # Determine health status
    is_healthy = True
    threshold_minutes = threshold_hours * 60

    if last_successful_tick_age_minutes is None:
        # All logs are failures — definitely unhealthy
        is_healthy = False
        message = (
            f"All {total_ticks} recent ticks failed. "
            f"Most recent: {last_log_age_minutes:.1f} min ago."
        )
    elif last_successful_tick_age_minutes > threshold_minutes:
        # Last successful tick is too old
        is_healthy = False
        message = (
            f"Last successful tick {last_successful_tick_age_minutes:.1f} min ago "
            f"(threshold: {threshold_minutes:.1f} min). "
            f"{consecutive_failures} consecutive failures since."
        )
    else:
        # Healthy
        message = (
            f"Last successful tick {last_successful_tick_age_minutes:.1f} min ago. "
            f"{consecutive_failures} consecutive failures. "
            f"Total ticks: {total_ticks}."
        )

    return HealthStatus(
        is_healthy=is_healthy,
        last_successful_tick_age_minutes=last_successful_tick_age_minutes,
        last_log_age_minutes=last_log_age_minutes,
        consecutive_failures=consecutive_failures,
        total_ticks=total_ticks,
        message=message,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path.home() / ".dimension_loop" / "logs",
        help="Directory containing tick logs (default: ~/.dimension_loop/logs)",
    )
    parser.add_argument(
        "--threshold-hours",
        type=float,
        default=3.0,
        help="Max age (hours) of last successful tick before unhealthy (default: 3)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON",
    )
    args = parser.parse_args()

    if args.threshold_hours <= 0:
        print("Error: --threshold-hours must be positive", file=sys.stderr)
        sys.exit(2)

    status = check_health(args.log_dir, args.threshold_hours)

    if args.json:
        print(
            json.dumps(
                {
                    "is_healthy": status.is_healthy,
                    "last_successful_tick_age_minutes": status.last_successful_tick_age_minutes,
                    "last_log_age_minutes": status.last_log_age_minutes,
                    "consecutive_failures": status.consecutive_failures,
                    "total_ticks": status.total_ticks,
                    "message": status.message,
                }
            )
        )
    else:
        print(status.message)

    sys.exit(0 if status.is_healthy else 1)


if __name__ == "__main__":
    main()
