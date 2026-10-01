#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Unified pipeline log / heartbeat / inbox check for operators.

Wraps slot_17_analyse_ae_log.py for bridge + inject timing summaries and
adds live heartbeat, inbox, and transfer_status.log tails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT / "python" / "scripts"))

from pytest_dimension_ae.probes import probe_ae  # noqa: E402
from slot_17_analyse_ae_log import (  # noqa: E402
    latest_bridge_round_trips,
    latest_inject_run,
    parse_lines,
    render_bridge_table,
    render_inject_table,
)


def _transfer_tail(repo: Path, lines: int = 5) -> list[str]:
    tlog = repo / "transfer_status.log"
    if not tlog.is_file():
        return ["transfer_status.log: missing"]
    tail = tlog.read_text(encoding="utf-8", errors="replace").splitlines()
    if not tail:
        return ["transfer_status.log: empty"]
    return [f"transfer_status.log (last {lines}):"] + tail[-lines:]


def _slot17_summary(log_path: Path) -> dict:
    records = parse_lines(log_path)
    return {
        "record_count": len(records),
        "bridge_round_trips": latest_bridge_round_trips(records)[:3],
        "latest_inject": latest_inject_run(records),
    }


def run_check(
    repo: Path,
    log_path: Path,
    *,
    transfer_lines: int = 5,
) -> dict:
    probe = probe_ae(repo)
    slot17 = _slot17_summary(log_path)
    return {
        "probe": probe.to_dict(),
        "inbox": {
            "pending": probe.inbox_pending,
            "claimed": probe.inbox_claimed,
        },
        "transfer_tail": _transfer_tail(repo, transfer_lines)[1:],
        "slot17": slot17,
    }


def render_human(payload: dict, log_path: Path) -> str:
    probe = payload["probe"]
    lines = [
        "══════════════════════════════════════════════════════════════",
        "  Dimension pipeline log check",
        "══════════════════════════════════════════════════════════════",
        "",
        f"  READY: {probe['ready']} — {probe['summary']}",
        f"  AE process: {probe['ae_process']}",
        f"  Socket :45445: {probe['socket_listening']}",
        (
            f"  Heartbeat: exists={probe['heartbeat_exists']} "
            f"fresh={probe['heartbeat_fresh']}"
        ),
    ]
    if probe.get("heartbeat_age_s") is not None:
        lines.append(f"             age={probe['heartbeat_age_s']:.1f}s")
    if probe.get("heartbeat_text"):
        lines.append(f"             {probe['heartbeat_text']}")
    lines.append(
        f"  Inbox: pending={probe['inbox_pending']} "
        f"claimed={probe['inbox_claimed']}"
    )
    lines.append("")

    for row in _transfer_tail(Path(probe["repo_root"])):
        lines.append(f"  {row}")
    lines.append("")

    slot17 = payload["slot17"]
    lines.append(f"  dimension.log: {log_path} ({slot17['record_count']} records)")
    bridge = slot17.get("bridge_round_trips") or []
    if not bridge:
        lines.append("  (no bridge handshake markers in log)")
    else:
        for rt in bridge[:2]:
            lines.append(render_bridge_table(rt))
            lines.append("")
    inject = slot17.get("latest_inject")
    if inject:
        lines.append(render_inject_table(inject))
    else:
        lines.append("  (no recent inject run in log)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Unified heartbeat, inbox, transfer log, and slot_17 summary.",
    )
    parser.add_argument(
        "--repo",
        default=str(REPO_ROOT),
        help="Dimension repo root",
    )
    parser.add_argument(
        "--log",
        default="logs/dimension.log",
        help="Path to dimension.log (default: ./logs/dimension.log)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON",
    )
    parser.add_argument(
        "--transfer-lines",
        type=int,
        default=5,
        help="Lines of transfer_status.log to include (default: 5)",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    log_path = Path(args.log)
    if not log_path.is_absolute():
        log_path = (repo / log_path).resolve()

    if not log_path.is_file():
        print(f"WARNING: log not found at {log_path}", file=sys.stderr)

    payload = run_check(repo, log_path, transfer_lines=args.transfer_lines)

    if args.json:
        payload["log_path"] = str(log_path)
        print(json.dumps(payload, indent=2, default=str))
        return 0

    print(render_human(payload, log_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())