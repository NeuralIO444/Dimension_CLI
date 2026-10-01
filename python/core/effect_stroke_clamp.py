# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/effect_stroke_clamp.py
Issue #391 -- wires typographic_micro_guard.clamp_stroke_width_px() into a
real conform pass, producing `conformed_layer_styles` for exactly one case:
a BOTTOM/LEGALS-tagged layer's Stroke layer style ("ADBE Stroke Layer
Style" / "ADBE Stroke Size").

Deliberately NOT a general "scale effect/layer-style params by S" pass --
that premise is the one `effect_conformer.py` got wrong and was deleted
for (CLAUDE.md's sharp edge, 2026-08-19). This module never multiplies a
param by any scale factor; it only ever calls the already-safe inverse
floor-clamp in `typographic_micro_guard.py`, and only for this one
matchName. Every other effect/layer-style parameter on every layer is
left completely untouched -- no `conformed_effects` or
`conformed_layer_styles` entry is ever emitted for anything else here.

Static-only: a layer whose Stroke Size is keyframed is skipped entirely
(no clean semantics for "clamp the keyframed value" in this narrow slice).
"""

from __future__ import annotations

from typing import Any, Optional

from core.layer_utils import canon_tag
from core.typographic_micro_guard import clamp_stroke_width_px
from models.conformed_manifest import ConformedEffectParam, ConformedLayerStyle

# AE's real internal matchName for the Stroke layer style is "frameFX"
# (a Photoshop-derived internal name with no visible relationship to the
# UI label "Stroke") -- NOT "ADBE Stroke Layer Style"/"ADBE Stroke Size"
# as core/property_registry.py's (dead, unverified) entry assumed.
# Confirmed live against AE 26.3x87 (2026-09-05): the group's own
# matchName IS "frameFX/enabled" (an AE quirk -- the enable toggle and
# the parameter group share one matchName), and its Size sub-property is
# "frameFX/size". This is exactly the class of mistake CLAUDE.md's
# effect_conformer.py sharp edge warns synthetic-only testing won't
# catch -- do not "correct" these back to the ADBE-prefixed guesses
# without re-verifying live in AE first.
STROKE_LAYER_STYLE_MATCH_NAME = "frameFX/enabled"
STROKE_SIZE_MATCH_NAME = "frameFX/size"
STROKE_SIZE_DISPLAY_NAME = "Size"

# LGL/LEGALS/CTA all normalize to BOTTOM via the tag registry (see CLAUDE.md's
# tag vocabulary table) -- comparing against the canonical id covers all of them.
ELIGIBLE_CANONICAL_TAG = "BOTTOM"


def _find_stroke_size_property(layer: Any) -> Optional[Any]:
    """Return the Stroke layer style's Size PropertyRecord, or None if the
    layer has no enabled Stroke layer style, or that property is missing."""
    layer_styles = getattr(layer, "layer_styles", None) or []
    for style in layer_styles:
        if getattr(style, "match_name", None) != STROKE_LAYER_STYLE_MATCH_NAME:
            continue
        if not getattr(style, "enabled", True):
            continue
        for prop in getattr(style, "properties", None) or []:
            if getattr(prop, "match_name", None) == STROKE_SIZE_MATCH_NAME:
                return prop
    return None


def compute_stroke_clamp_layer_style(
    layer: Any,
    layer_scale_factor: float,
) -> Optional[ConformedLayerStyle]:
    """Compute the (single-entry) conformed_layer_styles payload for one
    layer, or None if this layer isn't eligible or the clamp doesn't need
    to intervene.

    `layer_scale_factor` is THIS layer's own effective transform-scale
    ratio (conformed scale / source scale), matching
    typographic_micro_guard's contract -- not necessarily the comp-wide S,
    since per-tag scale overrides mean layers can end up with different
    effective ratios.
    """
    if canon_tag(getattr(layer, "content_tag", None)) != ELIGIBLE_CANONICAL_TAG:
        return None

    stroke_size_prop = _find_stroke_size_property(layer)
    if stroke_size_prop is None:
        return None

    # Animated Stroke Size has no clean "clamp the keyframed value"
    # semantics in this narrow slice -- skip rather than guess.
    if getattr(stroke_size_prop, "keys", None):
        return None

    raw_width_px = getattr(stroke_size_prop, "static", None)
    if not isinstance(raw_width_px, (int, float)):
        return None

    clamped = clamp_stroke_width_px(float(raw_width_px), layer_scale_factor)
    if clamped == raw_width_px:
        # Pass-through -- ConformedEffectParam docstring: PASS_THROUGH
        # params are omitted entirely, not emitted with an unchanged value.
        return None

    return ConformedLayerStyle(
        match_name=STROKE_LAYER_STYLE_MATCH_NAME,
        display_name="Stroke",
        params=[
            ConformedEffectParam(
                match_name=STROKE_SIZE_MATCH_NAME,
                display_name=STROKE_SIZE_DISPLAY_NAME,
                conformed_static=clamped,
            )
        ],
    )


def layer_scale_factor(source_scale: Optional[list], conformed_scale: Optional[list]) -> Optional[float]:
    """This layer's own effective scale ratio (conformed / source, X axis).

    Returns None when either scale is missing/degenerate -- callers must
    treat None as "cannot compute, skip this layer" rather than guessing
    a default ratio.
    """
    if not source_scale or not conformed_scale:
        return None
    try:
        src_x = float(source_scale[0])
        conf_x = float(conformed_scale[0])
    except (TypeError, IndexError, ValueError):
        return None
    if src_x == 0:
        return None
    return conf_x / src_x
