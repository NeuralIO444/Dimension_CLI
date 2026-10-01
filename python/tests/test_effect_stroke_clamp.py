# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_effect_stroke_clamp.py -- Issue #391.

Covers core.effect_stroke_clamp (pure per-layer computation) and
stages.conform_passes.apply_effect_stroke_clamp_pass (the manifest-level
wiring). Deliberately proves the NEGATIVE cases as hard as the positive
one: this pass must never emit anything for a non-BOTTOM tag, a
non-Stroke effect/layer-style, an animated Stroke Size, or a Stroke Size
that doesn't need clamping -- those are exactly the paths that would
reintroduce the effect_conformer.py double-transform class of bug if
this pass ever grew beyond its narrow scope.
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.effect_stroke_clamp import (
    STROKE_LAYER_STYLE_MATCH_NAME,
    STROKE_SIZE_MATCH_NAME,
    compute_stroke_clamp_layer_style,
    layer_scale_factor,
)
from stages.conform_passes import apply_effect_stroke_clamp_pass


class TestRealAeMatchNames:
    """Regression guard: confirmed live against AE 26.3x87 (2026-09-05,
    issue #391) that the Stroke layer style's real matchNames are
    "frameFX/enabled" (group) and "frameFX/size" (the width param) --
    NOT the "ADBE Stroke Layer Style"/"ADBE Stroke Size" names every
    other reference in this codebase (property_registry.py,
    match_name_registry.py, SovCore_Classify.jsx) assumed without ever
    verifying against real AE. Do not "fix" these back without
    re-confirming live in AE first -- see effect_stroke_clamp.py's
    module comment for the full account."""

    def test_stroke_layer_style_match_name_is_framefx_not_adbe(self):
        assert STROKE_LAYER_STYLE_MATCH_NAME == "frameFX/enabled"

    def test_stroke_size_match_name_is_framefx_not_adbe(self):
        assert STROKE_SIZE_MATCH_NAME == "frameFX/size"


def _prop(match_name, static=None, keys=None):
    return types.SimpleNamespace(match_name=match_name, static=static, keys=keys)


def _stroke_style(size_static=2.0, size_keys=None, enabled=True):
    # Real AE matchName confirmed live (2026-09-05, AE 26.3x87): the
    # Stroke layer style's group AND its enable toggle share the
    # matchName "frameFX/enabled" -- NOT "ADBE Stroke Layer Style". See
    # core/effect_stroke_clamp.py's module comment.
    return types.SimpleNamespace(
        match_name="frameFX/enabled",
        enabled=enabled,
        properties=[_prop("frameFX/size", static=size_static, keys=size_keys)],
    )


def _layer(tag="BOTTOM", layer_styles=None, scale=None):
    return types.SimpleNamespace(
        content_tag=tag,
        layer_styles=layer_styles if layer_styles is not None else [_stroke_style()],
        scale=scale if scale is not None else [100.0, 100.0, 100.0],
        index=1,
    )


class TestLayerScaleFactor:
    def test_computes_ratio_from_x_axis(self):
        assert layer_scale_factor([100.0, 100.0, 100.0], [56.25, 56.25, 100.0]) == 0.5625

    def test_missing_source_scale_returns_none(self):
        assert layer_scale_factor(None, [50.0, 50.0, 100.0]) is None

    def test_missing_conformed_scale_returns_none(self):
        assert layer_scale_factor([100.0, 100.0, 100.0], None) is None

    def test_zero_source_scale_returns_none_not_divide_by_zero(self):
        assert layer_scale_factor([0.0, 0.0, 100.0], [50.0, 50.0, 100.0]) is None


class TestComputeStrokeClampLayerStyle:
    def test_bottom_tag_with_shrinking_stroke_gets_clamped(self):
        layer = _layer(tag="BOTTOM", layer_styles=[_stroke_style(size_static=2.0)])
        result = compute_stroke_clamp_layer_style(layer, layer_scale_factor=0.1)
        assert result is not None
        assert result.match_name == "frameFX/enabled"
        assert result.params[0].match_name == "frameFX/size"
        assert result.params[0].conformed_static == 10.0  # 1.0 / 0.1

    def test_legals_alias_normalizes_to_bottom_and_is_eligible(self):
        layer = _layer(tag="LEGALS", layer_styles=[_stroke_style(size_static=2.0)])
        result = compute_stroke_clamp_layer_style(layer, layer_scale_factor=0.1)
        assert result is not None

    def test_non_bottom_tag_never_emits(self):
        for tag in ("CENTER", "TOP", "FILL", None, ""):
            layer = _layer(tag=tag, layer_styles=[_stroke_style(size_static=2.0)])
            assert compute_stroke_clamp_layer_style(layer, layer_scale_factor=0.1) is None

    def test_no_stroke_layer_style_never_emits(self):
        layer = _layer(tag="BOTTOM", layer_styles=[])
        assert compute_stroke_clamp_layer_style(layer, layer_scale_factor=0.1) is None

    def test_disabled_stroke_layer_style_never_emits(self):
        layer = _layer(tag="BOTTOM", layer_styles=[_stroke_style(size_static=2.0, enabled=False)])
        assert compute_stroke_clamp_layer_style(layer, layer_scale_factor=0.1) is None

    def test_animated_stroke_size_never_emits(self):
        layer = _layer(
            tag="BOTTOM",
            layer_styles=[_stroke_style(size_static=2.0, size_keys={"times": [0], "values": [2.0]})],
        )
        assert compute_stroke_clamp_layer_style(layer, layer_scale_factor=0.1) is None

    def test_already_safe_width_never_emits(self):
        # 2px raw at 0.5 scale = 1.0px effective, already at the floor.
        layer = _layer(tag="BOTTOM", layer_styles=[_stroke_style(size_static=2.0)])
        assert compute_stroke_clamp_layer_style(layer, layer_scale_factor=0.5) is None

    def test_other_effect_matchnames_are_ignored(self):
        """A layer with some other layer style (not Stroke) must never
        produce a conformed_layer_styles entry, however it's tagged."""
        other_style = types.SimpleNamespace(
            match_name="ADBE Drop Shadow Layer Style",
            enabled=True,
            properties=[_prop("ADBE Drop Shadow Distance", static=50.0)],
        )
        layer = _layer(tag="BOTTOM", layer_styles=[other_style])
        assert compute_stroke_clamp_layer_style(layer, layer_scale_factor=0.1) is None

    def test_non_numeric_static_never_emits(self):
        layer = _layer(tag="BOTTOM", layer_styles=[_stroke_style(size_static=None)])
        assert compute_stroke_clamp_layer_style(layer, layer_scale_factor=0.1) is None


class TestApplyEffectStrokeClampPass:
    def _manifest(self, layers):
        return types.SimpleNamespace(layers=layers)

    def test_wires_conformed_layer_styles_onto_result(self):
        manifest = self._manifest([_layer(tag="BOTTOM", layer_styles=[_stroke_style(size_static=2.0)])])
        result = {"layers": [{"conformed_transforms": {"scale": [10.0, 10.0, 100.0]}}]}
        count = apply_effect_stroke_clamp_pass(manifest, result)
        assert count == 1
        styles = result["layers"][0]["conformed_layer_styles"]
        assert len(styles) == 1
        assert styles[0]["match_name"] == "frameFX/enabled"
        assert styles[0]["params"][0]["conformed_static"] == 10.0  # 1.0 / 0.1

    def test_non_eligible_layer_gets_no_field_at_all(self):
        manifest = self._manifest([_layer(tag="CENTER", layer_styles=[_stroke_style(size_static=2.0)])])
        result = {"layers": [{"conformed_transforms": {"scale": [10.0, 10.0, 100.0]}}]}
        count = apply_effect_stroke_clamp_pass(manifest, result)
        assert count == 0
        assert "conformed_layer_styles" not in result["layers"][0]

    def test_missing_conformed_transforms_skips_without_crashing(self):
        manifest = self._manifest([_layer(tag="BOTTOM")])
        result = {"layers": [{}]}
        count = apply_effect_stroke_clamp_pass(manifest, result)
        assert count == 0

    def test_multiple_layers_only_clamps_the_eligible_one(self):
        manifest = self._manifest([
            _layer(tag="BOTTOM", layer_styles=[_stroke_style(size_static=2.0)]),
            _layer(tag="CENTER", layer_styles=[_stroke_style(size_static=2.0)]),
            _layer(tag="BOTTOM", layer_styles=[]),  # no stroke style
        ])
        result = {"layers": [
            {"conformed_transforms": {"scale": [10.0, 10.0, 100.0]}},
            {"conformed_transforms": {"scale": [10.0, 10.0, 100.0]}},
            {"conformed_transforms": {"scale": [10.0, 10.0, 100.0]}},
        ]}
        count = apply_effect_stroke_clamp_pass(manifest, result)
        assert count == 1
        assert "conformed_layer_styles" in result["layers"][0]
        assert "conformed_layer_styles" not in result["layers"][1]
        assert "conformed_layer_styles" not in result["layers"][2]

    def test_never_touches_a_spatial_effect_param(self):
        """Sanity guard against the exact bug class this issue is about:
        even a layer with a spatial effect (CC Lens-style matchName) must
        never get a conformed_effects entry from this pass -- it only
        ever knows about the one Stroke layer-style matchName."""
        layer = _layer(tag="BOTTOM", layer_styles=[_stroke_style(size_static=2.0)])
        layer.effects = [types.SimpleNamespace(
            index=1, match_name="ADBE CC Lens", display_name="CC Lens",
            properties=[_prop("ADBE CC Lens-0001", static=[936.0, 500.0])],
        )]
        manifest = self._manifest([layer])
        result = {"layers": [{"conformed_transforms": {"scale": [10.0, 10.0, 100.0]}}]}
        apply_effect_stroke_clamp_pass(manifest, result)
        assert "conformed_effects" not in result["layers"][0]
