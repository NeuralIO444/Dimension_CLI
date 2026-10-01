# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/match_name_registry.py
Dimension Engine — AE matchName → Category / ValueKind / ScaleRule

matchNames are the stable, locale-independent identifiers AE exposes for
every property (`property.matchName`). They survive AE version bumps and
do not depend on UI language — which is why this registry is keyed on
matchName, not display name.

This file is the SINGLE SOURCE OF TRUTH. The JSX side (SovCore_Classify)
mirrors the same data. A unit test verifies the two stay in sync by
comparing an exported JSON to the embedded JSX table.

Structure:
  - TRANSFORM ─ core transform group properties (position, scale, etc.)
  - CAMERA    ─ camera-specific options
  - LIGHT     ─ light-specific options
  - EFFECTS   ─ known spatial / scale parameters on built-in effects
  - MASKS     ─ mask group and params
  - STYLES    ─ layer styles
  - CONTAINERS ─ group containers (effects parade, masks parade, etc.)

Unknown matchNames fall through to Category.UNKNOWN / ScaleRule.PASS_THROUGH.
The scraper still captures them (nothing is dropped) but they don't get
mathematically transformed — they pass through to the injector unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.property_registry import ScaleRule
from models.scraper_v5 import Category, ValueKind  # noqa: F401 — re-exported for JSX drift test


@dataclass(frozen=True)
class MatchNameEntry:
    match_name: str
    display_name: str           # informational — English default
    category: Category
    value_kind: ValueKind
    scale_rule: ScaleRule
    separable: bool = False     # true for position/scale/anchor
    notes: str = ""


# ══════════════════════════════════════════════════════════════════════════
#  TRANSFORM GROUP
# ══════════════════════════════════════════════════════════════════════════

_TRANSFORM: List[MatchNameEntry] = [
    # Container
    MatchNameEntry("ADBE Transform Group", "Transform",
                   Category.META, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),

    # Core transform properties
    MatchNameEntry("ADBE Anchor Point", "Anchor Point",
                   Category.TRANSFORM, ValueKind.VEC3, ScaleRule.PASS_THROUGH,
                   separable=True,
                   notes="Same matchName is used for Camera's Point of Interest "
                         "on the camera transform group — disambiguate by "
                         "parent layer kind."),
    MatchNameEntry("ADBE Position", "Position",
                   Category.TRANSFORM, ValueKind.VEC3,
                   ScaleRule.ROOT_CENTER_REMAP_XY_SCALE_Z,
                   separable=True),

    # Separated-dimension axes
    MatchNameEntry("ADBE Position_0", "X Position",
                   Category.TRANSFORM_AXIS, ValueKind.SCALAR,
                   ScaleRule.ROOT_REMAP_AXIS_X),
    MatchNameEntry("ADBE Position_1", "Y Position",
                   Category.TRANSFORM_AXIS, ValueKind.SCALAR,
                   ScaleRule.ROOT_REMAP_AXIS_Y),
    MatchNameEntry("ADBE Position_2", "Z Position",
                   Category.TRANSFORM_AXIS, ValueKind.SCALAR,
                   ScaleRule.ROOT_SCALE_AXIS_Z),

    MatchNameEntry("ADBE Scale", "Scale",
                   Category.TRANSFORM, ValueKind.VEC3,
                   ScaleRule.ROOT_MULTIPLY_BY_S, separable=True),

    MatchNameEntry("ADBE Scale_0", "X Scale",
                   Category.TRANSFORM_AXIS, ValueKind.SCALAR,
                   ScaleRule.ROOT_MULTIPLY_BY_S),
    MatchNameEntry("ADBE Scale_1", "Y Scale",
                   Category.TRANSFORM_AXIS, ValueKind.SCALAR,
                   ScaleRule.ROOT_MULTIPLY_BY_S),
    MatchNameEntry("ADBE Scale_2", "Z Scale",
                   Category.TRANSFORM_AXIS, ValueKind.SCALAR,
                   ScaleRule.ROOT_MULTIPLY_BY_S),

    MatchNameEntry("ADBE Orientation", "Orientation",
                   Category.TRANSFORM_3D, ValueKind.VEC3, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Rotate X", "X Rotation",
                   Category.TRANSFORM_3D, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Rotate Y", "Y Rotation",
                   Category.TRANSFORM_3D, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Rotate Z", "Rotation",
                   Category.TRANSFORM, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Opacity", "Opacity",
                   Category.TRANSFORM, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
]


# ══════════════════════════════════════════════════════════════════════════
#  CAMERA OPTIONS
# ══════════════════════════════════════════════════════════════════════════

_CAMERA: List[MatchNameEntry] = [
    MatchNameEntry("ADBE Camera Options Group", "Camera Options",
                   Category.META, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),

    MatchNameEntry("ADBE Camera Zoom", "Zoom",
                   Category.CAMERA_INTRINSIC, ValueKind.SCALAR,
                   ScaleRule.CAMERA_INTRINSIC_SCALE),
    MatchNameEntry("ADBE Camera Depth of Field", "Depth of Field",
                   Category.CAMERA_INTRINSIC, ValueKind.BOOL,
                   ScaleRule.BOOLEAN_PASS_THROUGH),
    MatchNameEntry("ADBE Camera Focus Distance", "Focus Distance",
                   Category.CAMERA_INTRINSIC, ValueKind.SCALAR,
                   ScaleRule.CAMERA_INTRINSIC_SCALE),
    MatchNameEntry("ADBE Camera Aperture", "Aperture",
                   Category.CAMERA_INTRINSIC, ValueKind.SCALAR,
                   ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Camera Blur Level", "Blur Level",
                   Category.CAMERA_INTRINSIC, ValueKind.SCALAR,
                   ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Iris Shape", "Iris Shape",
                   Category.CAMERA_INTRINSIC, ValueKind.ENUM, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Iris Rotation", "Iris Rotation",
                   Category.CAMERA_INTRINSIC, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Iris Roundness", "Iris Roundness",
                   Category.CAMERA_INTRINSIC, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Iris Aspect Ratio", "Iris Aspect Ratio",
                   Category.CAMERA_INTRINSIC, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Iris Diffraction Fringe", "Iris Diffraction Fringe",
                   Category.CAMERA_INTRINSIC, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Iris Highlight Gain", "Highlight Gain",
                   Category.CAMERA_INTRINSIC, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Iris Highlight Threshold", "Highlight Threshold",
                   Category.CAMERA_INTRINSIC, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Iris Highlight Saturation", "Highlight Saturation",
                   Category.CAMERA_INTRINSIC, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
]


# ══════════════════════════════════════════════════════════════════════════
#  LIGHT OPTIONS
# ══════════════════════════════════════════════════════════════════════════

_LIGHT: List[MatchNameEntry] = [
    MatchNameEntry("ADBE Light Options Group", "Light Options",
                   Category.META, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),

    MatchNameEntry("ADBE Light Intensity", "Intensity",
                   Category.LIGHT_INTRINSIC, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    # Some AE versions emit "ADBE Light Intensity 2"; include both.
    MatchNameEntry("ADBE Light Intensity 2", "Intensity",
                   Category.LIGHT_INTRINSIC, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Light Color", "Color",
                   Category.LIGHT_INTRINSIC, ValueKind.COLOR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Light Cone Angle", "Cone Angle",
                   Category.LIGHT_INTRINSIC, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Light Cone Angle 2", "Cone Angle",
                   Category.LIGHT_INTRINSIC, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Light Cone Feather", "Cone Feather",
                   Category.LIGHT_INTRINSIC, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Light Cone Feather 2", "Cone Feather",
                   Category.LIGHT_INTRINSIC, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Light Falloff Type", "Falloff Type",
                   Category.LIGHT_INTRINSIC, ValueKind.ENUM, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Light Falloff Start", "Falloff Start",
                   Category.LIGHT_INTRINSIC, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S,
                   notes="World-space distance — must scale by S."),
    MatchNameEntry("ADBE Light Falloff Distance", "Falloff Distance",
                   Category.LIGHT_INTRINSIC, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S,
                   notes="World-space distance — must scale by S."),
    MatchNameEntry("ADBE Light Radius", "Radius",
                   Category.LIGHT_INTRINSIC, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Light Casts Shadows", "Casts Shadows",
                   Category.LIGHT_INTRINSIC, ValueKind.BOOL, ScaleRule.BOOLEAN_PASS_THROUGH),
    MatchNameEntry("ADBE Light Shadow Darkness", "Shadow Darkness",
                   Category.LIGHT_INTRINSIC, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Light Shadow Diffusion", "Shadow Diffusion",
                   Category.LIGHT_INTRINSIC, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
]


# ══════════════════════════════════════════════════════════════════════════
#  MASKS
# ══════════════════════════════════════════════════════════════════════════

_MASKS: List[MatchNameEntry] = [
    MatchNameEntry("ADBE Mask Parade", "Masks",
                   Category.META, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Mask Atom", "Mask",
                   Category.MASK_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Mask Shape", "Mask Path",
                   Category.MASK_SHAPE, ValueKind.SHAPE, ScaleRule.PASS_THROUGH,
                   notes="Vertices are in layer-local pixel space. Root-layer "
                         "masks on conformed layers need per-vertex center-remap "
                         "in a future mask-aware engine pass."),
    MatchNameEntry("ADBE Mask Feather", "Mask Feather",
                   Category.MASK_PARAM, ValueKind.VEC2, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Mask Opacity", "Mask Opacity",
                   Category.MASK_PARAM, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Mask Offset", "Mask Expansion",
                   Category.MASK_PARAM, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
]


# ══════════════════════════════════════════════════════════════════════════
#  EFFECTS — known spatial / scale parameters on common built-in effects.
#
#  This is the table of "effects with pixel-space parameters" that must
#  scale when a comp is conformed. Add entries here as new effects are
#  encountered. Unknown params pass through unchanged (safe default).
# ══════════════════════════════════════════════════════════════════════════

_EFFECTS_CONTAINERS: List[MatchNameEntry] = [
    MatchNameEntry("ADBE Effect Parade", "Effects",
                   Category.META, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
]

_EFFECTS_SPATIAL: List[MatchNameEntry] = [
    # Drop Shadow (built-in)
    MatchNameEntry("ADBE Drop Shadow", "Drop Shadow",
                   Category.EFFECT_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Drop Shadow-0001", "Shadow Color",
                   Category.EFFECT_PARAM, ValueKind.COLOR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Drop Shadow-0002", "Opacity",
                   Category.EFFECT_PARAM, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Drop Shadow-0003", "Direction",
                   Category.EFFECT_PARAM, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Drop Shadow-0004", "Distance",
                   Category.EFFECT_SCALE, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Drop Shadow-0005", "Softness",
                   Category.EFFECT_SCALE, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),

    # Fast Box Blur / Gaussian Blur (built-in) — blurriness is in pixels
    MatchNameEntry("ADBE Box Blur2", "Fast Box Blur",
                   Category.EFFECT_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Gaussian Blur 2", "Gaussian Blur",
                   Category.EFFECT_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),

    # CC Particle World — Producer Position in comp pixels
    MatchNameEntry("CC Particle World", "CC Particle World",
                   Category.EFFECT_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),

    # Trapcode Particular — Emitter Position
    MatchNameEntry("Trapcode Particular", "Particular",
                   Category.EFFECT_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),

    # Effect Point controls — any effect param whose type is POINT should
    # be center-remapped. The scraper detects these by parameter type
    # rather than matchName (see SovCore_Value.jsx).
]


# ══════════════════════════════════════════════════════════════════════════
#  LAYER STYLES
# ══════════════════════════════════════════════════════════════════════════

_LAYER_STYLES: List[MatchNameEntry] = [
    MatchNameEntry("ADBE Layer Styles", "Layer Styles",
                   Category.META, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Drop Shadow Layer Style", "Drop Shadow",
                   Category.LAYER_STYLE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Drop Shadow Distance", "Distance",
                   Category.LAYER_STYLE_PARAM, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Drop Shadow Size", "Size",
                   Category.LAYER_STYLE_PARAM, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Drop Shadow Spread", "Spread",
                   Category.LAYER_STYLE_PARAM, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Inner Shadow Layer Style", "Inner Shadow",
                   Category.LAYER_STYLE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Inner Shadow Distance", "Distance",
                   Category.LAYER_STYLE_PARAM, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Inner Shadow Size", "Size",
                   Category.LAYER_STYLE_PARAM, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Stroke Layer Style", "Stroke",
                   Category.LAYER_STYLE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Stroke Size", "Size",
                   Category.LAYER_STYLE_PARAM, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Glow Layer Style", "Outer Glow",
                   Category.LAYER_STYLE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Glow Size", "Size",
                   Category.LAYER_STYLE_PARAM, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Bevel Layer Style", "Bevel & Emboss",
                   Category.LAYER_STYLE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Bevel Size", "Size",
                   Category.LAYER_STYLE_PARAM, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Bevel Soften", "Soften",
                   Category.LAYER_STYLE_PARAM, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
]


# ══════════════════════════════════════════════════════════════════════════
#  SHAPE LAYER CONTENTS
#
#  Shape layers use a deeply nested hierarchy:
#    Root Vectors Group → Group → Shapes (Path / Fill / Stroke / Transform)
#
#  Most shape parameters live in layer-local pixel space, so fill/stroke
#  colors pass through (PASS_THROUGH) but stroke widths, offsets, and
#  feather radii need MULTIPLY_BY_S.  Path vertices need per-vertex
#  center-remap — that's a future shape-aware engine pass; for now they
#  are tagged PASS_THROUGH so they survive the conform unmodified.
# ══════════════════════════════════════════════════════════════════════════

_SHAPE: List[MatchNameEntry] = [
    # Root containers
    MatchNameEntry("ADBE Root Vectors Group", "Contents",
                   Category.SHAPE_CONTENTS, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vectors Group", "Group",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Group Contents", "Contents",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Transform Group", "Transform",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),

    # Group-level transform (separate matchNames from layer transform)
    MatchNameEntry("ADBE Vector Anchor", "Anchor Point",
                   Category.TRANSFORM, ValueKind.VEC2, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Position", "Position",
                   Category.TRANSFORM, ValueKind.VEC2, ScaleRule.PASS_THROUGH,
                   notes="Group-local position — no center remap needed."),
    MatchNameEntry("ADBE Vector Scale", "Scale",
                   Category.TRANSFORM, ValueKind.VEC2, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Skew", "Skew",
                   Category.TRANSFORM, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Skew Axis", "Skew Axis",
                   Category.TRANSFORM, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Rotation", "Rotation",
                   Category.TRANSFORM, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Opacity", "Opacity",
                   Category.TRANSFORM, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),

    # Shape path
    MatchNameEntry("ADBE Vector Shape", "Path",
                   Category.SHAPE_PATH, ValueKind.SHAPE, ScaleRule.PASS_THROUGH,
                   notes="Vertices in layer-local pixels. Future: per-vertex center-remap."),
    MatchNameEntry("ADBE Vector Shape - Rect", "Rectangle Path",
                   Category.SHAPE_PATH, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Shape - Ellipse", "Ellipse Path",
                   Category.SHAPE_PATH, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Shape - Star", "Polystar Path",
                   Category.SHAPE_PATH, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),

    # Rectangle / Ellipse / Star parameters (pixel-space → MULTIPLY_BY_S)
    MatchNameEntry("ADBE Vector Rect Size", "Size",
                   Category.SHAPE_SCALE, ValueKind.VEC2, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Vector Rect Position", "Position",
                   Category.SHAPE_PATH, ValueKind.VEC2, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Rect Roundness", "Roundness",
                   Category.SHAPE_SCALE, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Vector Ellipse Size", "Size",
                   Category.SHAPE_SCALE, ValueKind.VEC2, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Vector Ellipse Position", "Position",
                   Category.SHAPE_PATH, ValueKind.VEC2, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Star Outer Radius", "Outer Radius",
                   Category.SHAPE_SCALE, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Vector Star Inner Radius", "Inner Radius",
                   Category.SHAPE_SCALE, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Vector Star Position", "Position",
                   Category.SHAPE_PATH, ValueKind.VEC2, ScaleRule.PASS_THROUGH),

    # Fill
    MatchNameEntry("ADBE Vector Graphic - Fill", "Fill",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Fill Color", "Color",
                   Category.SHAPE_PATH, ValueKind.COLOR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Fill Opacity", "Opacity",
                   Category.SHAPE_PATH, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Fill Rule", "Fill Rule",
                   Category.SHAPE_PATH, ValueKind.ENUM, ScaleRule.PASS_THROUGH),

    # Stroke
    MatchNameEntry("ADBE Vector Graphic - Stroke", "Stroke",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Stroke Color", "Color",
                   Category.SHAPE_PATH, ValueKind.COLOR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Stroke Opacity", "Opacity",
                   Category.SHAPE_PATH, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Stroke Width", "Stroke Width",
                   Category.SHAPE_SCALE, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S,
                   notes="Pixel-space stroke width — must scale by S."),
    MatchNameEntry("ADBE Vector Stroke Line Cap", "Line Cap",
                   Category.SHAPE_PATH, ValueKind.ENUM, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Stroke Line Join", "Line Join",
                   Category.SHAPE_PATH, ValueKind.ENUM, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Stroke Dashes", "Dashes",
                   Category.SHAPE_SCALE, ValueKind.UNKNOWN, ScaleRule.MULTIPLY_BY_S),

    # Offset / trim / merge
    MatchNameEntry("ADBE Vector Filter - Offset", "Offset Paths",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Offset Amount", "Amount",
                   Category.SHAPE_SCALE, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Vector Filter - Trim", "Trim Paths",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Trim Start", "Start",
                   Category.SHAPE_PATH, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Trim End", "End",
                   Category.SHAPE_PATH, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Trim Offset", "Offset",
                   Category.SHAPE_PATH, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Filter - Merge", "Merge Paths",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Filter - Repeater", "Repeater",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Repeater Transform", "Transform",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Repeater Copies", "Copies",
                   Category.SHAPE_PATH, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Repeater Offset", "Offset",
                   Category.SHAPE_PATH, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),

    # Gradient fill / stroke
    MatchNameEntry("ADBE Vector Graphic - G-Fill", "Gradient Fill",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Graphic - G-Stroke", "Gradient Stroke",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Grad Start Pt", "Start Point",
                   Category.SHAPE_SCALE, ValueKind.VEC2, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Vector Grad End Pt", "End Point",
                   Category.SHAPE_SCALE, ValueKind.VEC2, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Vector Grad Highlight Length", "Highlight Length",
                   Category.SHAPE_PATH, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Grad Colors", "Colors",
                   Category.SHAPE_PATH, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),

    # Pucker/Bloat, Twist, Roughen, Zig-zag (pixel params → scale)
    MatchNameEntry("ADBE Vector Filter - PB", "Pucker & Bloat",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Filter - Roughen", "Roughen Edges",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Filter - Twist", "Twist",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Filter - Zigzag", "Zig Zag",
                   Category.SHAPE_GROUP, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Vector Zigzag Size", "Size",
                   Category.SHAPE_SCALE, ValueKind.SCALAR, ScaleRule.MULTIPLY_BY_S),
    MatchNameEntry("ADBE Vector Zigzag Ridges", "Ridges per Segment",
                   Category.SHAPE_PATH, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
]


# ══════════════════════════════════════════════════════════════════════════
#  TEXT LAYER PROPERTIES
# ══════════════════════════════════════════════════════════════════════════

_TEXT: List[MatchNameEntry] = [
    MatchNameEntry("ADBE Text Properties", "Text",
                   Category.META, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Text Document", "Source Text",
                   Category.TEXT_SOURCE, ValueKind.TEXT, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Text Path Options", "Path Options",
                   Category.META, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Text More Options", "More Options",
                   Category.META, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Text Animators", "Animators",
                   Category.TEXT_ANIMATOR, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Text Animator", "Animator",
                   Category.TEXT_ANIMATOR, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Text Selectors", "Selectors",
                   Category.TEXT_ANIMATOR, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Text Percent Selector", "Range Selector",
                   Category.TEXT_ANIMATOR, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Text Percent Selector Start", "Start",
                   Category.TEXT_ANIMATOR, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Text Percent Selector End", "End",
                   Category.TEXT_ANIMATOR, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Text Animator Properties", "Animator Properties",
                   Category.TEXT_ANIMATOR, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
]


# ══════════════════════════════════════════════════════════════════════════
#  3D MATERIAL OPTIONS
#
#  All material coefficients are dimensionless 0-100 percentages —
#  PASS_THROUGH.  The Casts/Accepts Shadows flags are boolean.
# ══════════════════════════════════════════════════════════════════════════

_MATERIAL: List[MatchNameEntry] = [
    MatchNameEntry("ADBE Material Options Group", "Material Options",
                   Category.META, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Casts Shadows", "Casts Shadows",
                   Category.MATERIAL, ValueKind.ENUM, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Accepts Shadows", "Accepts Shadows",
                   Category.MATERIAL, ValueKind.BOOL, ScaleRule.BOOLEAN_PASS_THROUGH),
    MatchNameEntry("ADBE Accepts Lights", "Accepts Lights",
                   Category.MATERIAL, ValueKind.BOOL, ScaleRule.BOOLEAN_PASS_THROUGH),
    MatchNameEntry("ADBE Appears in Reflections", "Appears in Reflections",
                   Category.MATERIAL, ValueKind.BOOL, ScaleRule.BOOLEAN_PASS_THROUGH),
    MatchNameEntry("ADBE Ambient Coefficient", "Ambient",
                   Category.MATERIAL, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Diffuse Coefficient", "Diffuse",
                   Category.MATERIAL, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Specular Coefficient", "Specular",
                   Category.MATERIAL, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Shininess Coefficient", "Shininess",
                   Category.MATERIAL, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Metal Coefficient", "Metal",
                   Category.MATERIAL, ValueKind.SCALAR, ScaleRule.PASS_THROUGH),
]


# ══════════════════════════════════════════════════════════════════════════
#  AUDIO GROUP
# ══════════════════════════════════════════════════════════════════════════

_AUDIO: List[MatchNameEntry] = [
    MatchNameEntry("ADBE Audio Group", "Audio",
                   Category.META, ValueKind.UNKNOWN, ScaleRule.PASS_THROUGH),
    MatchNameEntry("ADBE Audio Levels", "Audio Levels",
                   Category.AUDIO, ValueKind.VEC2, ScaleRule.PASS_THROUGH),
]


# ══════════════════════════════════════════════════════════════════════════
#  LAYER MARKERS
# ══════════════════════════════════════════════════════════════════════════

_MARKERS: List[MatchNameEntry] = [
    MatchNameEntry("ADBE Marker", "Marker",
                   Category.MARKER, ValueKind.MARKER, ScaleRule.PASS_THROUGH),
]


# ══════════════════════════════════════════════════════════════════════════
#  ROOT ASSEMBLY
# ══════════════════════════════════════════════════════════════════════════


REGISTRY: List[MatchNameEntry] = (
    _TRANSFORM
    + _CAMERA
    + _LIGHT
    + _MASKS
    + _EFFECTS_CONTAINERS
    + _EFFECTS_SPATIAL
    + _LAYER_STYLES
    + _SHAPE
    + _TEXT
    + _MATERIAL
    + _AUDIO
    + _MARKERS
)


# Indexes for O(1) lookup.
_BY_MATCH: Dict[str, MatchNameEntry] = {e.match_name: e for e in REGISTRY}


# ══════════════════════════════════════════════════════════════════════════
#  QUERY API
# ══════════════════════════════════════════════════════════════════════════


def get(match_name: str) -> Optional[MatchNameEntry]:
    """Return the registry entry for a matchName, or None if unregistered."""
    return _BY_MATCH.get(match_name)


def classify(match_name: str) -> MatchNameEntry:
    """
    Return the registry entry for a matchName, or a sentinel UNKNOWN entry
    so callers don't have to None-check. Unknown entries are safe to
    consume — they pass-through in every engine.
    """
    hit = _BY_MATCH.get(match_name)
    if hit is not None:
        return hit
    return MatchNameEntry(
        match_name=match_name,
        display_name=match_name,
        category=Category.UNKNOWN,
        value_kind=ValueKind.UNKNOWN,
        scale_rule=ScaleRule.PASS_THROUGH,
    )


def all_match_names() -> List[str]:
    return sorted(_BY_MATCH.keys())


def to_json() -> List[Dict[str, Any]]:
    """
    Serialize the registry as JSON-ready list. Emitted alongside manifests
    so ExtendScript consumers can read the same table Python dispatches on.
    """
    return [
        {
            "match_name": e.match_name,
            "display_name": e.display_name,
            "category": e.category.value,
            "value_kind": e.value_kind.value,
            "scale_rule": e.scale_rule.value,
            "separable": e.separable,
            "notes": e.notes,
        }
        for e in REGISTRY
    ]
