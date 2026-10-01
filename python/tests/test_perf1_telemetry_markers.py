# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_perf1_telemetry_markers.py

Static source guard for the four PERF-1 Phase 2 gap-bisection telemetry
markers shipped in commit 8990488 (plan.md §4.2):

    marker 1  event "pump.enter"                (top of _pump())
    marker 2  event "pump.rewire.schedule"      (rewire → scheduler handoff)
    marker 3  phase "schedule_task_call"        (brackets app.scheduleTask)
    marker 4  phase "rewire_undo_close"         (brackets app.endUndoGroup)

Scope honesty (see CLAUDE.md anti-pattern "Trusting synthetic Python test
fixtures to prove a JSX↔Python integration contract"): this test proves
the marker SOURCE TEXT is present, correctly bracketed, and identical in
both the source-of-truth JSX and the CEP bundle AE actually loads. It
does NOT prove the markers fire at runtime or that their timings are
meaningful — only a live AE inject run (Matt's smoke test) proves that.
What this guards against is the cheap, silent regression: a future edit
deleting or reordering a marker so the timer no longer wraps the call it
claims to measure.
"""

from __future__ import annotations

import os

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_SOURCE = os.path.join(_REPO_ROOT, "Scripts", "Dimension_Assets", "Babysitter.jsx")
_BUNDLE = os.path.join(_REPO_ROOT, "cep", "jsx", "Babysitter.jsx")

# Each marker's uniquely identifying source fragment. Kept as fragments
# (not full lines) so whitespace/comment churn doesn't false-fail.
_MARKER_FRAGMENTS = {
    "pump.enter": 'event: "pump.enter"',
    "pump.rewire.schedule": 'event: "pump.rewire.schedule"',
    "schedule_task_call": 'phase: "schedule_task_call"',
    "rewire_undo_close": 'phase: "rewire_undo_close"',
}


def _read(path: str) -> str:
    assert os.path.isfile(path), f"missing JSX file: {path}"
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


@pytest.fixture(scope="module")
def source_text() -> str:
    return _read(_SOURCE)


class TestMarkersPresent:
    @pytest.mark.parametrize("marker,fragment", sorted(_MARKER_FRAGMENTS.items()))
    def test_marker_present_exactly_once_in_source(self, source_text, marker, fragment):
        count = source_text.count(fragment)
        assert count == 1, (
            f"PERF-1 marker '{marker}' expected exactly once in "
            f"Scripts/Dimension_Assets/Babysitter.jsx, found {count}. "
            "If it was intentionally removed, the gap-bisection telemetry "
            "in .pipeline/plan.md §4.2 no longer works — update the plan "
            "and this test together."
        )

    def test_bundle_carries_identical_markers(self, source_text):
        """The CEP bundle is what AE actually loads. test_cep_jsx_bundle_sync
        already asserts byte-parity for the whole file; this narrower check
        exists so a marker regression reports as a PERF-1 failure by name,
        not just a generic drift failure."""
        bundle_text = _read(_BUNDLE)
        for marker, fragment in _MARKER_FRAGMENTS.items():
            assert bundle_text.count(fragment) == 1, (
                f"PERF-1 marker '{marker}' missing/duplicated in cep/jsx "
                "bundle — re-run the package.sh bundling step."
            )


class TestBracketIntegrity:
    """The timers are only meaningful if T0/T1 actually straddle the call
    they claim to measure. Guard the ordering inside each bracket."""

    def test_schedule_task_call_brackets_scheduletask(self, source_text):
        t0 = source_text.index("var _scheduleT0")
        call = source_text.index("app.scheduleTask(", t0)
        t1 = source_text.index("var _scheduleT1", call)
        log = source_text.index(_MARKER_FRAGMENTS["schedule_task_call"], t1)
        assert t0 < call < t1 < log, (
            "schedule_task_call bracket broken: expected "
            "_scheduleT0 → app.scheduleTask() → _scheduleT1 → log, in order."
        )

    def test_rewire_undo_close_brackets_endundogroup(self, source_text):
        t0 = source_text.index("var _rewireUndoT0")
        call = source_text.index("app.endUndoGroup();", t0)
        t1 = source_text.index("var _rewireUndoT1", call)
        log = source_text.index(_MARKER_FRAGMENTS["rewire_undo_close"], t1)
        flag = source_text.index("undoGroupOpen = false;", log)
        assert t0 < call < t1 < log < flag, (
            "rewire_undo_close bracket broken: expected _rewireUndoT0 → "
            "app.endUndoGroup() → _rewireUndoT1 → log → undoGroupOpen=false, "
            "in order. NOTE: moving the log AFTER `undoGroupOpen = false` is "
            "also acceptable behaviorally — if that reorder was intentional, "
            "update this test; anything else here is a real regression."
        )

    def test_pump_enter_reuses_preexisting_entry_timestamp(self, source_text):
        """Marker 1 must reuse the pre-existing _pumpEntryMs (which also
        feeds tick_total_ms) — a second `new Date()` for the marker would
        make pump.enter.t_ms and tick_total_ms disagree."""
        decl = source_text.index("var _pumpEntryMs")
        log = source_text.index(_MARKER_FRAGMENTS["pump.enter"])
        assert decl < log, "_pumpEntryMs must be declared before the pump.enter log line"
        assert "t_ms: _pumpEntryMs" in source_text, (
            "pump.enter must stamp t_ms from _pumpEntryMs, not a fresh Date()"
        )

    def test_rewire_schedule_precedes_schedulenexttick(self, source_text):
        """Marker 2 is the anchor for the scheduler-dead-time segment; it
        must fire immediately before the handoff, not after."""
        log = source_text.index(_MARKER_FRAGMENTS["pump.rewire.schedule"])
        handoff = source_text.index("this._scheduleNextTick();", log)
        between = source_text[log:handoff]
        # nothing substantial may sit between the anchor and the handoff
        assert between.count(";") <= 2, (
            "pump.rewire.schedule must be the last logged event before "
            "_scheduleNextTick() in the rewire branch — code inserted "
            "between them silently inflates the scheduler-dead-time segment."
        )
