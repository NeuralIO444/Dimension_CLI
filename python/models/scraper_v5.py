# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/models/scraper_v5.py
Dimension Engine — Scraper Schema v5.0 (Rich, Registry-Driven)

This module defines the v5 scraper schema: the rich, introspection-first
shape the new Sovereign_Core emits. It is *additive* to the legacy
ScrapeManifest — v5 manifests carry all legacy fields plus richer ones
nested under new Optional fields. Legacy consumers ignore the new fields;
v5 consumers opt in by checking `schema_version`.

Design goals:
  - One uniform PropertyRecord shape for every AE property — transform,
    camera/light options, effects, masks, layer styles, shape contents.
  - matchName is the primary identifier (stable across AE versions and
    languages); display name is informational.
  - Separable properties (position/scale/anchor) carry per-axis key
    streams in `per_axis` when dimensionsSeparated is enabled.
  - Expressions are captured with source + fallback value + classification.
  - Every failed scrape surfaces in the manifest-level `errors` ledger —
    zero silent data loss.
  - Effects and masks are first-class with their own records so a future
    effect-aware engine can walk them uniformly.

Pydantic v2 compatible. All new fields are Optional — legacy manifests
validate against the extended ScrapeManifest unchanged.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ══════════════════════════════════════════════════════════════════════════
#  ENUMS
# ══════════════════════════════════════════════════════════════════════════


class Category(str, Enum):
    """
    Broad classification of a property. Consumers use this for coarse
    dispatch; scale_rule inside the record gives the precise math.
    """
    TRANSFORM = "transform"                 # position, scale, rotation, anchor, opacity
    TRANSFORM_AXIS = "transform_axis"       # X/Y/Z Position when separated
    TRANSFORM_3D = "transform_3d"           # rotation_x/y, orientation
    CAMERA_INTRINSIC = "camera_intrinsic"   # zoom, focus, aperture
    CAMERA_TRANSFORM = "camera_transform"   # pointOfInterest
    LIGHT_INTRINSIC = "light_intrinsic"     # intensity, color, radius
    EFFECT_GROUP = "effect_group"           # the effect container
    EFFECT_SPATIAL = "effect_spatial"       # effect parameter needing CENTER_REMAP
    EFFECT_SCALE = "effect_scale"           # effect parameter needing MULTIPLY_BY_S
    EFFECT_PARAM = "effect_param"           # everything else on an effect (pass-through)
    MASK_GROUP = "mask_group"
    MASK_SHAPE = "mask_shape"
    MASK_PARAM = "mask_param"
    LAYER_STYLE_GROUP = "layer_style_group"
    LAYER_STYLE_PARAM = "layer_style_param"
    TEXT_SOURCE = "text_source"
    TEXT_ANIMATOR = "text_animator"         # text animator group/properties
    SHAPE_CONTENTS = "shape_contents"       # root shape layer contents group
    SHAPE_GROUP = "shape_group"             # nested vector group
    SHAPE_PATH = "shape_path"              # vector path / fill / stroke params
    SHAPE_SCALE = "shape_scale"             # stroke width, offset — needs MULTIPLY_BY_S
    MATERIAL = "material"                   # 3D material options
    TIME = "time"                           # time remap, markers
    AUDIO = "audio"
    MARKER = "marker"
    META = "meta"                           # read-only / informational
    UNKNOWN = "unknown"                     # matchName not in registry


class ValueKind(str, Enum):
    """How to interpret the `static` / `keys.values` payload."""
    SCALAR = "scalar"        # one float
    VEC2 = "vec2"            # [x, y]
    VEC3 = "vec3"            # [x, y, z]
    VEC4 = "vec4"            # [x, y, z, w]
    COLOR = "color"          # [r, g, b, a] 0..1
    BOOL = "bool"            # 0 | 1
    ENUM = "enum"            # int with semantic mapping
    SHAPE = "shape"          # mask/shape path dict
    TEXT = "text"            # TextDocument dict
    MARKER = "marker"        # composition or layer marker
    UNKNOWN = "unknown"


class ExpressionClass(str, Enum):
    """Classification of an expression's resolution sensitivity."""
    SAFE = "safe"               # refs nothing resolution-sensitive — keep as-is
    REWRITABLE = "rewritable"   # refs thisComp.width/height — auto-substitutable
    UNSAFE = "unsafe"           # refs other layers/comps — needs human review
    UNKNOWN = "unknown"         # couldn't parse


class ErrorSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class TrackMatteType(str, Enum):
    NONE = "none"
    ALPHA = "alpha"
    ALPHA_INVERTED = "alpha_inverted"
    LUMA = "luma"
    LUMA_INVERTED = "luma_inverted"


class SourceKind(str, Enum):
    FOOTAGE = "footage"
    COMP = "comp"
    SOLID = "solid"
    PLACEHOLDER = "placeholder"
    MISSING = "missing"


class ScrapeMode(str, Enum):
    FAST = "fast"               # metadata + transforms only
    STANDARD = "standard"       # + keyframes + camera/light
    FULL = "full"               # + effects + masks + layer styles + expressions
    FORENSIC = "forensic"       # + static expression analysis + hashes
    SELECTED_LAYERS = "selected_layers"  # scrape only AE-selected layers + their parents


# ══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════


def _check_finite_recursive(value: Any) -> Any:
    """Reject NaN/Inf at the boundary. Recurses through lists/dicts."""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"NaN or Infinite float from scrape: {value}")
    elif isinstance(value, list):
        for v in value:
            _check_finite_recursive(v)
    elif isinstance(value, dict):
        for v in value.values():
            _check_finite_recursive(v)
    return value


# ══════════════════════════════════════════════════════════════════════════
#  CORE RECORDS
# ══════════════════════════════════════════════════════════════════════════


class KeyStream(BaseModel):
    """
    One animated property's keyframe data.

    `values` can be scalar (opacity), vec2/3/4 (position, color),
    color (rgba 0..1), shape (path dict), or text (TextDocument dict).
    The `value_kind` on the parent PropertyRecord tells you which.
    """
    times: List[float]
    values: List[Any]

    # Interpolation type per keyframe as string names, matching the JSX
    # SovCore_Value output: "linear" | "bezier" | "hold" | "unknown".
    # The PropertyRouter converts these to AE integer enum codes before
    # passing to LerpEngine / Babysitter.jsx.
    in_interp: Optional[List[str]] = None
    out_interp: Optional[List[str]] = None

    # Temporal ease (speed, influence) pairs — only populated for BEZIER keys
    # Shape: [[{speed:..., influence:...}, ...], ...]  one inner list per key
    temporal_ease_in: Optional[List[List[Dict[str, float]]]] = None
    temporal_ease_out: Optional[List[List[Dict[str, float]]]] = None

    # Spatial tangents — only for VEC2/VEC3 properties with Bezier spatial interp
    spatial_tangent_in: Optional[List[List[float]]] = None
    spatial_tangent_out: Optional[List[List[float]]] = None

    # Per-key flags
    is_roving: Optional[List[bool]] = None
    is_hold: Optional[List[bool]] = None

    @field_validator("times", "values")
    @classmethod
    def _finite(cls, v: Any) -> Any:
        return _check_finite_recursive(v)


class ExpressionInfo(BaseModel):
    """
    An expression attached to a property. The source is always captured.
    Classification is populated by the Python-side expression_analyzer
    (scraper emits `classification=UNKNOWN`; analyzer fills it in post-load).
    """
    source: str
    enabled: bool

    # The live-evaluated value at scrape time — used as a fallback if we
    # decide to strip the expression during conform.
    fallback_value: Optional[Any] = None

    # Static analysis results (populated by expression_analyzer):
    classification: ExpressionClass = ExpressionClass.UNKNOWN
    refs_this_comp_dims: bool = False
    refs_other_comps: List[str] = Field(default_factory=list)
    refs_other_layers: List[str] = Field(default_factory=list)
    refs_index_numeric: bool = False         # uses layer(1) style — brittle
    refs_time_functions: bool = False        # uses wiggle/random/timeToFrames
    hard_pixel_literals: List[float] = Field(default_factory=list)
    analyzer_notes: List[str] = Field(default_factory=list)


class PropertyRecord(BaseModel):
    """
    The uniform shape for every scrapeable AE property.

    Path is the canonical identifier — a list of strings tracing from the
    layer root to this property. Examples:
        ["transform", "ADBE Position"]
        ["effects", "ADBE CC Particle World", "ADBE CC PW Producer Position"]
        ["masks", "Mask 1", "ADBE Mask Shape"]

    The consumer dispatches on (category, value_kind, scale_rule) to decide
    what math to apply. Unknown matchNames pass through untransformed.
    """
    # ── Identity ──────────────────────────────────────────────────────
    path: List[str]
    match_name: str
    display_name: str

    # ── Classification (from match_name_registry) ────────────────────
    category: Category = Category.UNKNOWN
    value_kind: ValueKind = ValueKind.UNKNOWN
    scale_rule: str = "pass_through"   # mirrors property_registry.ScaleRule values

    # ── Value ─────────────────────────────────────────────────────────
    # Static: always populated; for animated props this is the value at
    # scrape time (often t=0 or current CTI).
    static: Optional[Any] = None

    # Keys: populated when property has keyframes in the UNIFIED stream.
    keys: Optional[KeyStream] = None

    # Per-axis keys: populated only for separable VEC3 properties with
    # dimensionsSeparated=true. Keys on axis are independent streams.
    # Map: "x" | "y" | "z" → KeyStream (scalar values).
    per_axis: Optional[Dict[str, KeyStream]] = None
    separated: bool = False

    # ── Expression ────────────────────────────────────────────────────
    expression: Optional[ExpressionInfo] = None

    # ── Flags ─────────────────────────────────────────────────────────
    enabled: bool = True
    locked: bool = False

    # ── Extension slot ────────────────────────────────────────────────
    # Anything the scraper wants to surface that doesn't fit the schema
    # lands here. Typed consumers ignore; forensic tooling uses it.
    extra: Optional[Dict[str, Any]] = None

    @field_validator("static")
    @classmethod
    def _finite_static(cls, v: Any) -> Any:
        return _check_finite_recursive(v)


# ══════════════════════════════════════════════════════════════════════════
#  EFFECTS / MASKS / LAYER STYLES
# ══════════════════════════════════════════════════════════════════════════


class EffectRecord(BaseModel):
    """One effect instance on a layer, with its full parameter list."""
    index: int                          # position in the effect stack
    match_name: str                     # e.g. "ADBE CC Particle World"
    display_name: str                   # localized name from AE
    enabled: bool = True
    # All sub-properties of this effect (walked recursively). Each one
    # classified by the registry — spatial params get the right scale rule.
    properties: List[PropertyRecord] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_jsx_properties(cls, data: Any) -> Any:
        """
        Sovereign_Core.jsx writes effect properties as:
            { match_name, display_name, value, keys }
        PropertyRecord expects:
            { path, match_name, display_name, static, keys, ... }

        This validator normalizes the JSX shape into PropertyRecord shape
        so Pydantic can deserialize without a schema mismatch.
        `path` is synthesized as ["effects", <effect_display_name>, <prop_match_name>].
        `value` is mapped to `static`.
        """
        if not isinstance(data, dict):
            return data
        raw_props = data.get("properties")
        if not raw_props or not isinstance(raw_props, list):
            return data
        effect_display = data.get("display_name", "")
        normalized = []
        for p in raw_props:
            if not isinstance(p, dict):
                normalized.append(p)
                continue
            # Already in PropertyRecord format (has 'path')
            if "path" in p:
                normalized.append(p)
                continue
            prop_match = p.get("match_name", "")
            prop_display = p.get("display_name", "")
            # Map value → static; keys → keys
            normalized.append({
                "path": ["effects", effect_display, prop_match],
                "match_name": prop_match,
                "display_name": prop_display,
                "static": p.get("value"),
                "keys": p.get("keys"),
                "value_kind": p.get("value_kind", "unknown"),
                "scale_rule": "pass_through",
            })
        data = dict(data)
        data["properties"] = normalized
        return data


class MaskShape(BaseModel):
    """A mask path snapshot. Vertices are in layer-local pixel space."""
    vertices: List[List[float]] = Field(default_factory=list)
    in_tangents: List[List[float]] = Field(default_factory=list)
    out_tangents: List[List[float]] = Field(default_factory=list)
    closed: bool = True


class MaskRecord(BaseModel):
    """One mask on a layer. Shape vertices need root-scaling at conform time."""
    index: int
    name: str
    mode: str = "add"                   # add | subtract | intersect | lighten | darken | difference | none
    inverted: bool = False
    locked: bool = False
    shape: Optional[MaskShape] = None
    # Shape keyframes (animated mask) — separate from static `shape`
    shape_keys: Optional[KeyStream] = None
    properties: List[PropertyRecord] = Field(default_factory=list)   # feather, opacity, expansion


class LayerStyleRecord(BaseModel):
    """
    Drop shadow, inner shadow, bevel, stroke, etc.
    Layer style params with world-space distances need MULTIPLY_BY_S.
    """
    match_name: str                     # e.g. "ADBE Drop Shadow Layer Style"
    display_name: str
    enabled: bool = True
    properties: List[PropertyRecord] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════
#  LAYER-LEVEL METADATA
# ══════════════════════════════════════════════════════════════════════════


class LayerFlags(BaseModel):
    """One flat block of layer-level toggles — no more scattered scraping."""
    enabled: bool = True
    locked: bool = False
    shy: bool = False
    solo: bool = False
    three_d: bool = False
    adjustment: bool = False
    null_layer: bool = False
    guide: bool = False
    motion_blur: bool = False
    frame_blending: str = "none"                 # "none" | "frame" | "pixel"
    preserve_transparency: bool = False
    collapse_transformations: bool = False
    # Schema 5.1 / Slot 12.5 — per-layer quality switch on precomp / vector
    # layers. AE's "continuously rasterize" flag (sunburst icon, same row as
    # the collapse switch); when ON for a precomp it bypasses the 2D flatten.
    # Default False; Stage B's recursive scrape populates the real value.
    continuously_rasterize: bool = False
    auto_orient: str = "none"                    # "none" | "rotate" | "camera" | "path"
    blend_mode: str = "normal"
    quality: str = "best"                        # "wireframe" | "draft" | "best"
    sampling: str = "bilinear"                   # "bilinear" | "bicubic"
    is_text_layer: bool = False
    has_video: bool = True
    has_audio: bool = False
    # audio_enabled is the per-layer audio switch (distinct from has_audio which
    # is a source-item capability flag). Kept with the other bool fields.
    audio_enabled: bool = False

    @field_validator("frame_blending", mode="before")
    @classmethod
    def _coerce_frame_blending(cls, v):
        # JSX sends !!layer.frameBlending — bool, not the string enum.
        # True means blending is on but we don't know the type; default to "frame".
        if v is False or v is None:
            return "none"
        if v is True:
            return "frame"
        return v

    @field_validator("sampling", mode="before")
    @classmethod
    def _coerce_sampling(cls, v):
        # samplingQuality not available pre-CC 2017 — JSX falls back to null
        if v is None:
            return "bilinear"
        return v


class TrackMatte(BaseModel):
    """Track matte relationship. Target by both index and UID for safety."""
    type: TrackMatteType = TrackMatteType.NONE
    target_index: Optional[int] = None
    target_uid: Optional[str] = None

    # AE integer codes for TrackMatteType enum values.
    # ClassVar so Pydantic does not treat this as a model field.
    _AE_MATTE_CODES: ClassVar[dict] = {
        "5766": TrackMatteType.ALPHA,
        "5767": TrackMatteType.ALPHA_INVERTED,
        "5768": TrackMatteType.LUMA,
        "5769": TrackMatteType.LUMA_INVERTED,
    }

    @model_validator(mode="before")
    @classmethod
    def _remap_jsx_fields(cls, data):
        """
        JSX sends {mode, layer_index, layer_name} but model expects
        {type, target_index, target_uid}. Remap and coerce AE enum codes.
        """
        if not isinstance(data, dict):
            return data
        out = dict(data)
        # Field name remaps
        if "mode" in out and "type" not in out:
            raw = str(out.pop("mode"))
            # AE integer codes → enum string
            code_map = {
                "5766": "alpha", "5767": "alpha_inverted",
                "5768": "luma",  "5769": "luma_inverted",
            }
            # Also handle "TrackMatteType.ALPHA" style strings
            name_map = {
                "TrackMatteType.ALPHA": "alpha",
                "TrackMatteType.ALPHA_INVERTED": "alpha_inverted",
                "TrackMatteType.LUMA": "luma",
                "TrackMatteType.LUMA_INVERTED": "luma_inverted",
            }
            out["type"] = code_map.get(raw) or name_map.get(raw) or "none"
        if "layer_index" in out and "target_index" not in out:
            out["target_index"] = out.pop("layer_index")
        out.pop("layer_name", None)  # informational only, not in model
        return out


class SourceItem(BaseModel):
    """Metadata about the source item backing a layer (footage/comp/solid)."""
    kind: SourceKind
    id: Optional[int] = None
    name: str
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    pixel_aspect: Optional[float] = None
    frame_rate: Optional[float] = None
    file_path: Optional[str] = None              # footage only
    nested_comp_id: Optional[int] = None         # comp (precomp) only
    has_audio: Optional[bool] = None
    is_still: Optional[bool] = None

    @field_validator("kind", mode="before")
    @classmethod
    def _coerce_kind(cls, v):
        # JSX sends "file" for FileSource footage items; "unknown" as safety
        # fallback when instanceof checks all miss — treat both as footage.
        if v in ("file", "unknown"):
            return "footage"
        return v


class TimeInfo(BaseModel):
    """Per-layer timing envelope."""
    in_point: float = 0.0
    out_point: float = 0.0
    start_time: float = 0.0
    stretch: float = 100.0                       # percent
    time_remap_enabled: bool = False
    time_remap_keys: Optional[KeyStream] = None

    @field_validator("in_point", "out_point", "start_time", "stretch", mode="before")
    @classmethod
    def _coerce_null_float(cls, v):
        # JSX _safe() fallback sends null when timing properties are unavailable
        return v if v is not None else 0.0


class MarkerRecord(BaseModel):
    """A comment marker on a layer or comp."""
    time: Optional[float] = 0.0
    duration: float = 0.0
    comment: str = ""
    chapter: Optional[str] = None
    url: Optional[str] = None
    frame_target: Optional[str] = None
    cue_point_name: Optional[str] = None
    label: Optional[int] = None                  # AE label color 0..16


class LayerReference(BaseModel):
    """Slot 12.5 Stage B — one cross-layer reference held in an effect
    parameter. AE effects can carry a `PropertyValueType.LAYER_INDEX`
    parameter that points at another layer in the same comp (Set Matte,
    Displacement Map, Compound Blur, Calculations, Set Channels, Channel
    Combiner, CC Composite, etc.). Detection is generic — any property
    of that value type is captured here regardless of which effect owns it.

    Conform handling lives in a later stage (Q8 detect-and-warn now;
    full Q8C atomic-pair planning deferred per the scope-doc).

    `from_layer_uid` is the layer carrying the effect (the consumer).
    `to_layer_uid` is the referenced layer's UID, resolved via the
    containing comp's uidByIndex map; None when the index resolves to
    no layer (the property was set to 0 / `<none>` in AE).
    `to_layer_index` is the raw scraped index — kept alongside the UID
    so diagnostics can show what AE returned even when UID resolution
    misses (e.g. cross-comp index references AE doesn't support but
    a corrupt project might carry).
    """
    from_layer_uid:        Optional[str] = None
    to_layer_uid:          Optional[str] = None
    to_layer_index:        Optional[int] = None
    effect_match_name:     Optional[str] = None
    effect_display_name:   Optional[str] = None
    property_match_name:   Optional[str] = None
    property_display_name: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════
#  ERROR LEDGER
# ══════════════════════════════════════════════════════════════════════════


class ScrapeError(BaseModel):
    """
    One failure surfaced by the scraper. Every try/catch inside the
    scraper funnels here — silent swallows are a cardinal sin for a
    pixel-faithful pipeline.
    """
    layer_index: Optional[int] = None
    layer_name: Optional[str] = None
    path: List[str] = Field(default_factory=list)
    operation: str = "scrape"           # "read_value" | "read_keys" | "walk" | "scrape" | ...
    error: str
    severity: ErrorSeverity = ErrorSeverity.WARNING
    hypothesis: Optional[str] = None    # scraper's best guess at root cause
    match_name: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════
#  SCRAPE META
# ══════════════════════════════════════════════════════════════════════════


class ScrapeMeta(BaseModel):
    """
    Per-scrape provenance. The schema_version here is the contract the
    scraper promises; Python validates against it and can trigger
    migration logic for older versions.

    Schema versions:
        5.0 — original v5 scraper shape (pre Slot 12.5).
        5.1 — Slot 12.5 additive fields: LayerModel.containing_comp_id,
              containing_comp_uid, wrapper_layer_uid, nesting_depth;
              LayerFlags.continuously_rasterize; CompNode.preserve_nested_*.
              Every 5.1 addition is Optional with a sensible default, so
              5.0 manifests parse unchanged against the 5.1 model.
    """
    schema_version: str = "5.1"
    scraper_version: str = "sovereign-5.0.0"
    scrape_mode: ScrapeMode = ScrapeMode.STANDARD
    ae_version: Optional[str] = None
    ae_build: Optional[str] = None
    scrape_timestamp: Optional[str] = None       # ISO 8601
    scrape_duration_ms: Optional[int] = None
    total_layers: int = 0
    total_properties_scraped: int = 0
    total_errors: int = 0
    scraped_comp_ids: Optional[List[int]] = None
    manifest_hash: Optional[str] = None          # sha256 of canonical form
    host_os: Optional[str] = None
    host_name: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════
#  COMP-LEVEL EXTRAS
# ══════════════════════════════════════════════════════════════════════════


class RenderQueueItem(BaseModel):
    """One render queue item. Useful for preserving delivery specs."""
    comp_id: int
    comp_name: str
    status: str                                  # "queued" | "rendering" | "done" | ...
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    output_modules: List[Dict[str, Any]] = Field(default_factory=list)
