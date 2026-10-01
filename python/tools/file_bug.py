#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/file_bug.py
Thin `gh issue create` wrapper enforcing Dimension's severity/subsystem label
convention, so a discovery gets filed the same way whether a human runs this
interactively or a future automated process calls it non-interactively.

BUGS.md is a historical archive as of 2026-09-02 — new defects go here.

Usage (interactive — prompts for anything not passed):
  python3 python/tools/file_bug.py

Usage (non-interactive — every required flag supplied, no prompts):
  python3 python/tools/file_bug.py \\
      --title "Short bug title" \\
      --severity P2-major \\
      --subsystem soe \\
      --body-file /tmp/body.md

Body can also be piped via stdin (skip --body-file, pipe into stdin, and the
process is non-interactive as soon as title/severity/subsystem are supplied).

Severity ladder (Dimension's existing labels, unchanged here):
  P0-blocker   Blocks build, tests, or release
  P1-critical  Output corruption, crash, or data loss
  P2-major     Major functional failure or process hang
  P3-moderate  Suboptimal behavior or precision defect

Subsystem is any existing label (jsx, soe, duplication, post-conform, assets,
tagging, variants, ...) or a new one — this tool will offer to create a new
label via `gh label create` rather than silently filing unlabeled.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_SEVERITIES = ("P0-blocker", "P1-critical", "P2-major", "P3-moderate")

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(_REPO_ROOT), text=True, **kwargs)


def _existing_labels() -> list[str]:
    res = _run(["gh", "label", "list", "--limit", "100", "--json", "name"], capture_output=True)
    if res.returncode != 0:
        return []
    import json
    try:
        return [row["name"] for row in json.loads(res.stdout)]
    except (ValueError, KeyError):
        return []


def _prompt(question: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer or (default or "")


def _resolve_severity(value: str | None, interactive: bool) -> str:
    if value and value in _SEVERITIES:
        return value
    if value:
        print(f"Unknown severity '{value}'. Choose one of: {', '.join(_SEVERITIES)}", file=sys.stderr)
    if not interactive:
        raise SystemExit(f"--severity is required (one of: {', '.join(_SEVERITIES)})")
    for i, sev in enumerate(_SEVERITIES, 1):
        print(f"  {i}. {sev}")
    choice = _prompt("Severity", default="3")
    try:
        return _SEVERITIES[int(choice) - 1]
    except (ValueError, IndexError):
        raise SystemExit(f"Invalid severity choice: {choice!r}")


def _resolve_subsystem(value: str | None, interactive: bool) -> str:
    labels = _existing_labels()
    subsystem_labels = [
        l for l in labels
        if l not in _SEVERITIES and l not in ("bug", "enhancement", "documentation",
                                                "duplicate", "good first issue",
                                                "help wanted", "invalid", "question",
                                                "wontfix", "pre-beta", "task", "performance")
    ]
    if value:
        if value not in subsystem_labels:
            if not interactive:
                print(f"Note: '{value}' isn't an existing label — will create it.", file=sys.stderr)
            elif _prompt(f"'{value}' isn't an existing label. Create it? (y/n)", default="y").lower().startswith("y"):
                _run(["gh", "label", "create", value, "--description", f"{value} subsystem", "--color", "ededed"])
            else:
                raise SystemExit("Aborted — no subsystem label chosen.")
        return value
    if not interactive:
        raise SystemExit("--subsystem is required in non-interactive mode")
    print("Existing subsystem labels:", ", ".join(sorted(subsystem_labels)) or "(none)")
    return _prompt("Subsystem label (existing name, or a new one to create)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", help="Issue title")
    parser.add_argument("--severity", choices=_SEVERITIES, help="Severity label")
    parser.add_argument("--subsystem", help="Subsystem label (existing or new)")
    parser.add_argument("--body-file", help="Path to a markdown file for the issue body")
    parser.add_argument("--dry-run", action="store_true", help="Print the gh command instead of running it")
    args = parser.parse_args()

    interactive = sys.stdin.isatty() and not (args.title and args.severity and args.subsystem)

    title = args.title or (_prompt("Title") if interactive else None)
    if not title:
        raise SystemExit("--title is required in non-interactive mode")

    severity = _resolve_severity(args.severity, interactive)
    subsystem = _resolve_subsystem(args.subsystem, interactive)

    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        body = sys.stdin.read()
    elif interactive:
        print("Body (end with a line containing only 'EOF'):")
        lines = []
        while True:
            line = input()
            if line.strip() == "EOF":
                break
            lines.append(line)
        body = "\n".join(lines)
    else:
        raise SystemExit("--body-file or piped stdin is required in non-interactive mode")

    labels = f"bug,{severity},{subsystem}"
    cmd = ["gh", "issue", "create", "--title", title, "--label", labels, "--body", body]

    if args.dry_run:
        print("Would run:", " ".join(cmd[:-2]), f"--body <{len(body)} chars>")
        return

    res = _run(cmd)
    raise SystemExit(res.returncode)


if __name__ == "__main__":
    main()
