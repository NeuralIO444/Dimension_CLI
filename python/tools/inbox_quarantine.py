# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Quarantine stale file-bridge inbox jobs (RECOMMENDATIONS M7).

Moves `job_*.json` files older than N days (default 7) from
`.dimension_inbox/` into `.dimension_inbox/quarantine_YYYYMMDD/` —
the exact convention qa_preflight's stale-job hint already names.
Stale jobs in `.dimension_inbox/processing/` (claimed by the JSX
poller) are REPORTED but never moved: a claimed file is the poller's
property even when it looks abandoned; yanking it mid-flight risks a
half-processed job. Quarantine those by hand after confirming AE is
closed.

`--dry-run` lists what would move without touching anything.
Exit 0 always, unless an actual I/O error occurs while moving (exit 1).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "python"))

from tools.qa_common import scan_inbox_orphans  # noqa: E402


def quarantine_stale_jobs(
    repo: Path,
    *,
    days: float = 7.0,
    dry_run: bool = False,
    today: date | None = None,
) -> dict:
    """Move stale pending inbox jobs into a dated quarantine folder.

    Returns a summary payload; `errors` non-empty means an I/O failure.
    """
    scan = scan_inbox_orphans(repo, stale_days=days)
    quarantine_dir = (
        repo / ".dimension_inbox"
        / f"quarantine_{(today or date.today()).strftime('%Y%m%d')}"
    )

    moved: list[dict] = []
    errors: list[str] = []

    for job in scan.stale_pending:
        entry = {
            "name": job.path.name,
            "age_days": job.age_days,
            "job_type": job.job_type,
            "from": str(job.path),
            "to": str(quarantine_dir / job.path.name),
        }
        if not dry_run:
            try:
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                job.path.replace(quarantine_dir / job.path.name)
            except OSError as e:
                errors.append(f"{job.path.name}: {e}")
                continue
        moved.append(entry)

    return {
        "dry_run": dry_run,
        "days": days,
        "quarantine_dir": str(quarantine_dir),
        "moved": moved,
        "stale_claimed_not_moved": [
            {
                "name": j.path.name,
                "age_days": j.age_days,
                "job_type": j.job_type,
                "path": str(j.path),
            }
            for j in scan.stale_claimed
        ],
        "pending_total": len(scan.pending),
        "claimed_total": len(scan.claimed),
        "errors": errors,
    }


def render_human(payload: dict) -> str:
    verb = "would move" if payload["dry_run"] else "moved"
    lines = [
        "══════════════════════════════════════════════════════════════",
        "  Dimension inbox quarantine",
        "══════════════════════════════════════════════════════════════",
        "",
        f"  Threshold:  jobs older than {payload['days']}d",
        f"  Pending jobs scanned: {payload['pending_total']} "
        f"(claimed: {payload['claimed_total']})",
        f"  Stale pending {verb}: {len(payload['moved'])}",
    ]
    for entry in payload["moved"]:
        lines.append(
            f"    • {entry['name']} ({entry['age_days']}d, "
            f"type={entry['job_type']})"
        )
    if payload["moved"]:
        lines.append(f"  Quarantine dir: {payload['quarantine_dir']}")

    claimed = payload["stale_claimed_not_moved"]
    if claimed:
        lines.append("")
        lines.append(
            f"  ⚠ {len(claimed)} stale CLAIMED job(s) in processing/ — "
            "not moved (poller-owned; quarantine by hand with AE closed):"
        )
        for entry in claimed:
            lines.append(f"    • {entry['name']} ({entry['age_days']}d)")

    for err in payload["errors"]:
        lines.append(f"  ✗ I/O error: {err}")

    if not payload["moved"] and not claimed and not payload["errors"]:
        lines.append("  Inbox clean — nothing to quarantine.")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Move stale .dimension_inbox job files into a dated "
        "quarantine folder.",
    )
    parser.add_argument("--repo", default=str(REPO_ROOT), help="Dimension repo root")
    parser.add_argument(
        "--days",
        type=float,
        default=7.0,
        help="Quarantine jobs older than N days (default: 7)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would move without touching anything",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON")
    args = parser.parse_args(argv)

    payload = quarantine_stale_jobs(
        Path(args.repo).resolve(),
        days=args.days,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render_human(payload))

    return 1 if payload["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
