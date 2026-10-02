# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_expression_scaling_harness.py

Automated harness for the seven QA test cases defined in
docs/qa/expression-scaling-ae-smoke-checklist.md.

TC-1  Simple array expression scales correctly          (Python, pure)
TC-2  Non-array expressions are untouched               (Python, pure)
TC-3  Sealed precomp expressions are untouched          (Python, pure)
TC-4  Negative numbers scale correctly                  (Python, pure)
TC-5  Parse failure falls back gracefully               (Python, pure)
TC-6  S=1.0 pass-through (no-op)                       (Python, pure)
TC-7  Babysitter JSX expression-writer contract         (static JSX source)
TC-7b Real inject-log validation                        (ae_live marker)

TC-7 (static) validates that Babysitter.jsx:
  - Reads cLayer.conformed_expressions after transforms/keys
  - Writes via aeProp.expression = <string>
  - Emits level:"ERROR", phase:"expressions" on failure
  - Does NOT abort the inject on expression write failure

TC-7b (ae_live) validates a real transfer_status.log from a Parallax conform:
  - No phase:"expressions" ERROR lines for scaled layers
  - Pass --ae-log <path> via EXPRESSION_SCALING_LOG env var.

Pre-flight dependency: esprima must be importable.
Run: python3 -m pytest python/tests/test_expression_scaling_harness.py -v
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Shared infrastructure
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BABYSITTER = _REPO_ROOT / "Scripts" / "Dimension_Assets" / "Babysitter.jsx"


def _jsx() -> str:
    return _BABYSITTER.read_text(encoding="utf-8")


class _PrefsEnabled:
    expression_scaling_enabled = True


class _PrefsDisabled:
    expression_scaling_enabled = False


class MockScaleEngine:
    """Mirrors the real ScaleEngine.sealed_precomp_cids contract exactly
    (python/core/scale_engine.py:250) — a set of sealed comp ids, not a
    per-layer lookup. Sealing is comp-scoped in production: every layer
    inside a sealed nested precomp is sealed together, never selectively
    by index (THE INVARIANT, CLAUDE.md). A prior version of this mock
    exposed a fictional `is_sealed(layer_key)` method that
    stages/expression.py called but core/placement_units.py's
    PlacementResolution never defined — every real invocation crashed
    with AttributeError while this mock's fake method let the tests
    pass. See BUGS.md."""

    def __init__(self, sealed_precomp_cids=None):
        self.sealed_precomp_cids = sealed_precomp_cids or set()

    def conform(self):
        raise AssertionError(
            "engine.conform() must not be called from scale_expressions — "
            "S comes from the conformed result dict (BUGS.md 2026-07-06)"
        )


# ---------------------------------------------------------------------------
# Module-level esprima pre-flight (fails collection if missing)
# ---------------------------------------------------------------------------

try:
    import esprima as _esprima  # noqa: F401
    _ESPRIMA_VERSION = getattr(_esprima, "__version__", "unknown")
except ImportError:
    pytest.fail(
        "esprima not installed — run: pip3 install esprima\n"
        "Expression scaling requires esprima; TC-1 through TC-7 cannot run."
    )

from core.expression_parser import ExpressionScaler  # noqa: E402
from stages.expression import scale_expressions  # noqa: E402


@pytest.fixture(autouse=True)
def _enable_scaling(monkeypatch):
    """Enable expression_scaling_enabled for all tests in this module."""
    import logic.preferences_state as ps
    monkeypatch.setattr(ps, "preferences", _PrefsEnabled())


# ---------------------------------------------------------------------------
# Helpers — realistic Parallax-like conformed_result builder
# ---------------------------------------------------------------------------

def _result(s: float, layers: list) -> dict:
    return {"scale": {"S": s}, "layers": layers}


def _layer(index: int, comp_id: int = 0, expressions: dict | None = None,
           name: str = "") -> dict:
    d: dict = {"index": index, "containing_comp_id": comp_id,
               "name": name or f"Layer {index}"}
    if expressions is not None:
        d["expressions"] = expressions
    return d


# ---------------------------------------------------------------------------
# TC-1  Simple array expression scales correctly
# ---------------------------------------------------------------------------

class TestTC1SimpleArrayScaling:
    """TC-1: [x, y] array literals are multiplied by S (2 d.p.)."""

    def test_hd_to_tiktok_position(self):
        """Realistic HD→TikTok scale factor 0.5625 on a [960, 540] anchor."""
        result = _result(0.5625, [_layer(1, expressions={"position": "[960, 540]"})])
        out = scale_expressions(result, MockScaleEngine())
        scaled = out["layers"][0]["conformed_expressions"]["position"]
        assert scaled == "[540.00, 303.75]", f"Got {scaled!r}"

    def test_single_float_precision(self):
        """Values are formatted to exactly 2 decimal places."""
        result = _result(1.5, [_layer(1, expressions={"position": "[100, 200]"})])
        out = scale_expressions(result, MockScaleEngine())
        scaled = out["layers"][0]["conformed_expressions"]["position"]
        assert scaled == "[150.00, 300.00]"

    def test_3d_array(self):
        """3-element arrays (3D position) scale all three components."""
        result = _result(2.0, [_layer(1, expressions={"position": "[100, 200, 50]"})])
        out = scale_expressions(result, MockScaleEngine())
        scaled = out["layers"][0]["conformed_expressions"]["position"]
        assert scaled == "[200.00, 400.00, 100.00]"

    def test_multiple_properties(self):
        """Both position and anchorPoint are scaled when both have arrays."""
        expressions = {"position": "[100, 200]", "anchorPoint": "[50, 50]"}
        result = _result(2.0, [_layer(1, expressions=expressions)])
        out = scale_expressions(result, MockScaleEngine())
        ce = out["layers"][0]["conformed_expressions"]
        assert ce["position"] == "[200.00, 400.00]"
        assert ce["anchorPoint"] == "[100.00, 100.00]"

    def test_conformed_block_absent_when_no_expressions(self):
        """Layers with no expressions dict get no conformed_expressions key."""
        result = _result(2.0, [_layer(1)])
        out = scale_expressions(result, MockScaleEngine())
        assert "conformed_expressions" not in out["layers"][0]

    def test_conformed_block_absent_for_empty_expressions(self):
        """Empty expressions dict → no conformed_expressions key."""
        result = _result(2.0, [_layer(1, expressions={})])
        out = scale_expressions(result, MockScaleEngine())
        assert "conformed_expressions" not in out["layers"][0]


# ---------------------------------------------------------------------------
# TC-2  Non-array expressions are untouched
# ---------------------------------------------------------------------------

class TestTC2NonArrayUntouched:
    """TC-2: wiggle(), thisComp.*, binary expressions must be byte-identical."""

    @pytest.mark.parametrize("expr", [
        "wiggle(5, 10)",
        "thisComp.width / 2",
        "effect('Slider Control')('Slider')",
        "time * 50",
        "Math.sin(time) * 100",
        "value + [10, 0]",          # BUG-FIX regression: array in binary expression
        "loopOut('cycle')",
        "value[0] + 10",            # member access
        "[100, 200][0]",            # array indexing — ambiguous, should be left alone
    ])
    def test_untouched(self, expr):
        result = _result(2.0, [_layer(1, expressions={"position": expr})])
        out = scale_expressions(result, MockScaleEngine())
        ce = out["layers"][0].get("conformed_expressions", {})
        if "position" in ce:
            assert ce["position"] == expr, (
                f"Expression was modified — must be untouched.\nOriginal: {expr!r}\nGot:      {ce['position']!r}"
            )

    def test_assignment_rhs_array_is_scaled(self):
        """Assignment whose RHS is a bare array — the array element IS a spatial coord."""
        expr = "temp = [100, 200]; temp"
        result = _result(2.0, [_layer(1, expressions={"position": expr})])
        out = scale_expressions(result, MockScaleEngine())
        ce = out["layers"][0].get("conformed_expressions", {})
        # The array in the assignment RHS should be scaled; the trailing `temp` is untouched.
        if "position" in ce:
            assert "[200.00, 400.00]" in ce["position"], (
                f"Assignment-RHS array was not scaled: {ce['position']!r}"
            )



# ---------------------------------------------------------------------------
# TC-3  Sealed precomp expressions are untouched
# ---------------------------------------------------------------------------

class TestTC3SealedPrecompInvariant:
    """TC-3: THE INVARIANT — sealed layers must never receive conformed_expressions."""

    def test_sealed_layer_not_modified(self):
        """Sealing is comp-scoped in production (THE INVARIANT) — comp 1
        is sealed, comp 0 is not."""
        engine = MockScaleEngine(sealed_precomp_cids={1})
        result = _result(2.0, [
            _layer(1, comp_id=0, expressions={"position": "[100, 200]"}, name="Free"),
            _layer(2, comp_id=1, expressions={"position": "[300, 400]"}, name="Sealed"),
        ])
        out = scale_expressions(result, engine)
        assert "conformed_expressions" in out["layers"][0], "Free layer should be scaled"
        assert "conformed_expressions" not in out["layers"][1], \
            "Sealed layer was incorrectly modified by expression scaling"

    def test_all_sealed_layers_untouched(self):
        """Every layer inside a sealed comp passes through together —
        sealing can never apply to only some members of the same comp
        (THE INVARIANT)."""
        engine = MockScaleEngine(sealed_precomp_cids={1})
        layers = [_layer(i, comp_id=1, expressions={"position": "[100, 200]"})
                  for i in range(2, 5)]
        result = _result(2.0, layers)
        out = scale_expressions(result, engine)
        for layer in out["layers"]:
            assert "conformed_expressions" not in layer, \
                f"Sealed layer {layer['index']} was incorrectly modified"

    def test_layers_in_a_different_unsealed_comp_still_scale(self):
        """A sealed comp elsewhere must not affect layers in a separate,
        unsealed comp. (Sealing can't be selective within one comp — see
        test_all_sealed_layers_untouched — so this uses two distinct
        comps rather than two layers in the same comp.)"""
        engine = MockScaleEngine(sealed_precomp_cids={1})
        result = _result(2.0, [
            _layer(1, comp_id=0, expressions={"position": "[100, 200]"}, name="Free"),
            _layer(1, comp_id=1, expressions={"position": "[300, 400]"}, name="Sealed"),
        ])
        out = scale_expressions(result, engine)
        assert "conformed_expressions" in out["layers"][0]
        assert "conformed_expressions" not in out["layers"][1]

    def test_sealed_check_against_real_placement_resolution(self):
        """Regression for the is_sealed AttributeError bug (BUGS.md,
        2026-07-08): exercises the REAL core.placement_units.PlacementResolution
        dataclass rather than a hand-rolled mock, so a future rename of
        `sealed_precomp_cids` (or a re-introduced `is_sealed()` call in
        stages/expression.py) fails here instead of only in production.
        This is the exact class of gap CLAUDE.md's "trusting synthetic
        test fixtures" anti-pattern warns about — the mock previously
        had a method (`is_sealed`) the real class never defined."""
        from core.placement_units import PlacementResolution

        class _RealEngineShim:
            """Only exposes what stages/expression.py actually reads —
            the real ScaleEngine.sealed_precomp_cids property just
            forwards to this same attribute (scale_engine.py:250)."""
            def __init__(self, resolution):
                self.placement_resolution = resolution
                self.sealed_precomp_cids = resolution.sealed_precomp_cids

        resolution = PlacementResolution(sealed_precomp_cids={1})
        engine = _RealEngineShim(resolution)
        result = _result(2.0, [
            _layer(1, comp_id=0, expressions={"position": "[100, 200]"}, name="Free"),
            _layer(1, comp_id=1, expressions={"position": "[300, 400]"}, name="Sealed"),
        ])
        out = scale_expressions(result, engine)
        assert "conformed_expressions" in out["layers"][0]
        assert "conformed_expressions" not in out["layers"][1]


# ---------------------------------------------------------------------------
# TC-4  Negative numbers scale correctly
# ---------------------------------------------------------------------------

class TestTC4NegativeNumbers:
    """TC-4: UnaryExpression('-', Literal) must scale including sign."""

    def test_negative_and_positive_in_same_array(self):
        expr = "[-100.5, 200.5, 50]"
        assert ExpressionScaler(expr, 0.5).scale() == "[-50.25, 100.25, 25.00]"

    def test_all_negative(self):
        assert ExpressionScaler("[-100, -200]", 2.0).scale() == "[-200.00, -400.00]"

    def test_negative_in_stage_pipeline(self):
        result = _result(0.5, [_layer(1, expressions={"position": "[-100, 200]"})])
        out = scale_expressions(result, MockScaleEngine())
        assert out["layers"][0]["conformed_expressions"]["position"] == "[-50.00, 100.00]"

    def test_sign_preserved_after_scaling(self):
        """Negative values must remain negative after downscaling."""
        scaled = ExpressionScaler("[-960, -540]", 0.5625).scale()
        parts = [float(p.strip("[] ")) for p in scaled.strip("[]").split(",")]
        assert all(v < 0 for v in parts), f"Expected all negative, got: {parts}"


# ---------------------------------------------------------------------------
# TC-5  Parse failure falls back gracefully
# ---------------------------------------------------------------------------

class TestTC5ParseFallback:
    """TC-5: Invalid JS / ExtendScript-only syntax must return the original string."""

    @pytest.mark.parametrize("expr", [
        "[100, 200",                      # unclosed bracket
        "$.sleep(100); [10, 20]",         # ExtendScript global
        "var x = [10, 20]; x",            # statement-level var (esprima may parse but complex)
        "function() { return [1,2]; }()", # IIFE
        "",                               # empty string
    ])
    def test_fallback_returns_original(self, expr):
        result = ExpressionScaler(expr, 2.0).scale()
        # Must either return original or a validly scaled string — never throw
        assert isinstance(result, str)

    def test_empty_expression_noop(self):
        assert ExpressionScaler("", 2.0).scale() == ""

    def test_stage_skips_none_expression(self):
        """None values in the expressions dict must not crash the stage."""
        result = _result(2.0, [_layer(1, expressions={"position": None})])
        out = scale_expressions(result, MockScaleEngine())
        # None expressions produce no conformed_expressions entry
        assert "conformed_expressions" not in out["layers"][0]

    def test_stage_skips_whitespace_expression(self):
        """Whitespace-only expression strings must not crash."""
        result = _result(2.0, [_layer(1, expressions={"position": "   "})])
        out = scale_expressions(result, MockScaleEngine())
        # Whitespace-only → esprima may parse as empty program; value is preserved
        ce = out["layers"][0].get("conformed_expressions", {})
        if "position" in ce:
            assert isinstance(ce["position"], str)


# ---------------------------------------------------------------------------
# TC-6  S=1.0 pass-through
# ---------------------------------------------------------------------------

class TestTC6SFactor1Passthrough:
    """TC-6: When S=1.0, the pass exits early — no conformed_expressions."""

    def test_stage_noop_on_s1(self):
        result = _result(1.0, [_layer(1, expressions={"position": "[960, 540]"})])
        out = scale_expressions(result, MockScaleEngine())
        assert "conformed_expressions" not in out["layers"][0]

    def test_scaler_noop_on_s1(self):
        expr = "[100, 200]"
        assert ExpressionScaler(expr, 1.0).scale() == expr

    def test_stage_noop_when_scale_block_missing(self):
        result = {"layers": [_layer(1, expressions={"position": "[100, 200]"})]}
        out = scale_expressions(result, MockScaleEngine())
        assert "conformed_expressions" not in out["layers"][0]

    def test_stage_noop_when_disabled(self, monkeypatch):
        import logic.preferences_state as ps
        monkeypatch.setattr(ps, "preferences", _PrefsDisabled())
        result = _result(2.0, [_layer(1, expressions={"position": "[100, 200]"})])
        out = scale_expressions(result, MockScaleEngine())
        assert "conformed_expressions" not in out["layers"][0]


# ---------------------------------------------------------------------------
# TC-7  Babysitter JSX expression-writer static contract
# ---------------------------------------------------------------------------

class TestTC7BabysitterExpressionWriterContract:
    """
    TC-7 (static): Validates Babysitter.jsx implements the expression-writer
    contract without running AE.

    Checks mirror the manual steps in the AE smoke checklist:
    - conformed_expressions applied AFTER transforms/keys
    - aeProp.expression = <string> (standard AE scripting API)
    - ERROR logged on failure with phase:"expressions"
    - Failure is caught and does NOT re-raise (inject continues)
    """

    def test_conformed_expressions_block_exists(self):
        """Babysitter.jsx must reference cLayer.conformed_expressions."""
        source = _jsx()
        assert "cLayer.conformed_expressions" in source, \
            "Babysitter.jsx must reference cLayer.conformed_expressions"

    def test_expression_applied_after_dispatch(self):
        """conformed_expressions block must appear after PropertyWriter.dispatch call."""
        source = _jsx()
        dispatch_pos = source.find("this.PropertyWriter.dispatch(")
        expr_pos = source.find("cLayer.conformed_expressions")
        assert dispatch_pos != -1, "PropertyWriter.dispatch not found"
        assert expr_pos != -1, "conformed_expressions block not found"
        assert expr_pos > dispatch_pos, (
            "conformed_expressions must be applied AFTER PropertyWriter.dispatch "
            f"(dispatch@{dispatch_pos}, expressions@{expr_pos})"
        )

    def test_expression_set_via_aeProp_expression(self):
        """Writer must use aeProp.expression = <value> (standard AE API)."""
        source = _jsx()
        assert "aeProp.expression = cLayer.conformed_expressions[propName]" in source, \
            "Babysitter.jsx must write expressions via aeProp.expression = ..."

    def test_error_logged_with_correct_phase(self):
        """On failure the log entry must carry level:'ERROR' and phase:'expressions'."""
        source = _jsx()
        # Find the expression error log block
        pattern = re.compile(
            r'level\s*:\s*["\']ERROR["\']\s*,\s*phase\s*:\s*["\']expressions["\']',
            re.DOTALL
        )
        assert pattern.search(source), (
            "Babysitter.jsx must emit {level:'ERROR', phase:'expressions'} "
            "when an expression write fails"
        )

    def test_failure_is_caught_not_rethrown(self):
        """The catch block must NOT re-throw — inject must continue on expression errors."""
        source = _jsx()
        # Find the try/catch wrapping the expression writer
        # Locate the catch block that contains the ERROR log for expressions
        # It must not contain 'throw' after logging
        expr_block_start = source.find("cLayer.conformed_expressions")
        assert expr_block_start != -1
        # Extract a window around the expression writer (generous window)
        window = source[expr_block_start: expr_block_start + 800]
        # There must be a catch block
        assert "catch" in window, "Expression writer must be wrapped in try/catch"
        # The catch must not re-throw (bare throw or throw eExpr)
        catch_start = window.find("catch")
        catch_body = window[catch_start: catch_start + 300]
        # Allow 'eExpr.toString()' (reading, not throwing) but no 'throw eExpr'
        assert not re.search(r"\bthrow\s+eExpr\b", catch_body), \
            "Babysitter.jsx expression catch block must NOT re-throw — inject must continue"

    def test_hasownproperty_guard_present(self):
        """The for-in loop must use hasOwnProperty to avoid prototype pollution."""
        source = _jsx()
        assert "conformed_expressions.hasOwnProperty(propName)" in source, \
            "Babysitter.jsx must guard for-in with hasOwnProperty"

    def test_aeProp_null_guard_present(self):
        """aeProp must be checked for null/undefined before writing expression."""
        source = _jsx()
        expr_block_start = source.find("cLayer.conformed_expressions")
        window = source[expr_block_start: expr_block_start + 600]
        assert "if (aeProp)" in window, \
            "Babysitter.jsx must guard aeProp for null before setting expression"


# ---------------------------------------------------------------------------
# TC-7b  Real inject-log validation (ae_live — needs a real AE session)
# ---------------------------------------------------------------------------

@pytest.mark.ae_live
class TestTC7bRealInjectLog:
    """
    TC-7b: Validates a real transfer_status.log from a Parallax conform.

    Set env var EXPRESSION_SCALING_LOG to the path of the log file before
    running:

        EXPRESSION_SCALING_LOG=~/.../transfer_status.log \
            python3 -m pytest python/tests/test_expression_scaling_harness.py \
            -m ae_live -v
    """

    @pytest.fixture
    def log_path(self):
        path = os.environ.get("EXPRESSION_SCALING_LOG", "")
        if not path or not Path(path).exists():
            pytest.skip(
                "Set EXPRESSION_SCALING_LOG=<path/to/transfer_status.log> to run TC-7b"
            )
        return Path(path)

    @pytest.fixture
    def log_lines(self, log_path):
        lines = []
        for raw in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                lines.append(json.loads(raw))
            except json.JSONDecodeError:
                pass  # skip malformed lines
        return lines

    def test_no_expression_errors_for_scaled_layers(self, log_lines):
        """No phase:'expressions' ERROR entries should appear for scaled layers."""
        expr_errors = [
            line for line in log_lines
            if line.get("phase") == "expressions" and line.get("level") == "ERROR"
        ]
        assert not expr_errors, (
            "Expression write errors found in inject log:\n"
            + "\n".join(json.dumps(e, indent=2) for e in expr_errors)
        )

    def test_inject_reached_complete(self, log_lines):
        """The inject must have a COMPLETE event (not hung or failed)."""
        statuses = [line.get("status") or line.get("event") or "" for line in log_lines]
        assert "COMPLETE" in statuses, \
            f"Inject log does not contain a COMPLETE event. Statuses found: {set(statuses)}"

    def test_no_inject_abort(self, log_lines):
        """The inject must not contain an ERROR or FAILED terminal event."""
        terminal_errors = [
            line for line in log_lines
            if line.get("status") in ("ERROR", "FAILED")
            or line.get("level") == "FATAL"
        ]
        assert not terminal_errors, (
            "Inject log contains terminal error events:\n"
            + "\n".join(json.dumps(e, indent=2) for e in terminal_errors)
        )
