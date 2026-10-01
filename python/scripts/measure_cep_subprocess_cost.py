# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/scripts/measure_cep_subprocess_cost.py
#408 -- real cold-invocation cost of the frozen dimension_engine binary,
compared against the dev interpreter, for every subcommand
cep/js/backend_bridge.js actually spawns on a CEP button click.

Zero-risk measurement only. No architecture change. See issue #408 for
the full context: a prior session measured ~300ms of interpreter/import
overhead against the UNFROZEN dev interpreter; this closes the gap by
measuring the PyInstaller-frozen binary that actually ships.

USAGE
-----
    python3 python/scripts/measure_cep_subprocess_cost.py [--runs N]

Requires the frozen binary to already exist at cep/bin/mac/dimension_engine
(built via build_engine.py). If it's missing, the frozen-side columns are
reported as "N/A" rather than failing the whole script -- the dev-venv
numbers are still useful on their own.
"""

from __future__ import annotations

import argparse
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FROZEN_BINARY = REPO_ROOT / "cep" / "bin" / "mac" / "dimension_engine"
DEV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python3"
CLI_PY = REPO_ROOT / "python" / "cli.py"
LUT_FIXTURE = REPO_ROOT / "python" / "tests" / "fixtures" / "luts" / "flame_2026_12bit.3dl"

# Exactly the subcommands cep/js/backend_bridge.js spawns on a UI click
# (BB.loadEngineData / runPrefsCli / runProfileCli / runUnitsCli /
# validateLut). Each entry: (label, argv-after-the-interpreter-or-binary).
# `units` with no manifest still costs the same interpreter/import
# overhead we're measuring here even though it exits with an error --
# see the note in the report below.
SUBCOMMANDS: List[tuple] = [
    ("dump-data", ["dump-data"]),
    ("prefs get", ["prefs", "get"]),
    ("profile list", ["profile", "list"]),
    ("units (no manifest)", ["units"]),
    ("lut validate", ["lut", "validate", str(LUT_FIXTURE)]),
]


def _time_invocation(argv: List[str]) -> Optional[float]:
    """Wall-clock ms for one cold invocation. None on a launch failure
    (binary/interpreter missing) -- a non-zero *exit code* is NOT a
    launch failure and is still timed (see the `units` note above)."""
    t0 = time.perf_counter()
    try:
        subprocess.run(argv, capture_output=True, timeout=30)
    except (FileNotFoundError, OSError):
        return None
    except subprocess.TimeoutExpired:
        return None
    return (time.perf_counter() - t0) * 1000.0


def _measure(argv_builder, runs: int) -> Optional[dict]:
    samples = []
    for _ in range(runs):
        argv = argv_builder()
        ms = _time_invocation(argv)
        if ms is None:
            return None
        samples.append(ms)
    return {
        "mean": statistics.mean(samples),
        "median": statistics.median(samples),
        "stddev": statistics.stdev(samples) if len(samples) > 1 else 0.0,
        "min": min(samples),
        "max": max(samples),
        "samples": samples,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="measure_cep_subprocess_cost")
    ap.add_argument("--runs", type=int, default=5,
                     help="cold invocations per subcommand per interpreter (default: 5)")
    args = ap.parse_args(argv)

    frozen_available = FROZEN_BINARY.exists()
    dev_available = DEV_PYTHON.exists() and CLI_PY.exists()

    print("Dimension CEP cold-subprocess cost measurement (#408)")
    print("=" * 68)
    print(f"  frozen binary : {FROZEN_BINARY} "
          f"({'found' if frozen_available else 'NOT FOUND -- skipping'})")
    print(f"  dev venv      : {DEV_PYTHON} "
          f"({'found' if dev_available else 'NOT FOUND -- skipping'})")
    print(f"  runs/subcommand/interpreter: {args.runs}")
    print()

    if not frozen_available and not dev_available:
        print("Neither interpreter is available -- nothing to measure.", file=sys.stderr)
        return 2

    header = f"{'subcommand':<22} {'dev mean':>10} {'frozen mean':>12} {'delta':>10}"
    print(header)
    print("-" * len(header))

    results = []
    for label, sub_argv in SUBCOMMANDS:
        dev_stats = None
        if dev_available:
            dev_stats = _measure(
                lambda sa=sub_argv: [str(DEV_PYTHON), str(CLI_PY)] + sa, args.runs
            )
        frozen_stats = None
        if frozen_available:
            frozen_stats = _measure(
                lambda sa=sub_argv: [str(FROZEN_BINARY)] + sa, args.runs
            )

        dev_str = f"{dev_stats['mean']:.1f}ms" if dev_stats else "N/A"
        frozen_str = f"{frozen_stats['mean']:.1f}ms" if frozen_stats else "N/A"
        delta_str = "N/A"
        if dev_stats and frozen_stats:
            delta = frozen_stats["mean"] - dev_stats["mean"]
            sign = "+" if delta >= 0 else ""
            delta_str = f"{sign}{delta:.1f}ms"

        print(f"{label:<22} {dev_str:>10} {frozen_str:>12} {delta_str:>10}")
        results.append({
            "subcommand": label,
            "argv": sub_argv,
            "dev": dev_stats,
            "frozen": frozen_stats,
        })

    print()
    print("Full per-run samples and stddev omitted from the table above --")
    print("re-run with a debugger or add --verbose if per-sample detail is")
    print("ever needed; the table's mean/delta is the number that matters")
    print("for the UI-perceived-latency question issue #408 asks.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
