#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/pr_diff_harness.py
Phase 4 of the autonomous-engineering initiative -- the PR-diff-aware
dynamic harness. Diffs the current branch against a base ref, maps touched
files to relevant degenerate-geometry dimensions (python/core/
degenerate_geometry_generators.py), runs every generated case through a
real ScaleEngine.conform(), and asserts the universal invariant battery
(python/core/conform_invariant_checks.py) -- never an invented expected
value for the made-up geometry, only "did the math stay sane."

A failure here is real, actionable signal, not a false positive from a
wrong assumption about what the output "should" be -- the invariants
checked (finiteness, layer-count preservation, sealed-unit atomicity) are
true for ANY input geometry, by construction of the math itself.

Usage:
  python3 python/tools/pr_diff_harness.py                  # diff vs origin/main
  python3 python/tools/pr_diff_harness.py --base main
  python3 python/tools/pr_diff_harness.py --files a.py b.py  # skip git diff
  python3 python/tools/pr_diff_harness.py --all-dimensions  # ignore the diff, run everything

Exit code is non-zero on any invariant violation -- usable as a CI/pre-push
gate (see auto_pr.py's run_pre_push_review for the existing gate pattern
this is meant to extend).
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Set

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "python") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "python"))

from core.conform_invariant_checks import check_all  # noqa: E402
from core.degenerate_geometry_generators import GENERATORS  # noqa: E402
from core.scale_engine import ScaleEngine  # noqa: E402

# File-glob -> dimension-name mapping. Extend this as new conform-adjacent
# modules gain their own generator dimension (see degenerate_geometry_
# generators.py's GENERATORS registry for what's available today).
_FILE_DIMENSION_MAP: Dict[str, List[str]] = {
    "python/core/scale_engine*.py": ["scale_extremes", "camera_scenes", "multi_member_camera_scenes", "bleed_extremes"],
    "python/core/matrix_math.py": ["camera_scenes", "multi_member_camera_scenes"],
    "python/core/placement_units.py": ["camera_scenes", "multi_member_camera_scenes", "sealed_precomp_nesting"],
    "python/core/classify.py": ["camera_scenes", "multi_member_camera_scenes"],
    "python/core/variant_gate.py": ["sealed_precomp_nesting"],
    "python/core/gravity.py": ["scale_extremes"],
}

# Per-dimension conform parameters. "camera_scenes"/"multi_member_camera_scenes"
# deliberately match #365's real repro conditions (Fill mode, 5% bleed,
# narrow 1080x1920 target, layout=auto) -- the exact combination that hid
# the Z-depth bug until a real production log surfaced it.
_DIMENSION_CONFORM_PARAMS: Dict[str, dict] = {
    "scale_extremes": dict(target_width=3840, target_height=2160, scale_mode="Fit", bleed_pct=0.0, layout="tags"),
    "camera_scenes": dict(target_width=1080, target_height=1920, scale_mode="Fill", bleed_pct=0.05, layout="auto"),
    "multi_member_camera_scenes": dict(target_width=1080, target_height=1920, scale_mode="Fill", bleed_pct=0.05, layout="auto"),
    "sealed_precomp_nesting": dict(target_width=1080, target_height=1920, scale_mode="Auto", bleed_pct=0.0, layout="auto"),
}
_BLEED_SWEEP = (0.0, 0.05, 0.25)


def _git_diff_files(base: str) -> List[str]:
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"git diff failed: {e.stderr}", file=sys.stderr)
        return []
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def _relevant_dimensions(files: List[str]) -> Set[str]:
    dims: Set[str] = set()
    for f in files:
        for pattern, dim_names in _FILE_DIMENSION_MAP.items():
            if fnmatch.fnmatch(f, pattern):
                dims.update(dim_names)
    return dims


def _run_dimension(dim_name: str) -> List[str]:
    """Runs every generated case for one dimension, returns a list of
    human-readable failure strings (empty = all clean)."""
    failures: List[str] = []
    gen_fn = GENERATORS[dim_name]

    if dim_name == "bleed_extremes":
        for label, manifest in gen_fn():
            for bleed in _BLEED_SWEEP:
                full_label = f"{label}_bleed{bleed}"
                engine = ScaleEngine(manifest, 1080, 1920, scale_mode="Fill", bleed_pct=bleed, layout="tags")
                result = engine.conform()
                report = check_all(manifest, engine, result, full_label)
                failures.extend(str(v) for v in report.violations)
        return failures

    params = _DIMENSION_CONFORM_PARAMS.get(dim_name)
    if params is None:
        return [f"[{dim_name}] no conform params registered -- add an entry to _DIMENSION_CONFORM_PARAMS"]

    for label, manifest in gen_fn():
        engine = ScaleEngine(
            manifest,
            params["target_width"], params["target_height"],
            scale_mode=params["scale_mode"], bleed_pct=params["bleed_pct"], layout=params["layout"],
        )
        result = engine.conform()
        report = check_all(manifest, engine, result, label)
        failures.extend(str(v) for v in report.violations)

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/main", help="Base ref to diff against (default: origin/main)")
    parser.add_argument("--files", nargs="*", help="Skip git diff, use this explicit file list")
    parser.add_argument("--all-dimensions", action="store_true", help="Ignore the diff, run every registered dimension")
    args = parser.parse_args()

    if args.all_dimensions:
        dims = set(GENERATORS.keys())
        touched = ["(--all-dimensions)"]
    else:
        touched = args.files if args.files is not None else _git_diff_files(args.base)
        dims = _relevant_dimensions(touched)

    print(f"Touched files: {len(touched)}")
    if dims:
        print(f"Relevant dimensions: {sorted(dims)}")
    else:
        print("No conform-adjacent files touched -- nothing to sweep.")
        return

    total_failures: List[str] = []
    for dim_name in sorted(dims):
        failures = _run_dimension(dim_name)
        status = "FAIL" if failures else "ok"
        print(f"  [{status}] {dim_name} -- {len(failures)} violation(s)")
        total_failures.extend(failures)

    if total_failures:
        print(f"\n{len(total_failures)} invariant violation(s) found:")
        for f in total_failures:
            print(f"  {f}")
        print(
            "\nEach of these is a real, reproducible finding -- the checks "
            "asserted are universal truths (finite output, no dropped "
            "layers, sealed-unit atomicity), not invented expectations. "
            "Promote a confirmed one into a permanent test in "
            "test_hall_of_horrors_math.py per its growing-suite convention, "
            "and file it via python/tools/file_bug.py."
        )
        sys.exit(1)

    print("\nAll generated cases clean.")


if __name__ == "__main__":
    main()
