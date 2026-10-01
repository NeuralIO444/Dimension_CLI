#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""CLI wrapper for Dimension AE readiness probes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "python"))

from pytest_dimension_ae.probes import probe_ae  # noqa: E402


def _human_status(result) -> str:
    lines = [
        "Dimension AE probe",
        f"  repo:        {result.repo_root}",
        f"  ready:       {'yes' if result.ready else 'no'} — {result.summary}",
        f"  ae_process:  {result.ae_process}",
        f"  socket:      {result.socket_listening} (:45445)",
        f"  heartbeat:   exists={result.heartbeat_exists} fresh={result.heartbeat_fresh}",
    ]
    if result.heartbeat_age_s is not None:
        lines.append(f"               age={result.heartbeat_age_s:.1f}s")
    if result.heartbeat_text:
        lines.append(f"               {result.heartbeat_text}")
    lines.append(
        f"  inbox:       pending={result.inbox_pending} claimed={result.inbox_claimed}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe AE / poller / inbox status.")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="Print AE readiness probe")
    status.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON",
    )
    status.add_argument(
        "--repo",
        default=str(REPO_ROOT),
        help="Dimension repo root (default: auto-detected)",
    )
    args = parser.parse_args(argv)

    if args.command == "status":
        result = probe_ae(args.repo)
        if args.json:
            print(result.to_json())
        else:
            print(_human_status(result))
        return 0 if result.ready else 1

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())