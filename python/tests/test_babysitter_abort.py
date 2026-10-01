# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Static contract tests for Track A Phase A3 cooperative abort.

Babysitter.jsx cannot run in pytest. These tests lock the abort-flag
filename, the boundary-check sites (rewire / chunk / audit start — never
mid-layer), the ABORTED beacon shape, and the busy=false reset so a later
edit cannot quietly restore E5.
"""

from __future__ import annotations


from test_babysitter_hardening import _extract_function, _read

ABORT_FILENAME = "inject_abort_request.json"


class TestAbortFlagContract:
    def test_filename_is_the_beacon_contract_name(self):
        src = _read()
        assert ABORT_FILENAME in src
        path_fn = _extract_function(src, "_abortRequestPath: function")
        assert ABORT_FILENAME in path_fn

    def test_abort_helpers_exist(self):
        src = _read()
        for name in (
            "_abortRequestPath:",
            "_clearAbortRequest:",
            "_abortRequested:",
            "_abortPump:",
        ):
            assert name in src, f"Babysitter.jsx must define {name}"


class TestAbortPumpBoundary:
    def test_pump_checks_abort_at_rewire_chunk_and_audit_start(self):
        pump = _extract_function(_read(), "_pump: function")
        assert pump.count("this._abortRequested(st.logPath)") == 3
        assert pump.count("this._abortPump(st)") == 3
        for phase in ('"rewire"', '"chunk"', '"audit"'):
            phase_pos = pump.index(f"if (st.phase === {phase})")
            abort_pos = pump.find("this._abortRequested(st.logPath)", phase_pos)
            assert abort_pos != -1, f"missing abort check in {phase} branch"
            next_phase = pump.find("if (st.phase ===", phase_pos + 1)
            if next_phase == -1:
                next_phase = len(pump)
            assert abort_pos < next_phase, f"abort check for {phase} is outside its branch"

    def test_setup_tick_does_not_check_abort(self):
        """Abort mid-setupWorkspace is unsafe (half-created mirrors).
        Cancel during setup is honored at the next rewire/chunk tick."""
        pump = _extract_function(_read(), "_pump: function")
        setup_pos = pump.index('if (st.phase === "setup")')
        rewire_pos = pump.index('if (st.phase === "rewire")')
        setup_branch = pump[setup_pos:rewire_pos]
        assert "this._abortRequested" not in setup_branch
        assert "this._abortPump" not in setup_branch

    def test_abort_is_not_inside_the_per_layer_loop(self):
        """Never abort mid-setValue / mid-layer (A3 sharp edge)."""
        pump = _extract_function(_read(), "_pump: function")
        loop_pos = pump.index("for (var l = 0;")
        chunk_pos = pump.index('if (st.phase === "chunk")')
        abort_pos = pump.find("this._abortRequested(st.logPath)", chunk_pos)
        assert abort_pos != -1
        assert abort_pos < loop_pos
        loop_body_probe = pump[loop_pos:loop_pos + 800]
        assert "this._abortRequested" not in loop_body_probe
        assert "this._abortPump" not in loop_body_probe


class TestAbortBeaconAndCleanup:
    def test_abort_pump_writes_terminal_aborted_and_resets(self):
        body = _extract_function(_read(), "_abortPump: function")
        assert 'status: "ABORTED"' in body
        assert 'error: "User cancelled"' in body
        assert "chunkIndex:" in body
        assert "totalChunks:" in body
        assert "this._resetState()" in body
        assert "this._clearAbortRequest" in body
        assert "this._scheduleNextTick" not in body
        assert "app.endUndoGroup" in body

    def test_execute_clears_stale_abort_flag(self):
        body = _extract_function(_read(), "executeSovereignInjection: function")
        assert "this._clearAbortRequest(logPath)" in body

    def test_memory_execute_also_clears_stale_abort_flag(self):
        body = _extract_function(_read(), "executeSovereignInjectionMemory: function")
        assert "this._clearAbortRequest(logPath)" in body
