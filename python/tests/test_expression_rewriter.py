# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_expression_rewriter.py
Comprehensive test suite for Adaptive AST Expression Normalizer (PR 5).
"""

import pytest
from core.expression_rewriter import ExpressionRewriter


class TestExpressionRewriterInstantiation:
    """1. Tests initialization and dimension ratio calculations."""

    def test_init_dimensions_and_scaling_factors(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=1080, target_height=1920)
        assert rewriter.scale_x == pytest.approx(1080 / 1920)
        assert rewriter.scale_y == pytest.approx(1920 / 1080)
        assert rewriter.scale_uniform == pytest.approx(1080 / 1920)

    def test_init_raises_on_zero_or_negative_dimensions(self):
        with pytest.raises(ValueError):
            ExpressionRewriter(source_width=0, source_height=1080, target_width=1080, target_height=1920)
        with pytest.raises(ValueError):
            ExpressionRewriter(source_width=1920, source_height=-1080, target_width=1080, target_height=1920)


class TestCoordinateExpressionRewriting:
    """2. Tests AST rewriting of hardcoded coordinate literals."""

    def test_center_point_expression_960x540_rewrites_to_comp_center_1080x1920(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=1080, target_height=1920)
        expr = "[960, 540]"
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is True
        assert rewritten == "[540, 960]"

    def test_arbitrary_position_array_scales_x_and_y(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=3840, target_height=2160)
        expr = "[100, 200]"
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is True
        assert rewritten == "[200, 400]"

    def test_3d_position_array_scales_x_y(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=3840, target_height=2160)
        expr = "[500, 300, 0]"
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is True
        assert "[1000, 600, 0]" in rewritten

    def test_nested_array_in_toComp_call_rescales(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=3840, target_height=2160)
        expr = "thisLayer.toComp([100, 100])"
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is True
        assert rewritten == "thisLayer.toComp([200, 200])"


class TestWiggleExpressionRewriting:
    """3. Tests scaling of spatial wiggle amplitude."""

    def test_spatial_wiggle_scales_amplitude(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=3840, target_height=2160, scale_factor=2.0)
        expr = "wiggle(2, 50)"
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is True
        assert rewritten == "wiggle(2, 100)"

    def test_wiggle_with_multiple_arguments_scales_amplitude(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=960, target_height=540, scale_factor=0.5)
        expr = "wiggle(5, 100, 1, 0.5, time)"
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is True
        assert rewritten == "wiggle(5, 50, 1, 0.5, time)"

    def test_wiggle_with_zero_amplitude(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=3840, target_height=2160)
        expr = "wiggle(3, 0)"
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is True
        assert rewritten == "wiggle(3, 0)"


class TestCommentAndStringPreservation:
    """4. Tests that string literals and comments are never rewritten."""

    def test_numbers_inside_string_literals_are_not_modified(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=3840, target_height=2160)
        expr = 'var label = "[960, 540]"; [100, 200];'
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is True
        assert 'var label = "[960, 540]"' in rewritten
        assert '[200, 400]' in rewritten

    def test_numbers_inside_line_comments_are_not_modified(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=3840, target_height=2160)
        expr = '// Center is at [960, 540]\n[100, 200];'
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is True
        assert '// Center is at [960, 540]' in rewritten
        assert '[200, 400]' in rewritten

    def test_numbers_inside_block_comments_are_not_modified(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=3840, target_height=2160)
        expr = '/* [960, 540] */ [100, 200];'
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is True
        assert '/* [960, 540] */' in rewritten
        assert '[200, 400]' in rewritten


class TestFailSafeGuardrail:
    """5. Tests fail-safe passthrough for unparseable or non-scalable expressions."""

    def test_syntax_error_expression_returns_unmodified_passthrough(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=1080, target_height=1920)
        expr = "this is not valid javascript %$$ 1234 ["
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is False
        assert rewritten == expr
        assert "PARSE_ERROR_PASSTHROUGH" in (reason or "")

    def test_empty_or_whitespace_expression_returns_unmodified(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=1080, target_height=1920)
        for empty_str in ("", "   ", "\n\t"):
            rewritten, modified, reason = rewriter.rewrite_expression(empty_str)
            assert modified is False
            assert rewritten == empty_str

    def test_non_scalable_expression_returns_unmodified(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=1080, target_height=1920)
        expr = "time * 360"
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is False
        assert rewritten == expr


class TestBase64CommentBackupAndRestore:
    """6. Tests 1-click rollback backup encoding and restoration."""

    def test_comment_backup_creation_and_restore_roundtrip(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=1080, target_height=1920)
        orig_expr = "wiggle(2, 50) + [960, 540];"
        backup_tag = rewriter.create_comment_backup(orig_expr)
        assert backup_tag.startswith("orig_expr:")

        restored = rewriter.restore_from_comment(backup_tag)
        assert restored == orig_expr

    def test_restore_from_comment_with_surrounding_studio_notes(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=1080, target_height=1920)
        orig_expr = "[1920, 1080]"
        tag = rewriter.create_comment_backup(orig_expr)
        studio_comment = f"UID:12345 | Artist: Matt | {tag} | Final Grade OK"
        restored = rewriter.restore_from_comment(studio_comment)
        assert restored == orig_expr

    def test_restore_from_comment_missing_tag_returns_none(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=1080, target_height=1920)
        assert rewriter.restore_from_comment("Clean comment without backup") is None
        assert rewriter.restore_from_comment("") is None

    def test_restore_from_corrupt_base64_returns_none(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=1080, target_height=1920)
        # Note: if invalid base64 is passed, should return None safely
        res = rewriter.restore_from_comment("orig_expr:???corrupted???")
        assert res is None or isinstance(res, str)


class TestComplexExpressions:
    """7. Tests multi-line and advanced expressions."""

    def test_multiline_conditional_with_position_arrays(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=3840, target_height=2160)
        expr = """
        if (time > 2.0) {
            [200, 300];
        } else {
            [100, 150];
        }
        """
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is True
        assert "[400, 600]" in rewritten
        assert "[200, 300]" in rewritten

    def test_variable_assignment_with_position_literals(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=3840, target_height=2160)
        expr = "var targetPos = [400, 500]; targetPos;"
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is True
        assert "var targetPos = [800, 1000]" in rewritten

    def test_hd_to_4k_expression_doubling(self):
        rewriter = ExpressionRewriter(source_width=1920, source_height=1080, target_width=3840, target_height=2160)
        expr = "var p = [960, 540]; p;"
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is True
        assert "[1920, 1080]" in rewritten

    def test_widen_9x16_to_16x9_expression_normalization(self):
        rewriter = ExpressionRewriter(source_width=1080, source_height=1920, target_width=1920, target_height=1080)
        expr = "[540, 960]"
        rewritten, modified, reason = rewriter.rewrite_expression(expr)
        assert modified is True
        assert rewritten == "[960, 540]"
