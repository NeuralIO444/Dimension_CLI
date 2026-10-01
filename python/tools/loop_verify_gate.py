#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/loop_verify_gate.py
Phase 8 of the autonomous-engineering initiative -- the full verification
gate from docs/autonomous_loop.md. Extends auto_pr.py's existing
run_pre_push_review() (ES3 syntax, reachability, 86-pillar, JSX bundle
sync, CEP Node tests, full pytest -- which already exercises Phases 2-5's
pytest-wrapped machinery: Hall of Horrors, the full-catalog sweep,
pr_diff_harness's --all-dimensions smoke test, and the fuzzer's built-in
500-iteration regression check) with three things that pytest wrapper does
NOT already cover:

  1. `ruff check .` -- run_pre_push_review()'s 6 steps do NOT include this,
     even though it's a separate CI job (`lint`). Added 2026-09-04 after
     three loop-produced PRs (#431, #432, #434) each merged with a trivial
     lint error this gate never looked for, turning main's `lint` CI job
     red while this gate had reported PASSED on every one of them.
  2. pr_diff_harness.py in its actual diff-aware mode, against the real
     base ref -- not --all-dimensions (already covered by
     test_pr_diff_harness.py), but "what did THIS SPECIFIC CHANGE touch."
  3. fuzz_conform.py with a real campaign-sized iteration budget, not the
     pytest wrapper's fixed 500 -- a merge decision deserves more
     exploration than a fast regression check does.

Since GitHub branch protection is unavailable on this repo's plan
(confirmed via a 403 from the branches/main/protection API -- see
docs/autonomous_loop.md), this gate is the ONLY thing standing between a
bad change and a live merge. No shortcuts, no partial credit.

Usage:
  python3 python/tools/loop_verify_gate.py
  python3 python/tools/loop_verify_gate.py --base main --fuzz-iterations 20000
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "python" / "tools") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "python" / "tools"))

from auto_pr import run_pre_push_review  # noqa: E402


def _venv_python() -> str:
    venv_python = str(_REPO_ROOT / ".venv" / "bin" / "python")
    return venv_python if Path(venv_python).exists() else sys.executable


def run_lint_check() -> tuple[bool, list[str]]:
    # `run_pre_push_review()` (auto_pr.py, 6 steps: ES3 audit, reachability,
    # pillars, JSX bundle sync, CEP Node tests, pytest) does NOT run ruff --
    # confirmed absent from that function. CI's separate `lint` job does,
    # which meant a loop-produced PR could pass this gate, auto-merge, and
    # still turn `main`'s `lint` CI job red -- exactly what happened on
    # 2026-09-04: three merged loop PRs (#431, #432, #434) each carried a
    # trivial unused-import/f-string error that this gate never looked for.
    # This is the one CI check this gate was missing; every other CI job
    # (python-verify, fixture-sync, ae-live) has an equivalent already
    # covered above.
    print(" • [7/9] Running ruff lint check...")
    res = subprocess.run(
        [_venv_python(), "-m", "ruff", "check", "."],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    if res.returncode != 0:
        print("   \033[91m✗ ruff found lint errors.\033[0m")
        return False, [f"ruff: {res.stdout.strip()[-2000:]}"]
    print("   \033[92m✓ ruff clean.\033[0m")
    return True, []


def run_diff_aware_harness(base: str) -> tuple[bool, list[str]]:
    print(" • [8/9] Running PR-diff-aware degenerate-geometry harness...")
    res = subprocess.run(
        [_venv_python(), "python/tools/pr_diff_harness.py", "--base", base],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    if res.returncode != 0:
        print("   \033[91m✗ pr_diff_harness.py found invariant violations.\033[0m")
        return False, [f"pr_diff_harness: {res.stdout.strip()[-2000:]}"]
    print("   \033[92m✓ pr_diff_harness.py clean.\033[0m")
    return True, []


def run_fuzz_campaign(iterations: int) -> tuple[bool, list[str]]:
    print(f" • [9/9] Running fuzz campaign ({iterations} iterations)...")
    res = subprocess.run(
        [_venv_python(), "python/tools/fuzz_conform.py", "--iterations", str(iterations)],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    if res.returncode != 0:
        print("   \033[91m✗ fuzz_conform.py found invariant violations.\033[0m")
        return False, [f"fuzz_conform ({iterations} iterations): {res.stdout.strip()[-2000:]}"]
    print(f"   \033[92m✓ fuzz_conform.py clean across {iterations} iterations.\033[0m")
    return True, []


def run_loop_verification_gate(base: str = "origin/main", fuzz_iterations: int = 5000) -> tuple[bool, list[str]]:
    passed, issues = run_pre_push_review()

    lint_ok, lint_issues = run_lint_check()
    passed = passed and lint_ok
    issues.extend(lint_issues)

    diff_ok, diff_issues = run_diff_aware_harness(base)
    passed = passed and diff_ok
    issues.extend(diff_issues)

    fuzz_ok, fuzz_issues = run_fuzz_campaign(fuzz_iterations)
    passed = passed and fuzz_ok
    issues.extend(fuzz_issues)

    return passed, issues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/main", help="Base ref for the diff-aware harness")
    parser.add_argument("--fuzz-iterations", type=int, default=5000, help="Fuzz campaign size (default: 5000)")
    args = parser.parse_args()

    passed, issues = run_loop_verification_gate(base=args.base, fuzz_iterations=args.fuzz_iterations)

    if passed:
        print("\n\033[1m\033[92m✅ LOOP VERIFICATION GATE PASSED — safe to proceed.\033[0m\n")
        sys.exit(0)

    print(f"\n\033[1m\033[91m❌ LOOP VERIFICATION GATE FAILED — {len(issues)} issue(s):\033[0m")
    for issue in issues:
        print(f"  - {issue}")
    sys.exit(1)


if __name__ == "__main__":
    main()
