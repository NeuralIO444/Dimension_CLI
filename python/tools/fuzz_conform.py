#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/fuzz_conform.py
Phase 5 CLI -- dependency-free property-based fuzzing of ScaleEngine.conform()
(python/core/random_geometry_fuzzer.py). Deliberately NOT in the always-on
PR gate -- true randomness means a clean run today says nothing about
tomorrow's seed, which is a bad property for something blocking merges.
Meant for nightly/on-demand use.

Usage:
  python3 python/tools/fuzz_conform.py                    # 2000 iterations, random master seed
  python3 python/tools/fuzz_conform.py --iterations 10000
  python3 python/tools/fuzz_conform.py --seed 12345        # reproduce a whole campaign's sequence
  python3 python/tools/fuzz_conform.py --no-replay         # skip replaying the corpus first
  python3 python/tools/fuzz_conform.py --replay-seed 84720913   # rebuild and run ONE specific case

`--seed` controls the campaign's master RNG (which per-iteration case seeds
get generated) -- it is NOT the same as an individual failing case's own
seed, which is what a failure report prints. To reproduce ONE specific
failure, use `--replay-seed <that case's seed>`, not `--seed`.

Exit code is non-zero on any failure -- usable as a scheduled CI gate
(see ci.yml comment near the always-on preset_sweep/pr_diff_harness steps
for why THIS tool deliberately isn't wired in next to them).
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "python") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "python"))

from core.random_geometry_fuzzer import generate_case, run_campaign, run_case, shrink  # noqa: E402


def _replay_one(seed: int) -> None:
    case = generate_case(seed)
    report = run_case(case)
    print(f"Replaying seed={seed} shape={case.shape}")
    print(f"  params: {case.params}")
    print(f"  conform_kwargs: {case.conform_kwargs}")
    if report.ok:
        print("  Clean -- no invariant violations.")
        return
    shrunk_case, steps = shrink(case)
    shrunk_report = run_case(shrunk_case)
    print(f"  FAILED ({len(report.violations)} violation(s)), shrunk in {steps} step(s):")
    print(f"  shrunk params: {shrunk_case.params}")
    for v in shrunk_report.violations:
        print(f"    {v}")
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--iterations", type=int, default=2000, help="New random cases to generate (default: 2000)")
    parser.add_argument("--seed", type=int, default=None, help="Campaign master seed (default: random each run)")
    parser.add_argument("--replay-seed", type=int, default=None, help="Rebuild and run exactly one case by its own seed (from a failure report), ignoring --iterations/--seed")
    parser.add_argument("--no-replay", action="store_true", help="Skip replaying the persisted failure corpus")
    args = parser.parse_args()

    if args.replay_seed is not None:
        _replay_one(args.replay_seed)
        return

    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(0, 2**31)
    print(f"Fuzz campaign: {args.iterations} iterations, master seed={seed}")

    result = run_campaign(iterations=args.iterations, seed=seed, replay_corpus=not args.no_replay)

    print(f"Corpus replayed: {result.replayed_corpus_failures} historical case(s)")
    print(f"New iterations run: {result.iterations_run}")

    if result.ok:
        print("Clean -- no invariant violations.")
        return

    print(f"\n{len(result.failures)} failure(s) found:")
    for f in result.failures:
        print(f"\n  seed={f.case.seed} shape={f.case.shape} (shrunk in {f.shrunk_from_iterations} steps)")
        print(f"  reproduce: python3 python/tools/fuzz_conform.py --replay-seed {f.case.seed}")
        print(f"  params: {f.case.params}")
        print(f"  conform_kwargs: {f.case.conform_kwargs}")
        for v in f.report.violations:
            print(f"    {v}")
    print(
        "\nEach failure is now persisted in .dimension/fuzz_corpus/ and will "
        "replay on every future run in THIS working directory. Promote a "
        "confirmed one into a permanent test in test_hall_of_horrors_math.py "
        "and file it via python/tools/file_bug.py."
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
