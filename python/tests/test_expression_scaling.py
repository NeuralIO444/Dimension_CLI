# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_expression_scaling.py

Tests for the expression scaling stage, ensuring that numeric literals in
expressions are scaled correctly while respecting the sealed-unit invariant.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from stages.expression import scale_expressions  # noqa: E402
from core.expression_parser import ExpressionScaler # noqa: E402


class MockScaleEngine:
    """Mocks the ScaleEngine to provide sealed-comp membership.

    Mirrors the real ScaleEngine.sealed_precomp_cids contract exactly
    (python/core/scale_engine.py:250) — a set of sealed comp ids.
    Sealing is comp-scoped in production (THE INVARIANT, CLAUDE.md):
    every layer inside a sealed nested precomp is sealed together,
    never selectively by index. A prior version of this mock exposed a
    fictional `is_sealed(layer_key)` method that stages/expression.py
    called but core/placement_units.py's PlacementResolution never
    defined — every real invocation crashed with AttributeError while
    this mock's fake method let the tests pass. See BUGS.md."""

    def __init__(self, s_factor=1.0, sealed_precomp_cids=None):
        self._s_factor = s_factor
        self.sealed_precomp_cids = sealed_precomp_cids or set()

    def conform(self):
        raise AssertionError(
            "engine.conform() must not be called from scale_expressions — "
            "S comes from the conformed result dict (BUGS.md 2026-07-06)"
        )


class _PrefsStub:
    expression_scaling_enabled = True


@pytest.fixture(autouse=True)
def _enable_expression_scaling(monkeypatch):
    """The pass is gated off by default; enable it for these tests."""
    import logic.preferences_state as ps
    monkeypatch.setattr(ps, "preferences", _PrefsStub())


@pytest.fixture
def sample_conformed_result():
    """Provides a sample conformed_result dictionary for tests."""
    return {
        "scale": {"S": 1.5},
        "layers": [
            {
                "index": 1,
                "name": "Layer 1 (Scalable)",
                "containing_comp_id": 0,
                "expressions": {"position": "[100, 200]"},
            },
            {
                "index": 2,
                "name": "Layer 2 (Sealed)",
                "containing_comp_id": 1,
                "expressions": {"position": "[300, 400]"},
            },
            {
                "index": 3,
                "name": "Layer 3 (No Expressions)",
                "containing_comp_id": 0,
            },
        ]
    }


class TestExpressionScalingStage:
    def test_scale_expressions_adds_conformed_block(self, sample_conformed_result):
        """Tests that the main stage function correctly adds a conformed_expressions block."""
        engine = MockScaleEngine(s_factor=1.5)
        result = scale_expressions(sample_conformed_result, engine)

        scalable_layer = result["layers"][0]
        assert "conformed_expressions" in scalable_layer
        assert scalable_layer["conformed_expressions"]["position"] == "[150.00, 300.00]"

        # Verify layer with no expressions is untouched
        untouched_layer = result["layers"][2]
        assert "conformed_expressions" not in untouched_layer

    def test_sealed_unit_invariant_is_respected(self, sample_conformed_result):
        """
        CRITICAL: Verifies that layers belonging to a sealed unit are NOT modified.
        This is the core safety guarantee of the expression scaling pass.
        """
        # Mark comp 1 (containing Layer 2) as sealed — comp-scoped, per
        # THE INVARIANT (sealing can't apply to only one layer index
        # within a comp).
        engine = MockScaleEngine(s_factor=2.0, sealed_precomp_cids={1})
        sample_conformed_result["scale"] = {"S": 2.0}

        result = scale_expressions(sample_conformed_result, engine)

        # Layer 1 (not sealed) should be scaled
        scalable_layer = result["layers"][0]
        assert "conformed_expressions" in scalable_layer

        # Layer 2 (sealed) must NOT be scaled
        sealed_layer = result["layers"][1]
        assert "conformed_expressions" not in sealed_layer, "A sealed layer was incorrectly modified by expression scaling"

    def test_pass_is_disabled_by_default(self, sample_conformed_result, monkeypatch):
        """With the preference off (the default), the pass must be a no-op."""
        import logic.preferences_state as ps

        class _Disabled:
            expression_scaling_enabled = False

        monkeypatch.setattr(ps, "preferences", _Disabled())
        engine = MockScaleEngine(s_factor=1.5)
        result = scale_expressions(sample_conformed_result, engine)

        for layer in result["layers"]:
            assert "conformed_expressions" not in layer

    def test_missing_scale_block_is_a_noop(self, sample_conformed_result):
        """Without a scale block in the result, the pass must not guess a factor."""
        del sample_conformed_result["scale"]
        engine = MockScaleEngine()
        result = scale_expressions(sample_conformed_result, engine)

        for layer in result["layers"]:
            assert "conformed_expressions" not in layer


class TestAstExpressionScaler:
    """Unit tests for the AST-based ExpressionScaler."""

    def test_scales_simple_arrays(self):
        expr = "[100, 200]"
        assert ExpressionScaler(expr, 2.0).scale() == "[200.00, 400.00]"

    def test_scales_arrays_with_floats_and_negative_numbers(self):
        expr = "[-100.5, 200.5, 50]"
        assert ExpressionScaler(expr, 0.5).scale() == "[-50.25, 100.25, 25.00]"

    def test_ignores_numbers_in_function_calls(self):
        expr = "wiggle(5, 10)"
        assert ExpressionScaler(expr, 2.0).scale() == "wiggle(5, 10)"

    def test_ignores_numbers_in_binary_expressions(self):
        expr = "thisComp.width / 2"
        assert ExpressionScaler(expr, 2.0).scale() == "thisComp.width / 2"

    def test_scales_only_array_part_of_complex_expression(self):
        expr = "temp = [100, 200]; temp + wiggle(5, 10)"
        expected = "temp = [200.00, 400.00]; temp + wiggle(5, 10)"
        assert ExpressionScaler(expr, 2.0).scale() == expected

    def test_handles_invalid_javascript_gracefully(self):
        expr = "[100, 200"  # Missing closing bracket
        assert ExpressionScaler(expr, 2.0).scale() == expr, "Should return original string on parse error"

    def test_no_scaling_if_factor_is_one(self):
        expr = "[100, 200]"
        assert ExpressionScaler(expr, 1.0).scale() == expr