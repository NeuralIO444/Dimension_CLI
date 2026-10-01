# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_pr_diff_harness.py
Phase 4 of the autonomous-engineering initiative -- keeps the dynamic
generator + invariant-check machinery itself regression-tested, since
pr_diff_harness.py's actual value (diff-aware dimension selection) only
fires inside a real PR/CI context. This runs every registered dimension
unconditionally (`--all-dimensions`) so a broken generator or a genuine
regression the generators would catch on a real diff gets caught here too.
"""

from __future__ import annotations

import sys

from core.conform_invariant_checks import check_all
from core.degenerate_geometry_generators import GENERATORS
from core.scale_engine import ScaleEngine
from tools.pr_diff_harness import _DIMENSION_CONFORM_PARAMS, _BLEED_SWEEP


def test_all_generator_dimensions_produce_valid_manifests():
    """Every generator must yield at least one (label, manifest) pair with
    at least one layer -- catches a generator that silently yields nothing."""
    for name, gen_fn in GENERATORS.items():
        cases = list(gen_fn())
        assert cases, f"generator '{name}' produced zero cases"
        for label, manifest in cases:
            assert manifest.layers, f"{name}/{label}: manifest has no layers"


def test_all_dimensions_pass_universal_invariants():
    """The actual Phase 4 regression guard: every generated case, across
    every registered dimension, must conform cleanly (finite output, no
    dropped layers, sealed-unit atomicity). A failure here means either a
    real regression or a generator producing a genuinely invalid manifest --
    both worth knowing about immediately, not just when a real diff
    happens to touch the right file."""
    violations = []
    for name, gen_fn in GENERATORS.items():
        if name == "bleed_extremes":
            for label, manifest in gen_fn():
                for bleed in _BLEED_SWEEP:
                    engine = ScaleEngine(manifest, 1080, 1920, scale_mode="Fill", bleed_pct=bleed, layout="tags")
                    result = engine.conform()
                    report = check_all(manifest, engine, result, f"{label}_bleed{bleed}")
                    violations.extend(str(v) for v in report.violations)
            continue

        params = _DIMENSION_CONFORM_PARAMS.get(name)
        assert params is not None, f"dimension '{name}' has no entry in _DIMENSION_CONFORM_PARAMS"
        for label, manifest in gen_fn():
            engine = ScaleEngine(
                manifest,
                params["target_width"], params["target_height"],
                scale_mode=params["scale_mode"], bleed_pct=params["bleed_pct"], layout=params["layout"],
            )
            result = engine.conform()
            report = check_all(manifest, engine, result, label)
            violations.extend(str(v) for v in report.violations)

    assert not violations, "invariant violations:\n" + "\n".join(violations)


def test_harness_cli_all_dimensions_exits_clean(capsys):
    """Smoke test the actual CLI entry point (not just the underlying
    functions), so a refactor that breaks main()'s wiring is caught."""
    from tools import pr_diff_harness

    old_argv = sys.argv
    sys.argv = ["pr_diff_harness.py", "--all-dimensions"]
    try:
        pr_diff_harness.main()
    except SystemExit as e:
        assert e.code in (None, 0), f"harness exited non-zero: {e.code}"
    finally:
        sys.argv = old_argv

    out = capsys.readouterr().out
    assert "All generated cases clean." in out
