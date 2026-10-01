# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/expression_parser.py
AST-based AE Expression Parser and Scaler.

Replaces the simple regex-based scaling in stages/expression.py with a
robust Abstract Syntax Tree (AST) parser. This allows for more intelligent
and safer scaling of numeric literals within expressions.

Requires `esprima-python`. Add to requirements.txt.
"""

from __future__ import annotations

import esprima  # type: ignore


class ExpressionScaler:
    """
    Scales numeric literals in AE expressions using an AST.
    This provides a more robust alternative to regex-based scaling by
    understanding the code structure.
    """

    def __init__(self, source: str, s_factor: float):
        self.source = source
        self.s_factor = s_factor
        self.replacements = []

    def scale(self) -> str:
        """
        Parses the expression, finds scalable literals within array
        expressions, and returns the modified expression string.
        """
        if not self.source or self.s_factor == 1.0:
            return self.source

        try:
            # The `range` option is crucial for getting source locations.
            tree = esprima.parseScript(self.source, options={'range': True})
        except Exception:
            # If esprima fails to parse, fall back to the original expression.
            # This is safer than breaking the conform on a complex expression.
            return self.source

        visitor = _NumericLiteralVisitor(self.s_factor)
        visitor.visit(tree)
        self.replacements = visitor.replacements

        # Apply replacements in reverse order to avoid index shifting issues.
        source_list = list(self.source)
        for start, end, new_value in sorted(self.replacements, key=lambda x: x[0], reverse=True):
            source_list[start:end] = new_value

        return "".join(source_list)


class _NumericLiteralVisitor(esprima.NodeVisitor):
    """
    An esprima.NodeVisitor that finds numeric literals inside ArrayExpressions
    and records the necessary replacements to scale them.

    Safety rule — only scale an ArrayExpression when it is the **direct** child
    of a top-level ExpressionStatement.  Arrays embedded inside binary
    expressions (`value + [10, 0]`), call arguments (`wiggle(5, [10, 20])`),
    assignments (`x = [10, 20]`), or any other compound node are left
    untouched.  This matches AE's dominant expression patterns where a
    stand-alone `[x, y]` or `[x, y, z]` is a spatial coordinate literal.
    """

    def __init__(self, s_factor: float):
        self.s_factor = s_factor
        self.replacements = []
        # Set of node IDs (id()) we are allowed to scale.
        self._scalable_array_ids: set = set()

    # ── first pass: identify which ArrayExpression nodes are top-level ──────

    def _mark_scalable_arrays(self, tree) -> None:
        """Walk the top-level statement list and mark qualifying arrays."""
        body = getattr(tree, "body", []) or []
        for stmt in body:
            if stmt.type != "ExpressionStatement":
                continue
            expr = stmt.expression
            # Case 1: bare `[x, y]`
            if expr.type == "ArrayExpression":
                self._scalable_array_ids.add(id(expr))
            # Case 2: `temp = [x, y]` (simple variable assignment whose RHS
            #         is a bare array — common in AE wiggle helpers).
            elif (
                expr.type == "AssignmentExpression"
                and expr.right.type == "ArrayExpression"
            ):
                self._scalable_array_ids.add(id(expr.right))
            # All other patterns (binary ops, call results, ternary …)
            # are intentionally excluded.

    # ── NodeVisitor entry point ──────────────────────────────────────────────

    def visit(self, tree) -> None:
        self._mark_scalable_arrays(tree)
        super().visit(tree)

    # ── visitor callback ─────────────────────────────────────────────────────

    def visit_ArrayExpression(self, node):
        """Called for each `[ ... ]` array expression in the AST."""
        if id(node) not in self._scalable_array_ids:
            # Not a top-level bare array — skip entirely.
            return

        for element in node.elements:
            # Scale simple positive numbers.
            if element and element.type == "Literal" and isinstance(element.value, (int, float)):
                self._record_replacement(element)
            # Negative numbers parse as UnaryExpression('-', Literal) —
            # skipping them would leave the array partially scaled.
            elif (
                element
                and element.type == "UnaryExpression"
                and element.operator == "-"
                and element.argument.type == "Literal"
                and isinstance(element.argument.value, (int, float))
            ):
                self._record_replacement(element, negate=True)

        # Do NOT call generic_visit — we only care about the top-level arrays.

    def _record_replacement(self, literal_node, negate=False):
        """
        Calculates the scaled value and records the replacement details.

        `negate=True` when the node is a UnaryExpression minus wrapping a
        Literal; the replacement then spans the sign and the number.
        """
        original_value = literal_node.argument.value if negate else literal_node.value
        if negate:
            original_value = -original_value
        scaled_value = original_value * self.s_factor

        # Format to 2 decimal places to keep expressions clean.
        new_value_str = f"{scaled_value:.2f}"

        # The `range` property gives the [start, end] character indices
        # of the node in the original source string.
        start_index, end_index = literal_node.range
        
        self.replacements.append((start_index, end_index, new_value_str))