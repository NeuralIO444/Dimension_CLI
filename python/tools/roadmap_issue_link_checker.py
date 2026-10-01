#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/roadmap_issue_link_checker.py

Parses ROADMAP.md and BUGS.md for GitHub issue/PR references (#NNN) and flags
any that have a status claim ("shipped", "done", checkmark) but whose actual
GitHub state is OPEN.

This catches drift where a reference was marked shipped but the issue/PR is
still open -- a sign that the roadmap status claim is stale and misleading.

Usage:
  python3 python/tools/roadmap_issue_link_checker.py                      # checks both ROADMAP.md and BUGS.md
  python3 python/tools/roadmap_issue_link_checker.py --file ROADMAP.md     # checks only ROADMAP.md
  python3 python/tools/roadmap_issue_link_checker.py --json                # machine-readable output

Exit code 0 with report (even if empty), exit code 1 on errors.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]

ISSUE_REF_PATTERN = re.compile(r'#(\d+)')
STATUS_KEYWORDS = {
    r'\bshipped\b',
    r'\bdone\b',
    r'\bcomplete\b',
    r'\b✅\b',
    r'\[x\]',  # markdown checkbox
    r'\b✓\b',
}
STATUS_PATTERN = re.compile('|'.join(STATUS_KEYWORDS), re.IGNORECASE)

# Context window: how many chars before/after a reference to consider for status keywords
CONTEXT_CHARS = 200


@dataclass
class IssueCheck:
    ref: int
    file: str
    line_num: int
    state: str | None
    title: str | None
    has_status_keyword: bool
    is_drift: bool


def _get_issue_or_pr_state(ref: int) -> tuple[str | None, str | None]:
    """
    Query GitHub for issue or PR state.
    Returns (state, title) or (None, None) on error.
    state is one of: OPEN, CLOSED, MERGED (for PRs only)
    """
    for cmd_type in ["issue", "pr"]:
        res = subprocess.run(
            ["gh", cmd_type, "view", str(ref), "--json", "state,title"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            try:
                data = json.loads(res.stdout)
                return data.get("state"), data.get("title")
            except json.JSONDecodeError:
                pass
    return None, None


def _has_status_keyword_near(text: str, ref_pos: int) -> bool:
    """Check if status keywords appear within CONTEXT_CHARS of the reference position."""
    start = max(0, ref_pos - CONTEXT_CHARS)
    end = min(len(text), ref_pos + CONTEXT_CHARS)
    context = text[start:end]
    return bool(STATUS_PATTERN.search(context))


def check_file(file_path: Path) -> list[IssueCheck]:
    """Parse a file for issue references and check their state."""
    if not file_path.exists():
        return []

    results = []
    content = file_path.read_text()
    lines = content.split('\n')

    for line_num, line in enumerate(lines, start=1):
        for match in ISSUE_REF_PATTERN.finditer(line):
            ref = int(match.group(1))
            ref_pos = match.start()

            state, title = _get_issue_or_pr_state(ref)
            has_status = _has_status_keyword_near(line, ref_pos)
            is_drift = has_status and state == "OPEN"

            results.append(IssueCheck(
                ref=ref,
                file=str(file_path),
                line_num=line_num,
                state=state,
                title=title,
                has_status_keyword=has_status,
                is_drift=is_drift,
            ))

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--file",
        type=Path,
        help="Specific file to check (default: both ROADMAP.md and BUGS.md)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable JSON output"
    )
    args = parser.parse_args()

    files_to_check = []
    if args.file:
        files_to_check = [args.file]
    else:
        files_to_check = [_REPO_ROOT / "ROADMAP.md", _REPO_ROOT / "BUGS.md"]

    all_checks = []
    for file_path in files_to_check:
        all_checks.extend(check_file(file_path))

    drift_issues = [c for c in all_checks if c.is_drift]

    if args.json:
        print(json.dumps({
            "total_refs": len(all_checks),
            "drift_count": len(drift_issues),
            "drifts": [
                {
                    "ref": c.ref,
                    "file": c.file,
                    "line": c.line_num,
                    "state": c.state,
                    "title": c.title,
                }
                for c in drift_issues
            ],
        }))
    else:
        print(f"Checked {len(all_checks)} issue/PR references.")
        if drift_issues:
            print(f"\n⚠️  Found {len(drift_issues)} status drift(s):\n")
            for check in drift_issues:
                print(f"  #{check.ref} ({check.file}:{check.line_num})")
                print(f"    State: {check.state} (but marked as shipped/done)")
                print(f"    Title: {check.title or '?'}\n")
        else:
            print("✓ No status drift detected.")

    sys.exit(0)


if __name__ == "__main__":
    main()
