#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/loop_pick_issue.py
Phase 8 of the autonomous-engineering initiative -- picks the next issue for
the governed autonomous loop (docs/autonomous_loop.md) to work on.

Deliberately narrow: only issues carrying the `ready-for-loop` label are
eligible -- opt-in, never "anything open." Oldest first (FIFO), so an issue
doesn't sit forever just because newer ones keep landing.

Usage:
  python3 python/tools/loop_pick_issue.py            # human-readable
  python3 python/tools/loop_pick_issue.py --json      # machine-readable, for the loop's own prompt to parse

Exit code 0 with an issue chosen, exit code 1 if none are eligible (a valid,
expected outcome -- the loop should just stop for this tick, not treat it
as an error).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOOP_LABEL = "ready-for-loop"


def pick_next_issue() -> dict | None:
    res = subprocess.run(
        [
            "gh", "issue", "list",
            "--label", _LOOP_LABEL,
            "--state", "open",
            "--json", "number,title,body,labels,createdAt,url",
            "--limit", "50",
        ],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    if res.returncode != 0:
        print(f"gh issue list failed: {res.stderr}", file=sys.stderr)
        return None

    issues = json.loads(res.stdout)
    if not issues:
        return None

    issues.sort(key=lambda i: (_difficulty_rank(i), i["createdAt"]))
    return issues[0]


# Difficulty ordering: easiest first, FIFO within a tier.
#
# Changed from pure FIFO on 2026-09-04 after a live tick picked the oldest
# eligible issue (#350, `difficulty:hard`), routed it to Opus per the model
# ladder, spent $1.65, and correctly concluded the work was blocked on a
# human -- while five `difficulty:easy` issues sat untouched behind it.
#
# That is the expensive failure mode of FIFO here: the loop's cost per tick
# is set by the difficulty label (easy -> Haiku, hard -> Opus), and hard
# issues are also the ones most likely to be blocked on a human decision or
# on real After Effects verification the loop cannot perform. FIFO therefore
# spends the most money on the work least likely to finish.
#
# Draining easy work first also keeps the lifetime spend cap meaningful:
# cheap tickets clear the queue, and the expensive ones are reached only
# once nothing tractable remains -- at which point a human is probably the
# right next step anyway.
#
# Unlabelled issues sort as "medium", matching run_loop.sh's model ladder,
# which defaults to Sonnet when no difficulty label is present.
_DIFFICULTY_ORDER = {"easy": 0, "medium": 1, "hard": 2}


def _difficulty_rank(issue: dict) -> int:
    for label in issue.get("labels") or []:
        name = label.get("name", "")
        if name.startswith("difficulty:"):
            return _DIFFICULTY_ORDER.get(name.split(":", 1)[1].strip(), 1)
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    args = parser.parse_args()

    issue = pick_next_issue()

    if issue is None:
        if args.json:
            print(json.dumps({"status": "NONE_ELIGIBLE"}))
        else:
            print(f"No open issues labeled '{_LOOP_LABEL}'. Nothing to do this tick.")
        sys.exit(1)

    if args.json:
        print(json.dumps({"status": "OK", "issue": issue}))
    else:
        print(f"#{issue['number']}: {issue['title']}")
        print(f"  {issue['url']}")
        print(f"  Labels: {', '.join(l['name'] for l in issue['labels'])}")
        print(f"\n{issue['body']}")


if __name__ == "__main__":
    main()
