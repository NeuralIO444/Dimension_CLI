#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/loop_path_guard.py
Phase 8 of the autonomous-engineering initiative -- the mechanical Phase
8/9 boundary from docs/autonomous_loop.md. Diffs a branch against a base
ref and refuses (exit 1) if the diff touches any conform-pipeline file,
per CLAUDE.md's "Pipeline contracts" section (Sovereign_Core.jsx = scrape,
surveyor.py = tag, scale_engine* + gravity.py + matrix_math.py +
placement_units.py + variant_gate.py + occlusion_engine.py = conform/SOE,
exporter.py = export, Babysitter.jsx = inject, Auditor.jsx = audit,
report_generator.py = report).

This is Phase 8's actual restriction, enforced in code, not by asking the
loop nicely -- until Phase 9 is explicitly authorized (see
docs/autonomous_loop.md's phase gate), any diff this flags must fall back
to a draft PR for a human, never an autonomous merge.

Usage:
  python3 python/tools/loop_path_guard.py                 # diff vs origin/main
  python3 python/tools/loop_path_guard.py --base main
  python3 python/tools/loop_path_guard.py --files a.py b.py

Exit 0 = clean, safe to proceed autonomously (Phase 8 scope).
Exit 1 = pipeline files touched, must fall back to human review.
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path
from typing import List

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Matches CLAUDE.md's "Pipeline contracts" section file-for-file. Extend
# this ONLY by also extending that section -- the two must stay in sync,
# same discipline as any other single-source-of-truth list in this repo.
_PIPELINE_GLOBS = (
    "Scripts/Dimension_Assets/Sovereign_Core.jsx",
    "Scripts/Dimension_Assets/SovCore_*.jsx",
    "python/core/surveyor.py",
    "python/core/scale_engine*.py",
    "python/core/gravity.py",
    "python/core/matrix_math.py",
    "python/core/placement_units.py",
    "python/core/variant_gate.py",
    "python/core/occlusion_engine.py",
    "python/logic/exporter.py",
    "Scripts/Dimension_Assets/Babysitter.jsx",
    "Scripts/Dimension_Assets/Babysitter_src/*.jsx",
    "Scripts/Dimension_Assets/Auditor.jsx",
    "python/logic/report_generator.py",
)


def _git_diff_files(base: str) -> List[str]:
    res = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    if res.returncode != 0:
        print(f"git diff failed: {res.stderr}", file=sys.stderr)
        return []
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def touched_pipeline_files(files: List[str]) -> List[str]:
    hits = []
    for f in files:
        for pattern in _PIPELINE_GLOBS:
            if fnmatch.fnmatch(f, pattern):
                hits.append(f)
                break
    return hits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/main", help="Base ref to diff against (default: origin/main)")
    parser.add_argument("--files", nargs="*", help="Skip git diff, use this explicit file list")
    args = parser.parse_args()

    files = args.files if args.files is not None else _git_diff_files(args.base)
    hits = touched_pipeline_files(files)

    if hits:
        print("PIPELINE FILES TOUCHED -- must fall back to a draft PR for human review:")
        for f in hits:
            print(f"  {f}")
        print(
            "\nPer docs/autonomous_loop.md's Phase 8/9 boundary: this diff cannot "
            "be autonomously merged until Phase 9 is explicitly authorized."
        )
        sys.exit(1)

    print(f"Clean -- {len(files)} file(s) touched, none in the pipeline set.")


if __name__ == "__main__":
    main()
