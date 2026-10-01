# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_babysitter_collapse_disable.py — Slot 12.5 Stage D collapse-
disable fix (2026-05-19).

Static contract tests for the rewire-phase collapse-override that
unblocks Corpus_01 → TikTok. Pre-fix, Precomp_A_Wrapper kept its
source `collapseTransformation: true` flag after rewire, which made
AE bypass the target-dimensioned mirror's frame and re-interpret
inner-layer transforms in Root_Omnibus_D's coordinate space —
content drifted off-canvas (X: -1749 in Matt's smoke).

Option A fix: after a successful rewire, set
`wrapper.collapseTransformation = false` on every mirror-tree
wrapper. Track each override; write the list to a sidecar JSON
so the Python report generator surfaces a designer-visible
warning section.

These tests pin the JSX shape and the sidecar contract so a
future regression that drops either fails CI without needing AE.
"""

from __future__ import annotations

import re
from pathlib import Path


_BABYSITTER_PATH = (
    Path(__file__).resolve().parents[2]
    / "Scripts" / "Dimension_Assets" / "Babysitter.jsx"
)


def _read() -> str:
    return _BABYSITTER_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Rewire phase disables collapse + records the override
# ---------------------------------------------------------------------------

class TestRewirePhaseDisablesCollapse:
    def test_tally_has_collapse_overrides_list(self):
        """`_executeRewirePhase` initialises a `collapse_overrides`
        list on the tally so successful overrides can be recorded."""
        src = _read()
        assert "collapse_overrides:" in src or "collapse_overrides :" in src, (
            "Rewire-phase tally must include a `collapse_overrides` "
            "list. Without it the report generator has nothing to "
            "surface — and Stage D requires a designer-visible "
            "warning per override, not just a log line."
        )

    def test_sets_collapse_transformation_false(self):
        """After a successful rewire, the wrapper's
        collapseTransformation flag must be set to false. Without
        this line the bug returns: collapse-ON wrappers bypass the
        mirror frame and content lands off-canvas."""
        src = _read()
        assert re.search(
            r"wrapper\.collapseTransformation\s*=\s*false",
            src,
        ), (
            "Rewire phase must set `wrapper.collapseTransformation "
            "= false` so the wrapper rasterizes its target-"
            "dimensioned mirror. Pre-fix the flag carried forward "
            "from the source and bypassed the mirror frame."
        )

    def test_only_overrides_when_collapse_was_on(self):
        """Wrappers without collapse-on stay untouched. The override
        only records (and only fires) when
        `wrapper.collapseTransformation === true`."""
        src = _read()
        assert re.search(
            r"if\s*\(\s*wrapper\.collapseTransformation\s*===\s*true\s*\)",
            src,
        ), (
            "Collapse override must be gated on the wrapper having "
            "Collapse Transformations ON. Force-disabling an "
            "already-off wrapper is a no-op but pollutes the audit "
            "log and the report's warning section."
        )

    def test_override_payload_carries_diagnostics(self):
        """Each override entry must carry enough fields for the
        report row: wrapper_uid, wrapper_name, containing comp,
        target mirror source. Anything less leaves the designer
        guessing which wrapper got touched."""
        src = _read()
        # Locate the rewire phase's collapse-disable push site.
        match = re.search(
            r"tally\.collapse_overrides\.push\s*\(\s*\{([^}]+)\}\s*\)",
            src,
        )
        assert match, "Could not find `tally.collapse_overrides.push({...})` site"
        payload = match.group(1)
        for field in (
            "wrapper_uid",
            "wrapper_name",
            "wrapper_in_source_comp",
            "target_mirror_source_name",
        ):
            assert field in payload, (
                f"Collapse override payload missing `{field}`. The "
                f"post-inject report row needs this to identify the "
                f"wrapper to a designer scanning the warning section."
            )


# ---------------------------------------------------------------------------
# 2. Pump phase plumbs overrides through to state + post-inject log
# ---------------------------------------------------------------------------

class TestPumpPhasePlumbsOverrides:
    def test_state_has_collapse_overrides_slot(self):
        """`_state.collapseOverrides` declared in `_resetState` so
        the audit phase can read what the rewire phase wrote."""
        src = _read()
        assert "collapseOverrides" in src, (
            "_state.collapseOverrides slot missing from _resetState. "
            "The rewire phase needs a state field to stash overrides "
            "in for the audit phase to consume."
        )

    def test_audit_writes_sidecar_json(self):
        """Audit phase must call `_writeJSONFile` to emit the
        `inject_collapse_overrides.json` sidecar the report reads."""
        src = _read()
        assert "_writeJSONFile" in src, (
            "_writeJSONFile helper missing — required for the "
            "audit phase to emit a truncating-write sidecar JSON. "
            "Append-mode (_writeLog) would corrupt the document on "
            "a second inject run."
        )
        assert "inject_collapse_overrides.json" in src, (
            "Sidecar file name `inject_collapse_overrides.json` not "
            "found. The report generator looks for this exact name "
            "next to transfer_status.log."
        )


# ---------------------------------------------------------------------------
# 3. Failure modes are non-fatal
# ---------------------------------------------------------------------------

class TestFailureModesNonFatal:
    def test_collapse_write_in_try_block(self):
        """The collapse-disable write must be inside a try/catch so
        a failure on a single wrapper doesn't take down the whole
        rewire phase. The rewire itself succeeded by that point;
        the override is a fix-up."""
        src = _read()
        # Find the collapse-disable site and verify it's inside a
        # try block.
        write_match = re.search(
            r"wrapper\.collapseTransformation\s*=\s*false",
            src,
        )
        assert write_match, "Could not find collapse-disable site"
        # Look back for the nearest enclosing try.
        prefix = src[:write_match.start()]
        last_try_idx = prefix.rfind("try {")
        last_catch_idx = prefix.rfind("catch")
        assert last_try_idx > last_catch_idx, (
            "Collapse-disable line must be inside a try block. "
            "Without one, an AE quirk on a single wrapper (eg "
            "collapseTransformation read-only on some layer kinds) "
            "would abort the entire rewire pass mid-flight."
        )
