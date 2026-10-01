# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/property_registry.py
Dimension Engine — Single Source of Truth for Every AE Property

The registry is a declarative table of every property Dimension touches
across the pipeline (scrape → scale → lerp → inject). Each consumer reads
from this table instead of maintaining its own parallel list of if/elif
branches. Adding a new property is a one-line entry here, not a hunt across
five files.

Layers of consumption:
  - Sovereign_Core.jsx  (scraper) — reads names + AE paths (via JSON emit)
  - ScaleEngine (Python) — dispatches on scale_rule for static values
  - LerpEngine  (Python) — dispatches on lerp_rule for keyframe values
  - Babysitter.jsx (injector) — reads names + accessor paths (via JSON emit)

Rules:
  - `scale_rule` and `lerp_rule` are disjoint enums describing the math
    applied when a property is transformed. PASS_THROUGH means "do not scale".
  - `layer_kinds` is a tuple of kinds this property applies to. A camera
    layer will only be queried for properties whose `layer_kinds` contains
    LayerKind.CAMERA.
  - `status = RESERVED` means the slot is declared but no engine currently
    handles it. Scraper/scale/lerp/inject must treat reserved entries as
    no-ops until promoted to IMPLEMENTED.

Adding a property: define a PropertyDef, append to REGISTRY. The four
helper functions below will route it automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ══════════════════════════════════════════════════════════════════════════
#  ENUMS
# ══════════════════════════════════════════════════════════════════════════


class LayerKind(str, Enum):
    AV = "av"
    CAMERA = "camera"
    LIGHT = "light"
    TEXT = "text"        # reserved
    SHAPE = "shape"      # reserved


class AEGroup(str, Enum):
    """Which AE property group hosts the property."""
    TRANSFORM = "transform"
    CAMERA_OPTION = "cameraOption"
    LIGHT_OPTION = "lightOption"
    SOURCE_TEXT = "sourceText"   # reserved
    CONTENTS = "contents"        # reserved


class ScaleRule(str, Enum):
    """
    How a *static* value transforms under uniform scale S.

    - PASS_THROUGH:                value unchanged.
    - MULTIPLY_BY_S:               scalar or list, all axes multiplied by S.
    - CENTER_REMAP_XY_SCALE_Z:     [(x-scx)*S+tcx, (y-scy)*S+tcy,
                                   z*S if scale_z else z].
    - ROOT_MULTIPLY_BY_S:          MULTIPLY_BY_S, but only when layer is root.
                                   Child layers pass through (parent cascade).
    - ROOT_CENTER_REMAP_XY_SCALE_Z: same, root-only.
    - CAMERA_INTRINSIC_SCALE:      always MULTIPLY_BY_S regardless of
                                   root/child (rendering property, not a
                                   parent-cascade transform).
    - BOOLEAN_PASS_THROUGH:        1/0 value, never scaled.
    - ROOT_REMAP_AXIS_X:           scalar axis; (v - src_cx) * S + tgt_cx;
                                   root-gated. Used when a layer has
                                   Separate Dimensions enabled on position
                                   and we scrape per-axis keyframe streams.
    - ROOT_REMAP_AXIS_Y:           scalar; Y analog of above.
    - ROOT_SCALE_AXIS_Z:           scalar Z; v * S if scale_z else v;
                                   root-gated.
    """
    PASS_THROUGH = "pass_through"
    MULTIPLY_BY_S = "multiply_by_s"
    CENTER_REMAP_XY_SCALE_Z = "center_remap_xy_scale_z"
    ROOT_MULTIPLY_BY_S = "root_multiply_by_s"
    ROOT_CENTER_REMAP_XY_SCALE_Z = "root_center_remap_xy_scale_z"
    CAMERA_INTRINSIC_SCALE = "camera_intrinsic_scale"
    BOOLEAN_PASS_THROUGH = "boolean_pass_through"
    ROOT_REMAP_AXIS_X = "root_remap_axis_x"
    ROOT_REMAP_AXIS_Y = "root_remap_axis_y"
    ROOT_SCALE_AXIS_Z = "root_scale_axis_z"


class Status(str, Enum):
    IMPLEMENTED = "implemented"
    RESERVED = "reserved"


# ══════════════════════════════════════════════════════════════════════════
#  PROPERTY DEFINITION
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PropertyDef:
    # Canonical identifier used in conformed_transforms / conformed_keys / logs
    name: str

    # Layer kinds this property applies to
    layer_kinds: Tuple[LayerKind, ...]

    # Where AE exposes it (group + property name)
    ae_group: AEGroup
    ae_name: str                # ExtendScript dot-accessor name, e.g. "position"
    ae_display: str             # .property("Display Name") lookup, e.g. "Position"

    # Capture profile
    scrape_static: bool         # Sovereign_Core captures a static value
    scrape_keys: bool           # Sovereign_Core captures keyframes
    temporal_key: Optional[str] # key name in temporal_data dict (None if no keys)

    # Transformation rules
    scale_rule: ScaleRule
    lerp_rule: ScaleRule        # usually mirrors scale_rule; diverges only if needed

    # Runtime status
    status: Status = Status.IMPLEMENTED

    # Optional: conformed_transforms field name (defaults to `name`)
    conformed_field: Optional[str] = None

# ══════════════════════════════════════════════════════════════════════════
#  THE REGISTRY
# ══════════════════════════════════════════════════════════════════════════

# NOTE: ordering within a kind is not significant to consumers, but is kept
# consistent with scrape order for readability.

_AV_ALL = (LayerKind.AV, LayerKind.CAMERA, LayerKind.LIGHT, LayerKind.TEXT, LayerKind.SHAPE)
_AV_LAYERS = (LayerKind.AV, LayerKind.TEXT, LayerKind.SHAPE)  # AV-family (has anchor, scale, opacity)


REGISTRY: List[PropertyDef] = [
    # ── Transform group — all layer kinds ──────────────────────────────
    PropertyDef(
        name="position",
        layer_kinds=_AV_ALL,
        ae_group=AEGroup.TRANSFORM,
        ae_name="position",
        ae_display="Position",
        scrape_static=True,
        scrape_keys=True,
        temporal_key="position",
        scale_rule=ScaleRule.ROOT_CENTER_REMAP_XY_SCALE_Z,
        lerp_rule=ScaleRule.ROOT_CENTER_REMAP_XY_SCALE_Z,
    ),
    # ── Separated-dimension position axes ──────────────────────────────
    # Used when a layer has Separate Dimensions enabled on position. AE
    # stores keyframes on the scalar sub-properties X Position / Y Position
    # / Z Position, and `transform.position.numKeys` is 0. The scraper
    # detects `transform.position.dimensionsSeparated` and emits per-axis
    # streams under these temporal keys. Static values are never scraped
    # for these — the unified `position` static is authoritative.
    PropertyDef(
        name="position_x",
        layer_kinds=_AV_ALL,
        ae_group=AEGroup.TRANSFORM,
        ae_name="xPosition",
        ae_display="X Position",
        scrape_static=False,
        scrape_keys=True,
        temporal_key="position_x",
        scale_rule=ScaleRule.ROOT_REMAP_AXIS_X,
        lerp_rule=ScaleRule.ROOT_REMAP_AXIS_X,
    ),
    PropertyDef(
        name="position_y",
        layer_kinds=_AV_ALL,
        ae_group=AEGroup.TRANSFORM,
        ae_name="yPosition",
        ae_display="Y Position",
        scrape_static=False,
        scrape_keys=True,
        temporal_key="position_y",
        scale_rule=ScaleRule.ROOT_REMAP_AXIS_Y,
        lerp_rule=ScaleRule.ROOT_REMAP_AXIS_Y,
    ),
    PropertyDef(
        name="position_z",
        layer_kinds=_AV_ALL,
        ae_group=AEGroup.TRANSFORM,
        ae_name="zPosition",
        ae_display="Z Position",
        scrape_static=False,
        scrape_keys=True,
        temporal_key="position_z",
        scale_rule=ScaleRule.ROOT_SCALE_AXIS_Z,
        lerp_rule=ScaleRule.ROOT_SCALE_AXIS_Z,
    ),
    PropertyDef(
        name="scale",
        # Cameras have no scale.
        layer_kinds=(LayerKind.AV, LayerKind.LIGHT, LayerKind.TEXT, LayerKind.SHAPE),
        ae_group=AEGroup.TRANSFORM,
        ae_name="scale",
        ae_display="Scale",
        scrape_static=True,
        scrape_keys=True,
        temporal_key="scale",
        scale_rule=ScaleRule.ROOT_MULTIPLY_BY_S,
        lerp_rule=ScaleRule.ROOT_MULTIPLY_BY_S,
    ),
    PropertyDef(
        name="anchor",
        # Cameras and lights have no anchor point (their transform is world-space).
        layer_kinds=_AV_LAYERS,
        ae_group=AEGroup.TRANSFORM,
        ae_name="anchorPoint",
        ae_display="Anchor Point",
        scrape_static=True,
        scrape_keys=True,
        temporal_key="anchor",
        scale_rule=ScaleRule.PASS_THROUGH,
        lerp_rule=ScaleRule.PASS_THROUGH,
    ),
    PropertyDef(
        name="rotation",                  # Z-axis rotation (2D + 3D .zRotation)
        layer_kinds=_AV_ALL,
        ae_group=AEGroup.TRANSFORM,
        ae_name="rotation",
        ae_display="Rotation",
        scrape_static=True,
        scrape_keys=True,
        temporal_key="rotation",
        scale_rule=ScaleRule.PASS_THROUGH,
        lerp_rule=ScaleRule.PASS_THROUGH,
        conformed_field="rotation",
    ),
    PropertyDef(
        name="rotation_x",
        layer_kinds=_AV_ALL,
        ae_group=AEGroup.TRANSFORM,
        ae_name="xRotation",
        ae_display="X Rotation",
        scrape_static=True,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.PASS_THROUGH,
        lerp_rule=ScaleRule.PASS_THROUGH,
    ),
    PropertyDef(
        name="rotation_y",
        layer_kinds=_AV_ALL,
        ae_group=AEGroup.TRANSFORM,
        ae_name="yRotation",
        ae_display="Y Rotation",
        scrape_static=True,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.PASS_THROUGH,
        lerp_rule=ScaleRule.PASS_THROUGH,
    ),
    PropertyDef(
        name="orientation",
        layer_kinds=_AV_ALL,
        ae_group=AEGroup.TRANSFORM,
        ae_name="orientation",
        ae_display="Orientation",
        scrape_static=True,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.PASS_THROUGH,
        lerp_rule=ScaleRule.PASS_THROUGH,
    ),
    PropertyDef(
        name="opacity",
        # Cameras have no opacity.
        layer_kinds=(LayerKind.AV, LayerKind.LIGHT, LayerKind.TEXT, LayerKind.SHAPE),
        ae_group=AEGroup.TRANSFORM,
        ae_name="opacity",
        ae_display="Opacity",
        scrape_static=False,                # keyframes only in legacy scraper
        scrape_keys=True,
        temporal_key="opacity",
        scale_rule=ScaleRule.PASS_THROUGH,
        lerp_rule=ScaleRule.PASS_THROUGH,
    ),

    # ── Camera-only properties ─────────────────────────────────────────
    PropertyDef(
        name="camera_zoom",
        layer_kinds=(LayerKind.CAMERA,),
        ae_group=AEGroup.CAMERA_OPTION,
        ae_name="zoom",
        ae_display="Zoom",
        scrape_static=True,
        scrape_keys=True,
        temporal_key="camera_zoom",
        scale_rule=ScaleRule.CAMERA_INTRINSIC_SCALE,
        lerp_rule=ScaleRule.CAMERA_INTRINSIC_SCALE,
        conformed_field="camera.zoom",
    ),
    PropertyDef(
        name="camera_pointOfInterest",
        layer_kinds=(LayerKind.CAMERA,),
        ae_group=AEGroup.TRANSFORM,         # POI lives on transform, not cameraOption
        ae_name="pointOfInterest",
        ae_display="Point of Interest",
        scrape_static=True,
        scrape_keys=True,
        temporal_key="camera_pointOfInterest",
        scale_rule=ScaleRule.ROOT_CENTER_REMAP_XY_SCALE_Z,
        lerp_rule=ScaleRule.ROOT_CENTER_REMAP_XY_SCALE_Z,
        conformed_field="camera.pointOfInterest",
    ),
    PropertyDef(
        name="camera_focusDistance",
        layer_kinds=(LayerKind.CAMERA,),
        ae_group=AEGroup.CAMERA_OPTION,
        ae_name="focusDistance",
        ae_display="Focus Distance",
        scrape_static=True,
        scrape_keys=True,
        temporal_key="camera_focusDistance",
        scale_rule=ScaleRule.CAMERA_INTRINSIC_SCALE,
        lerp_rule=ScaleRule.CAMERA_INTRINSIC_SCALE,
        conformed_field="camera.focusDistance",
    ),
    PropertyDef(
        name="camera_aperture",
        layer_kinds=(LayerKind.CAMERA,),
        ae_group=AEGroup.CAMERA_OPTION,
        ae_name="aperture",
        ae_display="Aperture",
        scrape_static=True,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.PASS_THROUGH,
        lerp_rule=ScaleRule.PASS_THROUGH,
        conformed_field="camera.aperture",
    ),
    PropertyDef(
        name="camera_blurLevel",
        layer_kinds=(LayerKind.CAMERA,),
        ae_group=AEGroup.CAMERA_OPTION,
        ae_name="blurLevel",
        ae_display="Blur Level",
        scrape_static=True,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.PASS_THROUGH,
        lerp_rule=ScaleRule.PASS_THROUGH,
        conformed_field="camera.blurLevel",
    ),
    PropertyDef(
        name="camera_depthOfField",
        layer_kinds=(LayerKind.CAMERA,),
        ae_group=AEGroup.CAMERA_OPTION,
        ae_name="depthOfField",
        ae_display="Depth of Field",
        scrape_static=True,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.BOOLEAN_PASS_THROUGH,
        lerp_rule=ScaleRule.BOOLEAN_PASS_THROUGH,
        conformed_field="camera.depthOfField",
    ),

    # ── Light-only properties ──────────────────────────────────────────
    PropertyDef(
        name="light_intensity",
        layer_kinds=(LayerKind.LIGHT,),
        ae_group=AEGroup.LIGHT_OPTION,
        ae_name="intensity",
        ae_display="Intensity",
        scrape_static=True,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.PASS_THROUGH,
        lerp_rule=ScaleRule.PASS_THROUGH,
        conformed_field="light.intensity",
    ),
    PropertyDef(
        name="light_color",
        layer_kinds=(LayerKind.LIGHT,),
        ae_group=AEGroup.LIGHT_OPTION,
        ae_name="color",
        ae_display="Color",
        scrape_static=True,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.PASS_THROUGH,
        lerp_rule=ScaleRule.PASS_THROUGH,
        conformed_field="light.color",
    ),
    PropertyDef(
        name="light_coneAngle",
        layer_kinds=(LayerKind.LIGHT,),
        ae_group=AEGroup.LIGHT_OPTION,
        ae_name="coneAngle",
        ae_display="Cone Angle",
        scrape_static=True,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.PASS_THROUGH,
        lerp_rule=ScaleRule.PASS_THROUGH,
        conformed_field="light.coneAngle",
    ),
    PropertyDef(
        name="light_coneFeather",
        layer_kinds=(LayerKind.LIGHT,),
        ae_group=AEGroup.LIGHT_OPTION,
        ae_name="coneFeather",
        ae_display="Cone Feather",
        scrape_static=True,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.PASS_THROUGH,
        lerp_rule=ScaleRule.PASS_THROUGH,
        conformed_field="light.coneFeather",
    ),
    PropertyDef(
        name="light_falloffDistance",
        layer_kinds=(LayerKind.LIGHT,),
        ae_group=AEGroup.LIGHT_OPTION,
        ae_name="falloffDistance",
        ae_display="Falloff Distance",
        scrape_static=True,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.MULTIPLY_BY_S,     # world-space distance
        lerp_rule=ScaleRule.MULTIPLY_BY_S,
        conformed_field="light.falloffDistance",
    ),
    PropertyDef(
        name="light_radius",
        layer_kinds=(LayerKind.LIGHT,),
        ae_group=AEGroup.LIGHT_OPTION,
        ae_name="radius",
        ae_display="Radius",
        scrape_static=True,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.MULTIPLY_BY_S,     # world-space distance
        lerp_rule=ScaleRule.MULTIPLY_BY_S,
        conformed_field="light.radius",
    ),
    PropertyDef(
        name="light_castsShadows",
        layer_kinds=(LayerKind.LIGHT,),
        ae_group=AEGroup.LIGHT_OPTION,
        ae_name="castsShadows",
        ae_display="Casts Shadows",
        scrape_static=True,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.BOOLEAN_PASS_THROUGH,
        lerp_rule=ScaleRule.BOOLEAN_PASS_THROUGH,
        conformed_field="light.castsShadows",
    ),

    # ── RESERVED: text layer content ───────────────────────────────────
    # Scraper does not currently capture sourceText or per-character animators.
    # Declared here so the registry is the single declaration site. Consumers
    # MUST check .status and no-op on RESERVED entries.
    PropertyDef(
        name="text_sourceText",
        layer_kinds=(LayerKind.TEXT,),
        ae_group=AEGroup.SOURCE_TEXT,
        ae_name="sourceText",
        ae_display="Source Text",
        scrape_static=False,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.PASS_THROUGH,
        lerp_rule=ScaleRule.PASS_THROUGH,
        status=Status.RESERVED,
    ),

    # ── RESERVED: shape layer content ──────────────────────────────────
    PropertyDef(
        name="shape_contents",
        layer_kinds=(LayerKind.SHAPE,),
        ae_group=AEGroup.CONTENTS,
        ae_name="contents",
        ae_display="Contents",
        scrape_static=False,
        scrape_keys=False,
        temporal_key=None,
        scale_rule=ScaleRule.PASS_THROUGH,       # shape path vertices would need scaling
        lerp_rule=ScaleRule.PASS_THROUGH,
        status=Status.RESERVED,
    ),
]


# ══════════════════════════════════════════════════════════════════════════
#  QUERY API
# ══════════════════════════════════════════════════════════════════════════

# Pre-indexed for O(1) lookup.
_BY_NAME: Dict[str, PropertyDef] = {p.name: p for p in REGISTRY}
_BY_TEMPORAL_KEY: Dict[str, PropertyDef] = {
    p.temporal_key: p for p in REGISTRY if p.temporal_key
}


def get(name: str) -> Optional[PropertyDef]:
    """Return the PropertyDef with this canonical name, or None."""
    return _BY_NAME.get(name)


def by_temporal_key(key: str) -> Optional[PropertyDef]:
    """Return the PropertyDef whose scraped temporal_data key matches."""
    return _BY_TEMPORAL_KEY.get(key)


# ══════════════════════════════════════════════════════════════════════════
#  EFFECT / LAYER STYLE PROPERTY SCALE RULES
# ══════════════════════════════════════════════════════════════════════════

# Known effect property match names → scale rule.
# Any property whose AE matchName appears here gets the mapped rule;
# everything else falls through to the display-name keyword heuristic.
# Match names are stable across AE versions and locales.
_EFFECT_MATCH_NAME_RULES: Dict[str, str] = {
    # ── Drop Shadow (effect: ADBE Drop Shadow) ────────────────────────
    "ADBE Drop Shadow-0004": "multiply_by_s",   # Distance
    "ADBE Drop Shadow-0005": "multiply_by_s",   # Softness
    # ── CC RepeTile (effect: ADBE CC RepeTile) ────────────────────────
    "ADBE CC RepeTile-0001": "multiply_by_s",   # Expand Left
    "ADBE CC RepeTile-0002": "multiply_by_s",   # Expand Right
    "ADBE CC RepeTile-0003": "multiply_by_s",   # Expand Up
    "ADBE CC RepeTile-0004": "multiply_by_s",   # Expand Down
    # ── Gaussian Blur ─────────────────────────────────────────────────
    "ADBE Gaussian Blur 2-0001": "multiply_by_s",   # Blurriness
    # ── Fast Box Blur ─────────────────────────────────────────────────
    "ADBE Fast Blur-0001": "multiply_by_s",   # Blur Radius
    # ── Radial Blur ───────────────────────────────────────────────────
    "ADBE Radial Blur-0002": "multiply_by_s",   # Amount
    # ── Stroke ────────────────────────────────────────────────────────
    "ADBE Stroke-0004": "multiply_by_s",   # Brush Size
    # ── Bevel Alpha ───────────────────────────────────────────────────
    "ADBE Bevel Alpha-0001": "multiply_by_s",   # Edge Thickness
    # ── Layer Styles: Drop Shadow ─────────────────────────────────────
    "dropShadow/distance": "multiply_by_s",
    "dropShadow/size": "multiply_by_s",
    "dropShadow/chokeMatte": "multiply_by_s",
    # ── Layer Styles: Inner Shadow ────────────────────────────────────
    "innerShadow/distance": "multiply_by_s",
    "innerShadow/size": "multiply_by_s",
    # ── Layer Styles: Outer Glow ──────────────────────────────────────
    "outerGlow/size": "multiply_by_s",
    "outerGlow/chokeMatte": "multiply_by_s",
    # ── Layer Styles: Inner Glow ──────────────────────────────────────
    "innerGlow/size": "multiply_by_s",
    # ── Layer Styles: Bevel and Emboss ────────────────────────────────
    "bevelEmboss/size": "multiply_by_s",
    "bevelEmboss/soften": "multiply_by_s",
    # ── Layer Styles: Stroke ──────────────────────────────────────────
    "stroke/size": "multiply_by_s",
}

# Display-name keyword heuristic — applied when match_name is unknown.
# Any scalar-like property whose display name contains one of these
# words (case-insensitive) is treated as a world-space pixel distance.
_SCALE_DISPLAY_KEYWORDS: frozenset = frozenset({
    "distance", "softness", "size", "radius", "width",
    "spread", "blur", "thickness", "feather",
})


def lookup_effect_scale_rule(match_name: str, display_name: str, value_kind: Optional[str] = None) -> str:
    """
    Return the scale rule string for an effect or layer-style property.

    Priority:
      1. Known match name in _EFFECT_MATCH_NAME_RULES
      2. Layer 2 — value_kind dispatch: when value_kind == "vec2", default to "center_remap_xy_scale_z"
      3. Display-name keyword heuristic
      4. Default: "pass_through"
    """
    rule = _EFFECT_MATCH_NAME_RULES.get(match_name)
    if rule:
        return rule
    kind_str = getattr(value_kind, "value", value_kind)
    if isinstance(kind_str, str) and kind_str.lower() == "vec2":
        return "center_remap_xy_scale_z"
    dl = display_name.lower()
    for kw in _SCALE_DISPLAY_KEYWORDS:
        if kw in dl:
            return "multiply_by_s"
    return "pass_through"


# ══════════════════════════════════════════════════════════════════════════
#  RULE APPLICATION
# ══════════════════════════════════════════════════════════════════════════


def apply_rule(
    rule: ScaleRule,
    value: Any,
    *,
    uniform_scale: float,
    is_root: bool,
    src_center: Tuple[float, float],
    tgt_center: Tuple[float, float],
    scale_z: bool,
    K: Optional[float] = None,
    layer_kind: str = "av",
) -> Any:
    """
    Apply a ScaleRule to a value (static or keyframe).

    Returns the transformed value. Pass-through rules and mismatched types
    return the value unchanged. Never throws — malformed inputs fall through
    to pass-through so the engine can never blow up a conform over a single
    bad value (the NaN firewall in the injector catches those separately).

    Bug J (2026-05-09): camera depth-axis values use K instead of S.
    K = max(target_W/source_W, target_H/source_H) (no bleed). Mirrors
    Bug L's static-path math in scale_engine._conform_camera. K and
    layer_kind default to None / "av" so non-Bug-J callers (effect_conformer)
    preserve pre-Bug-J behavior — depth-axis values fall back to S when
    K is not provided.
    """
    if rule in (ScaleRule.PASS_THROUGH, ScaleRule.BOOLEAN_PASS_THROUGH):
        return value

    S = uniform_scale
    scx, scy = src_center
    tcx, tcy = tgt_center
    # Z is the depth-axis scalar: K for cameras (Bug J), S otherwise.
    # Bug L static path uses K for camera position.Z, POI Z, zoom,
    # focusDistance; this is the keyframe parallel.
    is_camera = (layer_kind == "camera")
    Z = K if (is_camera and K is not None) else S

    if rule is ScaleRule.ROOT_CENTER_REMAP_XY_SCALE_Z:
        if not is_root:
            return value
        if isinstance(value, list) and len(value) >= 2:
            vz = value[2] if len(value) > 2 else 0.0
            return [
                ((value[0] - scx) * S) + tcx,
                ((value[1] - scy) * S) + tcy,
                vz * Z if scale_z else vz,
            ]
        return value

    if rule is ScaleRule.ROOT_MULTIPLY_BY_S:
        if not is_root:
            return value
        if isinstance(value, list):
            vz = value[2] if len(value) > 2 else 100.0
            return [
                value[0] * S,
                value[1] * S,
                vz * S if scale_z else vz,
            ]
        if isinstance(value, (int, float)):
            return value * S
        return value

    if rule is ScaleRule.CENTER_REMAP_XY_SCALE_Z:
        # Not gated on is_root — used for camera POI (no parent cascade) and 2D spatial points.
        # Bug J: Z uses K for cameras (Bug L's POI Z static parallel).
        if isinstance(value, list) and len(value) >= 2:
            remap_x = ((value[0] - scx) * S) + tcx
            remap_y = ((value[1] - scy) * S) + tcy
            if len(value) == 2:
                return [remap_x, remap_y]
            vz = value[2]
            return [
                remap_x,
                remap_y,
                vz * Z if scale_z else vz,
            ]
        return value

    if rule is ScaleRule.MULTIPLY_BY_S:
        if isinstance(value, list):
            return [v * S if isinstance(v, (int, float)) else v for v in value]
        if isinstance(value, (int, float)):
            return value * S
        return value

    if rule is ScaleRule.CAMERA_INTRINSIC_SCALE:
        # Camera-only rule (camera_zoom, camera_focusDistance). Bug L
        # static path uses K for these regardless of is_3d_camera_scene;
        # Bug J keyframe parallel uses K when provided. Falls back to S
        # for non-Bug-J callers.
        scalar = K if K is not None else S
        if isinstance(value, (int, float)):
            return value * scalar
        if isinstance(value, list):
            return [v * scalar if isinstance(v, (int, float)) else v for v in value]
        return value

    # ── Axis rules (scalar values from Separate Dimensions) ─────────────
    # When a layer has Separate Dimensions enabled on Position, AE stores
    # keyframes on the scalar sub-properties X Position / Y Position /
    # Z Position. The scraper emits them as separate streams; these rules
    # apply the same center-remap math the unified rule does, one axis
    # at a time. `value` is always a scalar here.

    if rule is ScaleRule.ROOT_REMAP_AXIS_X:
        if not is_root:
            return value
        if isinstance(value, (int, float)):
            return ((value - scx) * S) + tcx
        return value

    if rule is ScaleRule.ROOT_REMAP_AXIS_Y:
        if not is_root:
            return value
        if isinstance(value, (int, float)):
            return ((value - scy) * S) + tcy
        return value

    if rule is ScaleRule.ROOT_SCALE_AXIS_Z:
        # Bug J: cameras use K (depth-axis); AV stays on S (content-axis).
        # Mirrors scale_engine's `K if (kind == 'camera' and scale_z) else
        # (S if scale_z else 1.0)` static formula.
        if not is_root:
            return value
        if isinstance(value, (int, float)):
            return value * Z if scale_z else value
        return value

    # Unknown rule — fail safe
    return value


def to_json() -> List[Dict[str, Any]]:
    """
    Serialize the registry as a JSON-ready list. Intended for emission
    alongside the chunk manifest so ExtendScript consumers (Babysitter,
    Sovereign_Core) can read the same table Python dispatches on.
    """
    return [
        {
            "name": p.name,
            "layer_kinds": [k.value for k in p.layer_kinds],
            "ae_group": p.ae_group.value,
            "ae_name": p.ae_name,
            "ae_display": p.ae_display,
            "scrape_static": p.scrape_static,
            "scrape_keys": p.scrape_keys,
            "temporal_key": p.temporal_key,
            "scale_rule": p.scale_rule.value,
            "lerp_rule": p.lerp_rule.value,
            "status": p.status.value,
            "conformed_field": p.conformed_field or p.name,
        }
        for p in REGISTRY
    ]
