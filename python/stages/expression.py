# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Stage: Expression Scaling

Scales numeric literals within AE expressions, respecting sealed units.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.scale_engine import ScaleEngine

def scale_expressions(conformed_result: dict, engine: ScaleEngine) -> dict:
    # Gated off by default pending real-AE verification on the Parallax
    # project; expression params are the same "looks plausibly scaled"
    # trap class as effect params (see effect_conformer sharp edge).
    try:
        from logic.preferences_state import preferences as _prefs
        if not bool(getattr(_prefs, "expression_scaling_enabled", False)):
            return conformed_result
    except Exception:
        return conformed_result

    # S must come from the result dict already in hand — calling
    # engine.conform() here re-runs the entire engine per conform.
    s_factor = (conformed_result.get("scale") or {}).get("S", 1.0)
    if s_factor == 1.0:
        return conformed_result

    from core.expression_parser import ExpressionScaler

    for layer in conformed_result.get("layers", []):
        layer_key = (layer.get("containing_comp_id"), layer.get("index"))
        if layer_key[0] in engine.sealed_precomp_cids:
            continue

        if layer.get("expressions"):
            conformed_expressions = {}
            for prop, expr_str in layer["expressions"].items():
                if expr_str:
                    conformed_expressions[prop] = ExpressionScaler(expr_str, s_factor).scale()
            if conformed_expressions:
                layer["conformed_expressions"] = conformed_expressions

    return conformed_result