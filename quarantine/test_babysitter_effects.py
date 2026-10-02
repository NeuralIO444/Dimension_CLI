# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_babysitter_effects.py — Slot 12.5 Stage E.

Static contract tests for the effects-conform path in
`Scripts/Dimension_Assets/Babysitter.jsx`:
  - _processLayerEffects exists and takes two args
  - PropertyWriter has 'effect' and 'layer_style' strategies
  - All new code is ES3 compatible
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


class TestBabysitterEffectsStructure:
    def test_process_layer_effects_exists(self):
        src = _read()
        assert "_processLayerEffects: function(cLayer, aeLayer)" in src, (
            "_processLayerEffects helper must exist in Babysitter.jsx"
        )

    def test_process_layer_effects_calls_dispatch(self):
        body = _extract_function(_read(), "_processLayerEffects: function")
        assert "this.PropertyWriter.dispatch(" in body, (
            "_processLayerEffects must dispatch via PropertyWriter strategy."
        )

    def test_property_writer_strategies_exist(self):
        src = _read()
        assert "effect: {" in src, (
            "PropertyWriter must have 'effect' strategy."
        )
        assert "layer_style: {" in src, (
            "PropertyWriter must have 'layer_style' strategy."
        )

    def test_effect_strategy_details(self):
        body = _extract_function(_read(), "effect: {")
        assert "ADBE Effect Parade" in body, (
            "effect strategy must reference 'ADBE Effect Parade'."
        )
        assert "cEff.match_name" in body, (
            "effect strategy must verify effect matchName."
        )
        assert "cParam.conformed_static" in body, (
            "effect strategy must support conformed_static values."
        )
        assert "cParam.conformed_keys" in body, (
            "effect strategy must support conformed_keys."
        )

    def test_layer_style_strategy_details(self):
        body = _extract_function(_read(), "layer_style: {")
        assert "ADBE Layer Styles" in body, (
            "layer_style strategy must reference 'ADBE Layer Styles'."
        )
        assert "cParam.conformed_static" in body, (
            "layer_style strategy must support conformed_static values."
        )
        assert "cParam.conformed_keys" in body, (
            "layer_style strategy must support conformed_keys."
        )

    def test_pump_calls_process_layer_effects(self):
        src = _read()
        assert "this._processLayerEffects(cLayer, aeLayer);" in src, (
            "Babysitter._pump must call _processLayerEffects inside the layer loop."
        )


class TestES3Compatibility:
    def test_no_arrow_functions(self):
        src = _read()
        # Extract the new functions and verify ES3 compatibility
        fx_body = _extract_function(src, "_processLayerEffects: function")
        eff_body = _extract_function(src, "effect: {")
        style_body = _extract_function(src, "layer_style: {")

        for name, body in [("_processLayerEffects", fx_body), ("effect", eff_body), ("layer_style", style_body)]:
            assert "=>" not in body, f"Arrow function found in {name} - not ES3 compatible."
            assert "`" not in body, f"Template literal found in {name} - not ES3 compatible."
            assert not re.search(r"\blet\s+\w", body), f"`let` declaration found in {name} - use `var`."
            assert not re.search(r"\bconst\s+\w", body), f"`const` declaration found in {name} - use `var`."
