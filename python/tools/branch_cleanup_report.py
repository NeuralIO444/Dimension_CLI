#!/usr/bin/env python3
"""
Branch hygiene report: categorize unmerged local branches by GitHub PR state.

Usage: python3 branch_cleanup_report.py

Output:
  - Safe to delete (PR merged)
  - Safe to delete (PR closed, not merged)
  - No PR found (unknown status)
  - Prunable worktrees
"""

import subprocess
import json
from typing import Dict, List, Tuple

def run_cmd(cmd: List[str]) -> str:
    """Run a shell command and return stdout, stripped."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        # Some gh commands may fail gracefully (no results)
        return ""

def get_unmerged_branches() -> List[str]:
    """Get all local branches not yet merged into main."""
    output = run_cmd(["git", "branch", "--no-merged", "main"])
    if not output:
        return []
    return [line.strip().lstrip("* ") for line in output.split("\n") if line.strip()]

def check_pr_status(branch: str) -> Tuple[str, str]:
    """
    Check GitHub PR status for a branch.

    Returns: (status, pr_url)
      - status: "MERGED", "CLOSED", "OPEN", "NOT_FOUND"
      - pr_url: URL if found, empty string otherwise
    """
    # Try to find a PR with this branch as the head
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--head", branch, "--json", "state,url"],
            capture_output=True,
            text=True,
            check=True
        )
        if not result.stdout.strip():
            return "NOT_FOUND", ""

        prs = json.loads(result.stdout)
        if not prs:
            return "NOT_FOUND", ""

        # Take the first (most recent) PR
        pr = prs[0]
        state = pr.get("state", "UNKNOWN")
        url = pr.get("url", "")
        return state, url
    except Exception:
        return "NOT_FOUND", ""

def get_prunable_worktrees() -> List[Dict[str, str]]:
    """Get list of prunable worktrees."""
    output = run_cmd(["git", "worktree", "list", "--porcelain"])
    if not output:
        return []

    prunable = []
    for line in output.split("\n"):
        if line.startswith("prunable "):
            # Format: prunable <path>
            path = line[len("prunable "):].strip()
            prunable.append({"path": path})

    return prunable

def generate_report():
    """Generate and print the branch hygiene report."""
    print("=" * 70)
    print("BRANCH HYGIENE REPORT")
    print("=" * 70)
    print()

    branches = get_unmerged_branches()
    print(f"Total unmerged branches: {len(branches)}")
    print()

    # Categorize
    safe_merged = []
    safe_closed = []
    no_pr = []

    for branch in branches:
        status, url = check_pr_status(branch)

        if status == "MERGED":
            safe_merged.append((branch, url))
        elif status == "CLOSED":
            safe_closed.append((branch, url))
        else:
            no_pr.append(branch)

    # Print categorized results
    print("-" * 70)
    print("SAFE TO DELETE (PR merged into main):")
    print("-" * 70)
    if safe_merged:
        for branch, url in sorted(safe_merged):
            print(f"  {branch}")
            print(f"    PR: {url}")
    else:
        print("  (none)")
    print()

    print("-" * 70)
    print("SAFE TO DELETE (PR closed, not merged):")
    print("-" * 70)
    if safe_closed:
        for branch, url in sorted(safe_closed):
            print(f"  {branch}")
            print(f"    PR: {url}")
    else:
        print("  (none)")
    print()

    print("-" * 70)
    print("NO PR FOUND (unknown status, verify manually):")
    print("-" * 70)
    if no_pr:
        for branch in sorted(no_pr):
            print(f"  {branch}")
    else:
        print("  (none)")
    print()

    # Worktree status
    prunable = get_prunable_worktrees()
    print("-" * 70)
    print("PRUNABLE WORKTREES (metadata-only, safe to prune):")
    print("-" * 70)
    if prunable:
        for wt in prunable:
            print(f"  {wt['path']}")
        print()
        print("  To prune: git worktree prune")
    else:
        print("  (none)")
    print()

    # Summary
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Safe to delete (merged):        {len(safe_merged)}")
    print(f"  Safe to delete (closed):        {len(safe_closed)}")
    print(f"  Unknown status:                 {len(no_pr)}")
    print(f"  Prunable worktrees:             {len(prunable)}")
    print()
    print("NEXT STEPS:")
    print("  1. Review the categorized branches above")
    print("  2. For branches with unknown status, check manually if safe to delete")
    print("  3. Once confirmed, request Matt to approve deletion")
    print("  4. Request Matt to approve 'git worktree prune' if there are prunable entries")
    print()

if __name__ == "__main__":
    generate_report()
