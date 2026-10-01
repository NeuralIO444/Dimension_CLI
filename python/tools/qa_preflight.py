# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Pre-flight QA bundle: AE probe + inbox hygiene + pipeline log tail.

Combines ae_status and check_pipeline_logs with stale-job detection.
Exit 0 when AE is ready and no stale inbox jobs; 1 otherwise.
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
from tools.pipeline_log_check import run_check  # noqa: E402
from tools.qa_common import scan_inbox_orphans  # noqa: E402


def run_preflight(
    repo: Path,
    log_path: Path,
    *,
    stale_days: float = 7.0,
    transfer_lines: int = 3,
) -> dict:
    probe = probe_ae(repo)
    pipeline = run_check(repo, log_path, transfer_lines=transfer_lines)
    orphans = scan_inbox_orphans(repo, stale_days=stale_days)

    issues: list[str] = []
    if not probe.ready:
        issues.append(f"AE not ready: {probe.summary}")
    if orphans.has_stale:
        n = len(orphans.stale_pending) + len(orphans.stale_claimed)
        issues.append(f"{n} stale inbox job(s) older than {stale_days}d")
    if probe.inbox_pending > 10:
        issues.append(f"high inbox pending count: {probe.inbox_pending}")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "probe": probe.to_dict(),
        "pipeline": pipeline,
        "inbox": {
            "stale_threshold_days": stale_days,
            "pending_count": len(orphans.pending),
            "claimed_count": len(orphans.claimed),
            "stale_pending": [
                {
                    "path": str(j.path),
                    "age_days": j.age_days,
                    "job_type": j.job_type,
                }
                for j in orphans.stale_pending
            ],
            "stale_claimed": [
                {
                    "path": str(j.path),
                    "age_days": j.age_days,
                    "job_type": j.job_type,
                }
                for j in orphans.stale_claimed
            ],
        },
    }


def render_human(payload: dict) -> str:
    lines = [
        "══════════════════════════════════════════════════════════════",
        "  Dimension QA preflight",
        "══════════════════════════════════════════════════════════════",
        "",
        f"  OK: {payload['ok']}",
    ]
    for issue in payload.get("issues", []):
        lines.append(f"  ✗ {issue}")

    probe = payload.get("probe", {})
    lines.extend(
        [
            "",
            f"  AE ready:   {probe.get('ready')} — {probe.get('summary')}",
            f"  Socket:     {probe.get('socket_listening')}",
            f"  Heartbeat:  fresh={probe.get('heartbeat_fresh')}",
            f"  Inbox:      pending={probe.get('inbox_pending')} "
            f"claimed={probe.get('inbox_claimed')}",
        ]
    )

    inbox = payload.get("inbox", {})
    stale_p = inbox.get("stale_pending") or []
    stale_c = inbox.get("stale_claimed") or []
    if stale_p or stale_c:
        lines.append("")
        lines.append(
            f"  Stale jobs (>{inbox.get('stale_threshold_days')}d):"
        )
        for job in stale_p + stale_c:
            lines.append(
                f"    • {Path(job['path']).name} "
                f"({job.get('age_days')}d, type={job.get('job_type')})"
            )
        lines.append("")
        lines.append(
            "  Hint: move stale jobs to "
            ".dimension_inbox/quarantine_YYYYMMDD/ or delete if abandoned."
        )

    transfer = payload.get("pipeline", {}).get("transfer_tail") or []
    if transfer:
        lines.append("")
        lines.append("  transfer_status.log (tail):")
        for row in transfer[-3:]:
            lines.append(f"    {row}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AE readiness + inbox hygiene + pipeline log preflight.",
    )
    parser.add_argument("--repo", default=str(REPO_ROOT))
    parser.add_argument("--log", default="logs/dimension.log")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--stale-days",
        type=float,
        default=7.0,
        help="Flag inbox jobs older than N days (default: 7)",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    log_path = Path(args.log)
    if not log_path.is_absolute():
        log_path = (repo / log_path).resolve()

    payload = run_preflight(repo, log_path, stale_days=args.stale_days)

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render_human(payload))

    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())