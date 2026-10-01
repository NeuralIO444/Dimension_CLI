# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_verify_math_and_pillars_tool.py
Tests that the DiagnosticHarness in python/tools/verify_math_and_pillars.py
runs every check it defines and passes 100%.
"""

from __future__ import annotations

import inspect
import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.verify_math_and_pillars import DiagnosticHarness


def test_diagnostic_harness_full_run():
    """Discovers and runs every check_* method on the harness, rather than
    calling a hardcoded list of names against a hardcoded results count.

    This test used to call exactly 10 named methods and assert
    len(results) == 50. The harness grew to 16 check_* methods (~86
    checks) across the same two-day span of work this test file shipped
    in, and the test kept passing the whole time without ever exercising
    the other 6 pillars (Studio Deck, OOH/Core Hardening, FastTag/Palette,
    Natural Comment Parser, Dual-Zone/Transform Normalization, Synthetic
    Stress) -- a hardcoded method list and a magic-number count can't
    catch that kind of drift; discovering the methods can't fall behind
    the class that defines them.
    """
    harness = DiagnosticHarness()

    check_methods = [
        name for name, _ in inspect.getmembers(harness, predicate=inspect.ismethod)
        if name.startswith("check_")
    ]
    # A sanity floor, not an exact count -- catches a check_* method
    # disappearing or getting renamed without needing to be bumped every
    # time a new pillar is legitimately added.
    assert len(check_methods) >= 16, (
        f"expected at least the 16 known pillar/subsystem checks, "
        f"found {len(check_methods)}: {sorted(check_methods)}"
    )

    for name in check_methods:
        getattr(harness, name)()

    assert len(harness.results) > 0
    failed = [r for r in harness.results if not r["passed"]]
    assert failed == [], failed
    assert harness.summary() is True
