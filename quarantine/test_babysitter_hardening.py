# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_babysitter_hardening.py — Hardening & Resilience Verification tests.

Static contract tests to verify that the Babysitter.jsx ExtendScript injector
implements resilient error boundary guards, NaN/Infinity firewalls, proper
separate dimensions handling, and robust matching undo group closures.
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


def _extract_function(source: str, name: str) -> str:
    """Return the source of the function/property from the start of its
    declaration through the matching closing brace/bracket."""
    start = source.index(name)
    open_brace = source.index("{", start)
    depth = 1
    i = open_brace + 1
    n = len(source)
    while i < n and depth > 0:
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return source[start:i]


class TestES3HardeningCompatibility:
    def test_entire_file_es3_compatible(self):
        """Verify that no ES6+ syntax elements are present in the entire Babysitter.jsx."""
        src = _read()
        
        # 1. No arrow functions => (allowing comments or regexes if any, but checking for code usage)
        code_without_comments = re.sub(r"//.*|/\*[\s\S]*?\*/", "", src)
        assert "=>" not in code_without_comments, "Arrow functions '=>' are not ES3-compatible."

        # 2. No backtick template literals
        assert "`" not in code_without_comments, "Template literals '`' are not ES3-compatible."

        # 3. No let or const declarations
        assert not re.search(r"\blet\s+[a-zA-Z0-9_$]+", code_without_comments), (
            "`let` variable declarations are not ES3-compatible; use `var`."
        )
        assert not re.search(r"\bconst\s+[a-zA-Z0-9_$]+", code_without_comments), (
            "`const` variable declarations are not ES3-compatible; use `var`."
        )


class TestNumericSafetyFirewall:
    def test_is_finite_val_exists(self):
        """Verify that the numeric safety validator helper _isFiniteVal is declared."""
        src = _read()
        assert "_isFiniteVal:" in src, (
            "Babysitter.jsx must define a helper method `_isFiniteVal` to check "
            "for NaN and Infinity values before setting property parameters."
        )

    def test_safe_set_includes_finite_validation(self):
        """Verify that all `safeSet` instances validate values via `_isFiniteVal`, `isFinite`, or `_sanitizeValue`."""
        src = _read()
        
        # Extract the two contexts that define safeSet
        transforms_body = _extract_function(src, "_processLayerTransforms: function")
        effects_body = _extract_function(src, "_processLayerEffects: function")
        
        # Check that safeSet implementations in both contexts do checks.
        assert re.search(r"isFinite|isFiniteVal|_sanitizeValue", transforms_body), (
            "The safeSet implementation in _processLayerTransforms must validate "
            "incoming values against NaN/Infinity before writing to After Effects properties."
        )
        assert re.search(r"isFinite|isFiniteVal|_sanitizeValue", effects_body), (
            "The safeSet implementation in _processLayerEffects must validate "
            "incoming values against NaN/Infinity before writing."
        )

    def test_apply_keys_includes_finite_validation(self):
        """Verify that all `applyKeys` instances validate keyframe times/values."""
        src = _read()
        transforms_body = _extract_function(src, "_processLayerTransforms: function")
        effects_body = _extract_function(src, "_processLayerEffects: function")

        assert re.search(r"isFinite|isFiniteVal|_sanitizeValue", transforms_body), (
            "The applyKeys implementation in _processLayerTransforms must validate "
            "keyframe times/values against NaN/Infinity."
        )
        assert re.search(r"isFinite|isFiniteVal|_sanitizeValue", effects_body), (
            "The applyKeys implementation in _processLayerEffects must validate "
            "keyframe times/values against NaN/Infinity."
        )


class TestUndoGroupIntegrity:
    def test_undo_groups_matched_in_finally(self):
        """Ensure that every beginUndoGroup has a corresponding endUndoGroup
        guaranteed to run via try...finally or robust exception handling."""
        src = _read()
        
        # Find all occurrences of beginUndoGroup
        begin_matches = list(re.finditer(r"app\.beginUndoGroup\s*\(", src))
        assert len(begin_matches) >= 3, (
            "Expected at least 3 undo groups in Babysitter.jsx: "
            "rewire mirror tree, chunk processing, and duplication plan application."
        )
        
        # For each beginUndoGroup, search the surrounding/succeeding block for endUndoGroup
        for match in begin_matches:
            start_pos = match.start()
            # Extract 12000 characters after the begin call to check structure
            block = src[start_pos:start_pos + 12000]
            
            assert "app.endUndoGroup" in block, (
                "Every app.beginUndoGroup call must be paired with app.endUndoGroup() "
                "within the same execution scope."
            )
            
            # Verify try...finally structure for the chunk and rewire phases
            if "rewire mirror tree" in block or "chunk" in block:
                assert "finally" in block, (
                    "Rewire and chunk phases must use a `finally` block to guarantee "
                    "that app.endUndoGroup() runs even if an unhandled error aborts the phase."
                )


class TestSeparateDimensionsHandling:
    def test_separate_dimensions_detection_helper(self):
        """Verify that _isDimensionsSeparated exists and handles dimensionsSeparated checks."""
        src = _read()
        assert "_isDimensionsSeparated: function" in src or "_isDimensionsSeparated:" in src, (
            "Babysitter.jsx must define a `_isDimensionsSeparated` method."
        )

    def test_position_writers_respect_separated_flag(self):
        """Verify that _writePositionStatic and _writePositionKeys check for separated dimensions."""
        src = _read()
        static_body = _extract_function(src, "_writePositionStatic: function")
        keys_body = _extract_function(src, "_writePositionKeys: function")

        assert "_isDimensionsSeparated" in static_body, (
            "_writePositionStatic must check separate dimensions state."
        )
        assert "_isDimensionsSeparated" in keys_body, (
            "_writePositionKeys must check separate dimensions state."
        )


class TestLoggingSafety:
    def test_write_log_is_wrapped_in_exception_guards(self):
        """Ensure that _writeLog is protected by exception guards so logging failures
        cannot cause the main injection script to crash/abort."""
        src = _read()
        log_body = _extract_function(src, "_writeLog: function")
        
        # Check that there is an enclosing try...catch block in the log writer body
        assert "try" in log_body and "catch" in log_body, (
            "_writeLog must be wrapped in a try/catch block to absorb I/O "
            "or permissions failures."
        )


class TestMemoryManagementAndRollback:
    def test_gc_counter_initialization(self):
        """Verify that gcCounter is initialized to 0 in _resetState, executeSovereignInjection, and executeSovereignInjectionMemory."""
        src = _read()
        assert "gcCounter" in src, "Babysitter.jsx must initialize gcCounter to 0."
        
        reset_state_body = _extract_function(src, "_resetState: function")
        assert re.search(r"gcCounter:\s*0", reset_state_body), "gcCounter must be initialized to 0 in _resetState."
        
        exec_async_body = _extract_function(src, "executeSovereignInjection: function")
        assert re.search(r"gcCounter:\s*0", exec_async_body), "gcCounter must be initialized to 0 in executeSovereignInjection."
        
        exec_sync_body = _extract_function(src, "executeSovereignInjectionMemory: function")
        assert re.search(r"gcCounter:\s*0", exec_sync_body), "gcCounter must be initialized to 0 in executeSovereignInjectionMemory."


    def test_strategic_garbage_collection_in_writers(self):
        """Verify that safeSet and applyKeys increment gcCounter and run $.gc() modulo 50."""
        src = _read()
        transforms_body = _extract_function(src, "_processLayerTransforms: function")
        effects_body = _extract_function(src, "_processLayerEffects: function")
        
        assert "state: this._state" in transforms_body
        assert "gcCounter" in transforms_body
        assert "$.gc()" in transforms_body
        
        assert "state: this._state" in effects_body
        assert "gcCounter" in effects_body
        assert "$.gc()" in effects_body

    def test_strategic_garbage_collection_in_pump_layers_loop(self):
        """Verify that the layer processing loop inside _pump increments gcCounter and runs $.gc()."""
        src = _read()
        pump_body = _extract_function(src, "_pump: function")
        assert "st.gcCounter = (st.gcCounter || 0) + 1" in pump_body
        assert "$.gc()" in pump_body

    def test_undo_group_transaction_and_rollback(self):
        """Verify that executeSovereignInjectionMemory starts the 'Dimension Engine Inject' undo group and runs rollback Undo command on catch."""
        src = _read()
        exec_sync_body = _extract_function(src, "executeSovereignInjectionMemory: function")
        assert 'app.beginUndoGroup("Dimension Engine Inject")' in exec_sync_body
        assert 'app.endUndoGroup()' in exec_sync_body
        assert 'app.findMenuCommandId("Undo")' in exec_sync_body
        assert 'app.executeCommand' in exec_sync_body

