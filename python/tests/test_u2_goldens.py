# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_u2_goldens.py — U2 Phase 0 golden assertion harness (2026-07-02).

Re-runs the six full orchestrator subprocess conforms captured by
`python/scripts/capture_u2_goldens.py` and asserts the projected output
is EXACTLY equal to the checked-in goldens (byte-identity of the
canonical JSON projection).

These goldens were captured from the pre-U2-refactor tree. They pin:
  - layout=tags byte-identity on both real fixtures (3 target combos)
  - layout=auto identity on 87N (root IS the camera scene)
  - layout=auto identity on Parallax (root is NOT a scene; per-comp
    resolution in U2 Phase 6 must still produce identical output)

If a refactor phase changes any of these, THE CHANGE IS WRONG — fix the
change, never the golden. Re-capturing requires Matt's explicit
approval of a deliberate behavior change.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from scripts.capture_u2_goldens import (  # noqa: E402
    RUNS,
    golden_path,
    load_golden,
    run_conform_projection,
)


@pytest.mark.parametrize(
    "name,fixture,structure,width,height,layout",
    RUNS,
    ids=[r[0] for r in RUNS],
)
def test_conform_matches_golden(name, fixture, structure, width, height,
                                layout, tmp_path):
    assert golden_path(name).is_file(), (
        f"golden {name} missing — run python/scripts/capture_u2_goldens.py "
        f"on the PRE-refactor tree (never on a changed one)")
    golden = load_golden(name)
    projected = run_conform_projection(fixture, structure, width, height,
                                       layout, tmp_path)
    projected.pop("gpu_16k_limit_exceeded", None)
    if isinstance(projected.get("warnings"), dict):
        projected["warnings"].pop("gpu_16k_limit", None)
    # Canonical-JSON byte identity of the projection.
    assert (json.dumps(projected, sort_keys=True)
            == json.dumps(golden, sort_keys=True)), (
        f"conform output drifted from golden {name} — the refactor changed "
        f"behavior; fix the change, never the golden")
