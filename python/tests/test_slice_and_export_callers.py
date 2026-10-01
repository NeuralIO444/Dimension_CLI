# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_slice_and_export_callers.py — Slot 12.5 Stage D contract test.

Structural assertion: every production call site of
`PayloadSlicer.slice_and_export` MUST forward the Stage D kwargs
(`mirror_tree`, `target_bin_path`, `mirror_rewires`). One caller
wired and the others silently legacy-pathing is exactly the
regression class that produced the 2026-05-17 Corpus_01 → TikTok
inject failure: the Qt orchestrator (`controller.py`) was
calling `slice_and_export` without Stage D kwargs, the on-disk
`chunk_manifest.json` carried no `mirror_tree`, Babysitter took
the legacy single-comp setup path, and the post-inject audit
compared `comp.numLayers == 2` against `manifest.total_layers ==
11`.

This is a static (AST) test — it doesn't run the orchestrator,
it parses the source. The point is that a future change to one
caller can't silently drop the Stage D kwargs without failing
CI.

Production call sites (canonical list — keep in sync with the
exporter's caller surface):
  - python/__main__.py            (CLI)
  - python/ui/main_window.py      (Tkinter UI)
  - python/ui/controller.py       (Qt UI — Matt's path)
  - python/orchestrator.py        (Orchestrator)
  - python/scripts/slot_17_profile_pipeline.py (Offline Pipeline)

If a new caller is added, append it to STAGE_D_CALLERS below.
"""

from __future__ import annotations

import ast
import os
from typing import List, Set

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Files that call `slice_and_export`. The test suite dynamically
# enforces that this list stays current (it statically analyzes
# all .py files in the repo and fails if an unlisted file makes
# the call).
STAGE_D_CALLERS = [
    # Conform stage (orchestrator.py re-exports; implementation in stages/)
    "python/stages/conform.py",
    # The offline test pipeline
    "python/scripts/slot_17_profile_pipeline.py",
]

REQUIRED_KWARGS = {"mirror_tree", "target_bin_path", "mirror_rewires"}


def _find_slice_and_export_calls(source: str) -> List[ast.Call]:
    """Return every Call node in `source` whose function name (or
    attribute name) is `slice_and_export`. Matches both bare
    `slice_and_export(...)` and the more common
    `slicer.slice_and_export(...)`."""
    tree = ast.parse(source)
    matches: List[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "slice_and_export":
            matches.append(node)
        elif isinstance(fn, ast.Name) and fn.id == "slice_and_export":
            matches.append(node)
    return matches


def _kwarg_names(call: ast.Call) -> Set[str]:
    """Return the set of keyword names passed in this Call node.
    Ignores positional args and `**kwargs` splats (treated as
    not-statically-known)."""
    return {kw.arg for kw in call.keywords if kw.arg is not None}


@pytest.mark.parametrize("rel_path", STAGE_D_CALLERS)
def test_caller_forwards_stage_d_kwargs(rel_path: str) -> None:
    """Every caller listed in STAGE_D_CALLERS must pass all three
    Stage D kwargs through to slice_and_export."""
    abs_path = os.path.join(REPO_ROOT, rel_path)
    assert os.path.isfile(abs_path), (
        f"Caller file missing: {rel_path}. If the file was moved "
        f"or removed, update STAGE_D_CALLERS in this test."
    )

    with open(abs_path, "r", encoding="utf-8") as fh:
        source = fh.read()

    calls = _find_slice_and_export_calls(source)
    assert calls, (
        f"{rel_path}: no slice_and_export call found. If the call "
        f"site was removed, update STAGE_D_CALLERS in this test."
    )

    for call in calls:
        passed = _kwarg_names(call)
        missing = REQUIRED_KWARGS - passed
        assert not missing, (
            f"{rel_path}:{call.lineno} — slice_and_export call is "
            f"missing required Stage D kwargs: {sorted(missing)}. "
            f"All three of {sorted(REQUIRED_KWARGS)} must be "
            f"forwarded so Babysitter can branch into the mirror-"
            f"tree path. Passing none of them silently re-enters "
            f"the legacy single-comp setupWorkspace path and "
            f"false-fails the post-inject audit on every "
            f"multi-comp scrape (Slot 12.5 Stage D)."
        )


def test_no_unlisted_callers_in_production_code() -> None:
    """Guard against drift: if a new file in python/ calls
    slice_and_export but isn't in STAGE_D_CALLERS, fail loudly.
    Forces the test to be updated alongside any new caller, which
    in turn forces the Stage D wiring to be reviewed."""
    python_root = os.path.join(REPO_ROOT, "python")
    listed = {os.path.normpath(p) for p in STAGE_D_CALLERS}
    found: Set[str] = set()

    for dirpath, dirnames, filenames in os.walk(python_root):
        # Skip tests and caches
        dirnames[:] = [
            d for d in dirnames
            if d not in {"tests", "__pycache__", ".pytest_cache"}
        ]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            abs_path = os.path.join(dirpath, fname)
            try:
                with open(abs_path, "r", encoding="utf-8") as fh:
                    source = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            # Cheap pre-filter before parsing
            if "slice_and_export" not in source:
                continue
            # Skip the exporter (defines the method, doesn't call it)
            if abs_path.endswith(os.path.join("logic", "exporter.py")):
                continue
            try:
                calls = _find_slice_and_export_calls(source)
            except SyntaxError:
                continue
            if not calls:
                continue
            rel = os.path.relpath(abs_path, REPO_ROOT)
            found.add(os.path.normpath(rel))

    unlisted = found - listed
    assert not unlisted, (
        f"Found slice_and_export call site(s) not listed in "
        f"STAGE_D_CALLERS: {sorted(unlisted)}. Add them to the "
        f"list in this test (and verify they forward the Stage D "
        f"kwargs)."
    )
