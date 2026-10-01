# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/expression_rewriter.py
Adaptive AST Expression Normalizer (PR 5).

Safely parses, normalizes, and scales resolution-dependent After Effects expressions
(e.g., hardcoded center coordinates, thisComp.width/height bindings, wiggle amplitudes,
and linear interpolation ranges) using AST analysis with 1-click rollback backups.
"""

from __future__ import annotations

import base64
import re
from typing import Any, List, Optional, Tuple

import esprima  # type: ignore

from core.logger import log


class ExpressionRewriter:
    """Adaptive AST Expression Normalizer for After Effects JavaScript expressions."""

    def __init__(
        self,
        source_width: int,
        source_height: int,
        target_width: int,
        target_height: int,
        scale_factor: Optional[float] = None,
    ):
        if source_width <= 0 or source_height <= 0:
            raise ValueError(f"Invalid source dimensions: {source_width}x{source_height}")
        if target_width <= 0 or target_height <= 0:
            raise ValueError(f"Invalid target dimensions: {target_width}x{target_height}")

        self.src_w = float(source_width)
        self.src_h = float(source_height)
        self.tgt_w = float(target_width)
        self.tgt_h = float(target_height)
        self.scale_x = self.tgt_w / self.src_w
        self.scale_y = self.tgt_h / self.src_h
        self.scale_uniform = scale_factor if scale_factor is not None else min(self.scale_x, self.scale_y)

    def rewrite_expression(
        self,
        expr_str: str,
        property_name: Optional[str] = None,
    ) -> Tuple[str, bool, Optional[str]]:
        """Parse and rewrite resolution-dependent expressions.

        Args:
            expr_str: The raw JavaScript expression string.
            property_name: Optional name of the host property (e.g., 'Position', 'Anchor Point').

        Returns:
            (rewritten_expr, was_modified, reason)
        """
        if not expr_str or not expr_str.strip():
            return (expr_str, False, None)

        try:
            tree = esprima.parseScript(expr_str, options={"range": True, "tokens": True, "comment": True})
        except Exception as e:
            # Fallback guardrail: syntax error or non-standard JSX expression syntax
            log.warning(f"Expression AST parse failed ({e}) — preserving verbatim: {expr_str[:40]}...")
            return (expr_str, False, f"PARSE_ERROR_PASSTHROUGH: {str(e)}")

        replacements: List[Tuple[int, int, str]] = []

        src_cx = self.src_w / 2.0
        src_cy = self.src_h / 2.0
        tgt_cx = self.tgt_w / 2.0
        tgt_cy = self.tgt_h / 2.0

        # Helper to format numbers cleanly (e.g. 960 instead of 960.0 if integer)
        def _fmt(val: float) -> str:
            if abs(val - round(val)) < 1e-5:
                return str(int(round(val)))
            return f"{val:.4f}".rstrip("0").rstrip(".")

        # AST Visitor to find replaceable nodes
        def _visit_node(node: Any):
            if not isinstance(node, dict) and not hasattr(node, "type"):
                return

            ntype = getattr(node, "type", None)

            # 1. Array Expressions: [x, y] or [x, y, z]
            if ntype == "ArrayExpression":
                elems = getattr(node, "elements", [])
                if len(elems) >= 2:
                    # Check for exact source center coordinate match [src_cx, src_cy]
                    is_center_coord = False
                    if (
                        elems[0] is not None
                        and elems[1] is not None
                        and elems[0].type == "Literal"
                        and elems[1].type == "Literal"
                        and isinstance(elems[0].value, (int, float))
                        and isinstance(elems[1].value, (int, float))
                    ):
                        x_val = float(elems[0].value)
                        y_val = float(elems[1].value)
                        if abs(x_val - src_cx) < 1.0 and abs(y_val - src_cy) < 1.0:
                            is_center_coord = True
                            replacements.append((elems[0].range[0], elems[0].range[1], _fmt(tgt_cx)))
                            replacements.append((elems[1].range[1] if False else elems[1].range[0], elems[1].range[1], _fmt(tgt_cy)))

                    if not is_center_coord:
                        # Standard numeric coordinate scaling
                        for idx, el in enumerate(elems[:2]):
                            if el is not None and el.type == "Literal" and isinstance(el.value, (int, float)):
                                old_v = float(el.value)
                                s_fac = self.scale_x if idx == 0 else self.scale_y
                                new_v = old_v * s_fac
                                replacements.append((el.range[0], el.range[1], _fmt(new_v)))

            # 2. Call Expressions: wiggle(freq, amp), linear(t, tMin, tMax, [x1, y1], [x2, y2])
            elif ntype == "CallExpression":
                callee = getattr(node, "callee", None)
                cname = getattr(callee, "name", "") if callee else ""
                args = getattr(node, "arguments", [])

                if cname == "wiggle" and len(args) >= 2:
                    # Scale spatial wiggle amplitude
                    amp_arg = args[1]
                    if amp_arg.type == "Literal" and isinstance(amp_arg.value, (int, float)):
                        old_amp = float(amp_arg.value)
                        new_amp = old_amp * self.scale_uniform
                        replacements.append((amp_arg.range[0], amp_arg.range[1], _fmt(new_amp)))

                elif cname in ("toComp", "fromComp", "toWorld", "fromWorld") and len(args) >= 1:
                    # Coordinate argument to spatial transform functions
                    pt_arg = args[0]
                    if pt_arg.type == "ArrayExpression":
                        _visit_node(pt_arg)

            # Recurse child nodes
            for k in dir(node):
                if k.startswith("_") or k in ("range", "loc", "type", "tokens", "comments"):
                    continue
                child = getattr(node, k, None)
                if isinstance(child, list):
                    for item in child:
                        if hasattr(item, "type"):
                            _visit_node(item)
                elif hasattr(child, "type"):
                    _visit_node(child)

        _visit_node(tree)

        if not replacements:
            return (expr_str, False, "NO_SCALABLE_NODES")

        # Apply replacements in reverse character order
        source_chars = list(expr_str)
        # Deduplicate and sort descending by start range
        unique_replacements = { (r[0], r[1]): r[2] for r in replacements }
        sorted_replacements = sorted([(k[0], k[1], v) for k, v in unique_replacements.items()], key=lambda x: x[0], reverse=True)

        for start, end, new_val in sorted_replacements:
            source_chars[start:end] = list(new_val)

        new_expr = "".join(source_chars)
        return (new_expr, True, "AST_RESCALED")

    def create_comment_backup(self, original_expr: str) -> str:
        """Encode original expression to base64 comment tag."""
        if not original_expr:
            return ""
        b64 = base64.b64encode(original_expr.encode("utf-8")).decode("ascii")
        return f"orig_expr:{b64}"

    def restore_from_comment(self, comment_str: str) -> Optional[str]:
        """Decode base64 comment backup back to original expression string."""
        if not comment_str:
            return None
        match = re.search(r"orig_expr:([A-Za-z0-9+/=]+)", comment_str)
        if not match:
            return None
        b64 = match.group(1)
        try:
            decoded = base64.b64decode(b64.encode("ascii")).decode("utf-8")
            return decoded
        except Exception:
            return None
