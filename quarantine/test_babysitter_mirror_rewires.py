# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_babysitter_mirror_rewires.py — Slot 12.5 Stage D / Item 3.

Static contract tests for the wrapper-rewire path in
`Scripts/Dimension_Assets/Babysitter.jsx`:

  - New pump phase "rewire" inserted between "setup" and "chunk"
  - `_executeRewirePhase` helper
  - `_resolveMirrorRewireTarget` helper (Q3A default + Q3B fork hook)
  - `_state.mirrorRewires` captured from chunk_manifest.mirror_rewires
  - `_resetState` clears the new field
  - 87N flat regression: pump skips rewire phase when mirror_rewires
    is absent / empty

Real AE round-trip verification is Matt's smoke step (Stage D exit
criterion). These tests pin the JSX shape so a future regression
that drops the phase, breaks the resolver, or strips the helpers
fails CI without needing AE.
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
# 1. New helpers exist
# ---------------------------------------------------------------------------

class TestNewHelpers:
    def test_execute_rewire_phase_exists(self):
        src = _read()
        assert "_executeRewirePhase: function(st)" in src, (
            "_executeRewirePhase helper must exist — driven by the "
            "pump's new 'rewire' phase to walk mirror_rewires + "
            "replaceSource onto target mirrors."
        )

    def test_resolve_mirror_rewire_target_exists(self):
        src = _read()
        assert "_resolveMirrorRewireTarget: function(rewire, mirrorComps)" in src, (
            "_resolveMirrorRewireTarget helper must exist — owns the "
            "Q3A shared-mirror lookup AND the Q3B fork-key forward-"
            "compat hook so future per-consumer forks resolve cleanly."
        )


# ---------------------------------------------------------------------------
# 2. Pump phase transition: setup → rewire → chunk
# ---------------------------------------------------------------------------

class TestPumpPhaseTransition:
    def _setup_block(self):
        src = _read()
        # The setup branch ends with the phase transition.
        start = src.index("if (st.phase === \"setup\")")
        end = src.index("if (st.phase === \"rewire\")")
        return src[start:end]

    def _rewire_block(self):
        src = _read()
        start = src.index("if (st.phase === \"rewire\")")
        end = src.index("if (st.phase === \"chunk\")")
        return src[start:end]

    def test_setup_transitions_to_rewire_when_rewires_present(self):
        body = self._setup_block()
        # The transition checks mirrorRewires length before going to
        # rewire — and skips straight to chunk when absent so 87N flat
        # regression stays byte-identical.
        assert re.search(
            r"st\.mirrorRewires\s*&&\s*st\.mirrorRewires\.length\s*>\s*0",
            body,
        ), (
            "Setup phase must branch on `st.mirrorRewires.length > 0` "
            "before transitioning to the rewire phase."
        )
        assert "\"rewire\"" in body and "\"chunk\"" in body, (
            "Setup phase must select between 'rewire' and 'chunk' as "
            "the next phase based on rewire presence."
        )

    def test_rewire_block_calls_execute_rewire_phase(self):
        body = self._rewire_block()
        assert "this._executeRewirePhase(st)" in body, (
            "Rewire phase must delegate to _executeRewirePhase(st) so "
            "the rewire work itself is encapsulated in the helper."
        )

    def test_rewire_block_transitions_to_chunk(self):
        body = self._rewire_block()
        assert "st.phase = \"chunk\";" in body, (
            "Rewire phase must transition to 'chunk' so the chunk pump "
            "runs after rewires are applied."
        )

    def test_rewire_block_writes_log_with_tally(self):
        body = self._rewire_block()
        # The log entry must surface the four tally fields the helper
        # returns so the audit can read how many rewires succeeded.
        for field in ("rewires_attempted", "rewires_made",
                       "rewires_skipped", "rewires_errored"):
            assert field in body, (
                f"Rewire phase log line must surface `{field}` so the "
                "audit / report can read rewire health."
            )


# ---------------------------------------------------------------------------
# 3. _executeRewirePhase behavior
# ---------------------------------------------------------------------------

class TestExecuteRewirePhase:
    def _body(self):
        src = _read()
        start = src.index("_executeRewirePhase: function(st)")
        end = src.index("_processLayerTransforms: function(cLayer, aeLayer)")
        return src[start:end]

    def test_wraps_in_undo_group(self):
        body = self._body()
        assert "app.beginUndoGroup(\"Dimension Conform — rewire mirror tree\")" in body, (
            "Rewire phase must run inside one undo group so partial "
            "failures roll back cleanly under the user's Cmd+Z."
        )
        assert "app.endUndoGroup();" in body

    def test_iterates_state_mirror_rewires(self):
        body = self._body()
        assert re.search(
            r"for\s*\(\s*var\s+ri\s*=\s*0\s*;\s*ri\s*<\s*rewires\.length",
            body,
        ), (
            "Helper must iterate every entry in _state.mirrorRewires."
        )

    def test_uses_find_layer_by_uid(self):
        body = self._body()
        # The wrapper's UID is the anchor — reuse Stage A's
        # findLayerByUID (which scans layer.comment for uid:<hex>).
        assert "this.findLayerByUID(" in body, (
            "Helper must locate the wrapper via findLayerByUID — that's "
            "the canonical UID lookup pattern. Name fallback follows."
        )

    def test_has_name_fallback(self):
        body = self._body()
        # When UID lookup misses, fall back to name match (same pattern
        # as v5.8 _findConformedLayer).
        assert re.search(
            r"if\s*\(\s*rw\.wrapper_layer_name\s*\)",
            body,
        ), (
            "Helper must fall back to wrapper_layer_name when UID "
            "lookup misses — matches the v5.8 _findConformedLayer pattern."
        )

    def test_calls_replace_source(self):
        body = self._body()
        assert "wrapper.replaceSource(targetMirror, false)" in body, (
            "Helper must call replaceSource(targetMirror, false) — "
            "`false` keeps expressions un-reevaluated since conform "
            "already wrote final values."
        )

    def test_verifies_replace_succeeded(self):
        body = self._body()
        # Re-check wrapper.source.id == targetMirror.id post-replace
        # so a silent AE no-op surfaces as errored.
        assert re.search(
            r"wrapper\.source\s*&&\s*wrapper\.source\.id\s*===\s*targetMirror\.id",
            body,
        ), (
            "Helper must verify wrapper.source.id matches targetMirror.id "
            "after replaceSource — catches silent AE no-ops."
        )

    def test_categorizes_failures_into_skipped_vs_errored(self):
        body = self._body()
        # Skipped = data missing (target/containing mirror absent).
        # Errored = work attempted but failed (wrapper not found,
        # replaceSource threw, verify mismatched).
        assert "tally.skipped" in body, (
            "Helper must track `skipped` for missing-data cases."
        )
        assert "tally.errored" in body, (
            "Helper must track `errored` for attempted-but-failed cases."
        )
        assert "tally.made" in body, (
            "Helper must track `made` for successful rewires."
        )


# ---------------------------------------------------------------------------
# 4. _resolveMirrorRewireTarget — Q3A default + Q3B fork hook
# ---------------------------------------------------------------------------

class TestResolveMirrorRewireTarget:
    def _body(self):
        src = _read()
        start = src.index("_resolveMirrorRewireTarget: function(rewire, mirrorComps)")
        end = src.index("_executeRewirePhase: function(st)")
        return src[start:end]

    def test_returns_shared_mirror_by_target_name(self):
        body = self._body()
        assert "mirrorComps[targetName]" in body, (
            "Q3A default resolution: shared mirror keyed by "
            "target_mirror_source_name."
        )

    def test_q3b_fork_hook_tries_composed_key_first(self):
        body = self._body()
        # The fork lookup composes "source_name#consumer_uid" — Babysitter
        # tries that key before falling through to the shared mirror.
        assert re.search(
            r"targetName\s*\+\s*[\"']#[\"']\s*\+\s*String\(rewire\.fork_consumer_layer_uid\)",
            body,
        ), (
            "Q3B fork hook must compose the lookup key as "
            "`<source_name>#<consumer_uid>` before falling through "
            "to the Q3A shared mirror."
        )

    def test_falls_through_to_shared_when_fork_absent_or_missing(self):
        body = self._body()
        # When fork_consumer_layer_uid is absent → fall through to
        # mirrorComps[targetName]. The forward-compat hook doesn't
        # break Q3A.
        assert "mirrorComps[forkKey]" in body, (
            "Fork lookup must check the composed forkKey."
        )
        # And the function still returns the shared mirror after the
        # fork-key fall-through.
        assert "return mirrorComps[targetName] || null;" in body, (
            "After the fork hook, the resolver returns the shared "
            "mirror as the Q3A default — fork resolution doesn't "
            "exclude Q3A fall-through."
        )

    def test_returns_null_on_missing_data(self):
        body = self._body()
        # Defensive guards: empty/missing inputs return null cleanly.
        assert "if (!rewire || !mirrorComps) return null;" in body, (
            "Resolver must short-circuit to null on missing inputs."
        )


# ---------------------------------------------------------------------------
# 5. State wiring: chunk_manifest → _state.mirrorRewires
# ---------------------------------------------------------------------------

class TestStateWiring:
    def test_reset_state_clears_mirror_rewires(self):
        src = _read()
        reset_start = src.index("_resetState: function()")
        reset_end = src.index("executeSovereignInjection: function")
        body = src[reset_start:reset_end]
        assert "mirrorRewires: null" in body, (
            "_resetState must clear mirrorRewires so a prior session "
            "doesn't leak rewires into a new one."
        )

    def test_execute_sovereign_injection_reads_mirror_rewires(self):
        src = _read()
        exec_start = src.index("executeSovereignInjection: function")
        exec_body = src[exec_start:exec_start + 5000]
        assert re.search(
            r"mirrorRewires:\s*manifest\.mirror_rewires",
            exec_body,
        ), (
            "executeSovereignInjection must populate _state.mirrorRewires "
            "from manifest.mirror_rewires (the chunk_manifest field "
            "exporter.py emits when the orchestrator built rewires)."
        )


# ---------------------------------------------------------------------------
# 6. ES3 compatibility of the new code
# ---------------------------------------------------------------------------

class TestEs3Compatibility:
    def _new_code(self):
        src = _read()
        start = src.index("_resolveMirrorRewireTarget: function(rewire, mirrorComps)")
        end = src.index("_processLayerTransforms: function(cLayer, aeLayer)")
        return src[start:end]

    def test_no_arrow_functions(self):
        body = self._new_code()
        assert "=>" not in body, "Arrow functions are not ES3-compatible."

    def test_no_let_or_const(self):
        body = self._new_code()
        assert not re.search(r"\blet\s+\w", body), "ES3: use var, not let."
        assert not re.search(r"\bconst\s+\w", body), "ES3: use var, not const."

    def test_uses_classic_for_loops(self):
        body = self._new_code()
        assert re.search(r"for\s*\(\s*var\s+\w+\s*=", body), (
            "New JSX must use classic `for (var ...; ...; ...)` loops."
        )
