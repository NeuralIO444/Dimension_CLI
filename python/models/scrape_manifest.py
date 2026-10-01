# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.
"""
python/models/scrape_manifest.py
Dimension Engine v6.2 — **Runtime / Enriched** Scrape Manifest

This is the model for the manifest as it exists in Python's memory. It is
`extra="allow"` so stages like the surveyor, gardener, and style-proposal
engine can attach their findings without breaking the schema.

Defines the schema for scrape_manifest.json, written by Sovereign_Core.jsx
and read by ScaleEngine and the Tkinter UI.

Schema must match Sovereign_Core.jsx saveBridgeManifest() output exactly.
Any mismatch will raise a ValidationError before the math engine starts.

For the strict wire-format schema that validates raw JSX output, see
`jsx_wire_manifest.py`. Read paths use a heuristic to validate against
the wire schema first, then parse into this richer runtime model.
"""

from enum import Enum
import math
import os
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, field_validator, model_validator, ValidationError, ConfigDict
from models.bridge_contract import DuplicationPlan
from core.logger import log

# v5 schema additions. Imported lazily-friendly here — these are pure
# Pydantic models with no runtime dependencies on the legacy models, so
# circular imports are not a concern.
from models.scraper_v5 import (
    EffectRecord,
    LayerFlags,
    LayerReference,
    LayerStyleRecord,
    MarkerRecord,
    MaskRecord,
    PropertyRecord,
    RenderQueueItem,
    ScrapeError,
    ScrapeMeta,
    SourceItem,
    TimeInfo,
    TrackMatte,
)


def _check_finite(value: Any) -> Any:
    """Reject NaN/Inf floats at the boundary — garbage from AE stops here."""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"NaN or Infinite float from scrape: {value}")
    elif isinstance(value, list):
        for v in value:
            _check_finite(v)
    return value


class DimensionBaseModel(BaseModel):
    """Base model for Dimension manifests that preserves un-modelled JSX fields."""
    model_config = ConfigDict(extra="allow")


class ProjectInfo(DimensionBaseModel):
    name: str
    width: int
    height: int
    fps: Optional[float] = 30.0
    preview_path: Optional[str] = None

    @field_validator("preview_path")
    @classmethod
    def check_preview_path(cls, v: Optional[str]) -> Optional[str]:
        # Only validate if a path was actually provided
        if v and not os.path.exists(v):
            raise ValueError(f"Preview path does not exist on disk: {v}")
        return v


class TemporalDataMap(DimensionBaseModel):
    """Keyframe data for a single property (position, scale, rotation, anchor, opacity).
    Field names match Sovereign_Core.jsx output and lerp_engine.py expectations exactly.
    """
    times: List[float]
    values: List[Any]
    keyInInterpolationType: Optional[List[int]] = None   # AE KeyframeInterpolationType per keyframe
    keyOutInterpolationType: Optional[List[int]] = None


class TemporalDataDict(DimensionBaseModel):
    """All scraped keyframe properties for one layer."""
    position: Optional[TemporalDataMap] = None
    # Per-axis position streams, populated when the layer has Separate
    # Dimensions enabled on Position (AE stores keys on scalar sub-properties
    # X/Y/Z Position and `position.numKeys` is 0). When any of these are
    # present, the injector writes per-axis via
    # transform.property("X Position") etc., not transform.position.
    position_x: Optional[TemporalDataMap] = None
    position_y: Optional[TemporalDataMap] = None
    position_z: Optional[TemporalDataMap] = None
    scale: Optional[TemporalDataMap] = None
    rotation: Optional[TemporalDataMap] = None
    anchor: Optional[TemporalDataMap] = None
    opacity: Optional[TemporalDataMap] = None
    # Camera-specific animated properties (only populated for camera layers)
    camera_zoom: Optional[TemporalDataMap] = None
    camera_focusDistance: Optional[TemporalDataMap] = None
    camera_pointOfInterest: Optional[TemporalDataMap] = None


class DependencyEdge(DimensionBaseModel):
    """Expression dependency between two layers."""
    sourceId: int
    targetId: int
    type: str
    safe: bool = True


class CameraProperties(DimensionBaseModel):
    """Camera-specific properties scraped from a CameraLayer.

    Composition framing is camera-relative, so every one of these affects
    pixel position post-conform. `zoom` rescales by the uniform conform factor
    so the framed world region stays pixel-locked. `pointOfInterest` is a
    world-space position and remaps just like a layer position would.
    """
    zoom: Optional[float] = None
    pointOfInterest: Optional[List[float]] = None     # [x, y, z] world position
    depthOfField: Optional[bool] = None
    focusDistance: Optional[float] = None
    aperture: Optional[float] = None
    blurLevel: Optional[float] = None

    @field_validator("zoom", "focusDistance", "aperture", "blurLevel")
    @classmethod
    def check_finite_scalars(cls, v: Any) -> Any:
        return _check_finite(v)

    @field_validator("pointOfInterest")
    @classmethod
    def check_finite_poi(cls, v: Any) -> Any:
        return _check_finite(v)


class LightProperties(DimensionBaseModel):
    """Light-specific properties scraped from a LightLayer.

    `radius` and `falloffDistance` rescale by the uniform conform factor so
    the light hits the same world points after the comp grows. Intensity and
    color pass through unchanged.
    """
    lightType: Optional[str] = None        # "Parallel" | "Spot" | "Point" | "Ambient"
    intensity: Optional[float] = None
    color: Optional[List[float]] = None    # [r, g, b] 0..1
    coneAngle: Optional[float] = None      # degrees, spot only
    coneFeather: Optional[float] = None    # 0..100, spot only
    falloff: Optional[str] = None          # "None" | "Smooth" | "Inverse Square Clamped"
    falloffDistance: Optional[float] = None
    radius: Optional[float] = None
    castsShadows: Optional[bool] = None

    @field_validator("intensity", "coneAngle", "coneFeather", "falloffDistance", "radius")
    @classmethod
    def check_finite_scalars(cls, v: Any) -> Any:
        return _check_finite(v)


class TypographicInfo(DimensionBaseModel):
    """Typographic DNA scraped from TextDocument (TASK-ENG-01 / #256)."""
    font_size_pt: float = Field(default=0.0, description="Font size in points/pixels from TextDocument.")
    line_count: int = Field(default=0, description="Line count in text document.")
    char_count: int = Field(default=0, description="Total character count in text document.")
    font_name: Optional[str] = Field(default=None, description="Font PostScript or display name.")


class LayerArchetype(str, Enum):
    """Semantic archetype classification of an After Effects layer (PR 4)."""
    TYPE = "TYPE"                    # Text layers, typography, live text documents
    VECTOR_2D = "VECTOR_2D"          # Shape layers, Illustrator vectors, bezier path art
    THREE_D = "THREE_D"              # 3D models, 3D solids, camera/light planes
    KEYED_ALPHA = "KEYED_ALPHA"      # Keyed footage, greenscreen cutouts, raster art with alpha channels
    LIVE_PRECOMP = "LIVE_PRECOMP"    # Live nested After Effects composition precomps


class ArtworkBounds(DimensionBaseModel):
    """Pixel-accurate non-zero visual artwork boundary (alpha hull / vector extrema)."""
    left: float = 0.0
    top: float = 0.0
    width: float = 0.0
    height: float = 0.0
    alpha_threshold: float = 1.0 / 255.0
    centroid: Optional[List[float]] = None  # [cx, cy] visual center of mass / optical centroid

    @field_validator("left", "top", "width", "height", "alpha_threshold")
    @classmethod
    def check_finite_bounds(cls, v: Any) -> Any:
        return _check_finite(v)



class LayerModel(DimensionBaseModel):
    """
    One AE layer from the active composition.
    Populated by Sovereign_Core.jsx scrape() then validated here.
    """
    id: Optional[int] = None              # AE layer.id (unique within project)
    index: int                            # 1-based layer order in comp
    name: str
    uid: Optional[str] = None             # Stable UUID from Sovereign_Core (survives reordering)
    parent_uid: Optional[str] = None      # UID of parent layer (None if root)
    parent_index: int = -1               # index of parent layer, -1 if none
    is_dependency: bool = False          # True if layer was pulled in by parent chain harvest
    isBrittle: bool = False              # True if any transform property has an active expression
    collapseTransformations: bool = False  # True if "Collapse Transformations" flag is set

    # v4.4: discriminate AV vs Camera vs Light. Default is "av" for backwards
    # compatibility with v4.3 and earlier manifests where every layer was
    # assumed to be a footage/solid/precomp/text item.
    layer_kind: str = "av"               # "av" | "camera" | "light"
    threeD: bool = False                 # AE 3D-layer flag

    # Static transform values (at current time)
    position: Optional[List[float]] = None   # [x, y, z]
    scale: Optional[List[float]] = None      # [x%, y%, z%]
    rotation_x: Optional[float] = None       # degrees, 3D layers only
    rotation_y: Optional[float] = None       # degrees, 3D layers only
    rotation_z: Optional[float] = None       # degrees
    orientation: Optional[List[float]] = None  # [x, y, z] degrees, 3D only
    anchor: Optional[List[float]] = None     # [x, y, z] in layer-local space

    # Per-kind property bag — populated only for the matching layer_kind.
    camera: Optional[CameraProperties] = None
    light: Optional[LightProperties] = None

    @field_validator("position", "scale", "anchor", "orientation")
    @classmethod
    def check_finite_arrays(cls, v: Any) -> Any:
        return _check_finite(v)

    @field_validator("rotation_x", "rotation_y", "rotation_z")
    @classmethod
    def check_finite_rotation(cls, v: Any) -> Any:
        return _check_finite(v)

    # Optional — not yet scraped by Sovereign_Core.jsx
    breadcrumb: Optional[List[str]] = None
    dependencies: Optional[List[DependencyEdge]] = []
    expressions: Optional[Dict[str, Optional[str]]] = {}
    temporal_data: Optional[TemporalDataDict] = None

    # ── Slot 12.5 / schema 5.1 — cross-comp parent-chain fields ─────────
    # Populated by Stage B's recursive scrape (Sovereign_Core.jsx walking
    # into nested precomps). Top-level layers in the active comp leave
    # `wrapper_layer_uid` as None and `nesting_depth` as 0; layers found
    # inside a precomp's source comp carry the wrapper layer's uid and
    # the depth at which they live.
    #
    # All Optional — schema 5.0 manifests parse unchanged.
    containing_comp_id: Optional[int] = None
    """AE comp.id of the comp this layer instance belongs to. For a layer
    at the active comp's top level, this is the active comp's id. For a
    layer scraped from inside a nested precomp, this is the source comp's
    id. Used by the Stage C cross-comp matrix walker to identify which
    coord space a layer lives in."""

    containing_comp_uid: Optional[str] = None
    """Stable UID for the containing comp, if the JSX side stamps one.
    Reserved for future use — comps do not carry uids in schema 5.0; the
    field is added now so the schema 5.1 shape is forward-compatible."""

    wrapper_layer_uid: Optional[str] = None
    """UID of the precomp wrapper layer in the parent comp. None for
    layers at the active comp's top level. For a layer at nesting depth
    N, this points at the layer that referenced this layer's source comp
    from the comp one level up."""

    nesting_depth: Optional[int] = None
    """0 = layer lives at the active comp's top level. 1 = layer lives
    inside a precomp referenced by the active comp. N = layer lives N
    nesting levels deep. Stage C consumers gate behavior off this field."""

    # ── Slot 12.5 Stage B — effect-input cross-layer references ─────────
    # One entry per `PropertyValueType.LAYER_INDEX` effect parameter held
    # by this layer. The conform engine treats these as Q8 detect-and-warn
    # signals — the conform report lists the relationships so the designer
    # can decide whether to intervene. Full Q8C atomic-pair planning is
    # deferred per the scope-doc.
    layer_references: Optional[List[LayerReference]] = None

    # ── v5 Scraper Schema — additive, all Optional ──────────────────────
    # These are populated by the v5 scraper pipeline (SovCore_Walker etc.)
    # and ignored by legacy consumers. Together they replace the flat
    # `position / scale / camera / light / temporal_data` bag with a
    # richer, introspection-first representation.
    #
    # Consumers should prefer `properties` + `effects` + `masks` over the
    # legacy flat fields when `ScrapeManifest.scrape_meta.schema_version`
    # is "5.0" or higher. Legacy flat fields are STILL populated by the
    # v5 scraper for back-compat during the migration.
    flags: Optional[LayerFlags] = None
    source_item: Optional[SourceItem] = None
    track_matte: Optional[TrackMatte] = None
    time_info: Optional[TimeInfo] = None

    # The flat list of PropertyRecords walked from this layer's root.
    # Paths are unique within the list and trace from the layer root
    # to each property (e.g. ["transform", "ADBE Position"]).
    properties: Optional[List[PropertyRecord]] = None

    # First-class containers — walked into their own records so the
    # effect-aware / mask-aware engines can iterate them uniformly.
    effects: Optional[List[EffectRecord]] = None
    masks: Optional[List[MaskRecord]] = None
    layer_styles: Optional[List[LayerStyleRecord]] = None
    markers: Optional[List[MarkerRecord]] = None

    # Per-layer scrape errors collected by the v5 walker. JSX writes
    # these under `_v5_errors` for back-compat with pre-schema manifests
    # — `_coerce_v5_errors` below promotes them to the typed field.
    v5_errors: Optional[List[ScrapeError]] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_v5_errors(cls, data):
        if isinstance(data, dict) and "_v5_errors" in data and "v5_errors" not in data:
            data["v5_errors"] = data.pop("_v5_errors")
        return data

    # ── Dimension Content Tag — semantic role of this layer ──────────────
    # Populated in two passes:
    #   1. JSX scrape (SovCore_Layer.jsx): manual tags from comment/#syntax,
    #      layer name [BRACKET] syntax, or AE label color convention.
    #   2. Python Surveyor (core/surveyor.py): heuristic auto-classification
    #      for any layer that arrives without a manual tag.
    #
    # Tag vocabulary: defined by `config/tag_registry.yaml` —
    # canonical ids include TT/SUP/HERO/LOGO/BODY/CTA/LGL/DISC/BG
    # plus BOXART/ARTWORK/ANIMATION/GUIDE/PROTECT/NULL/UNCLASS.
    # Legacy aliases (TYPE/LEGALS/BACKGROUND/KEYART) still pass
    # validation — see core.tag_registry.REGISTRY.
    # Source: manual_comment | manual_name | manual_label |
    #         heuristic | profile | None
    content_tag: Optional[str] = None
    content_tag_source: Optional[str] = None   # "manual_*" | "heuristic" | None
    content_tag_confidence: Optional[float] = None  # 0.0–1.0, None = unclassified

    # ── Slot 15.6 — conflict-detection (#6) ───────────────────────────
    # When a manual tag (manual_label / manual_comment / manual_name)
    # shadows a profile prefix rule that would have matched the layer
    # name, the surveyor records the profile's would-be tag here so the
    # tagging panel can surface a ⚠ "manual override conflicts with
    # profile rule" warning. Populated ONLY when:
    #   - content_tag_source startswith("manual_")
    #   - active profile resolves the layer's name to a rule
    #   - that rule's tag differs from the manual tag
    # None means "no conflict" or "no profile active." Additive field,
    # no schema_version bump.
    profile_suggested_tag: Optional[str] = None

    # B1 — human-readable reason for the profile conflict, e.g.
    # 'Profile rule "TXT_" would assign "TOP" but manual tag is "BOTTOM"'.
    # Consumed by the CEP conflict popover for actionable explanations.
    profile_conflict_reason: Optional[str] = None

    # ── Raw comment (PR-E.1, gardener prereq) ───────────────────────────
    # The full original `layer.comment` string from AE, surfaced for the
    # pre-flight comment gardener (`python/core/comment_gardener.py`).
    # JSX writes this verbatim — no toUpperCase, no parsing — so the
    # gardener can classify the original studio content (render notes,
    # asset DAM IDs, etc.) before any tag-write would overwrite it.
    #
    # JSX serializes empty strings as None on the Python side (see
    # SovCore_Layer.jsx::scrapeLayer). Optional so legacy v5 manifests
    # written before this field landed parse unchanged.
    comment: Optional[str] = None

    # ── SOE-Scrape fields (v5.2.5) ───────────────────────────────────────
    # Sampled once at hero_time. Used by occlusion_engine.py to detect UI
    # chrome collisions before format conform is written. All None-safe:
    # layers that can't produce bounds (Cameras, Lights, Nulls,
    # Adjustments) simply have None here.
    #
    # Backwards-compatible: every field is Optional, every default is
    # None. v5-era manifests parse unchanged. schema_version is NOT bumped.

    source_rect: Optional[List[float]] = None
    """[left, top, width, height] in layer-local space from
    `layer.sourceRectAtTime(hero_time, false)`. NOT comp-space."""

    world_bounds: Optional[Dict[str, float]] = None
    """{"l": float, "t": float, "r": float, "b": float} in comp-space
    pixels. AA formula:
        left   = position.x + (rect.left  - anchor.x) * scaleX
        right  = left + rect.width  * scaleX
        top    = position.y + (rect.top   - anchor.y) * scaleY
        bottom = top  + rect.height * scaleY
    Validator below rejects NaN / Inf in any edge so SOE never
    consumes a corrupt rect."""

    hero_time: Optional[float] = None
    """Time (seconds) at which source_rect and world_bounds were
    sampled. JSX picks `comp.workAreaStart + comp.workAreaDuration/2`
    as the default; a layer marker named "HERO" (case-insensitive)
    overrides per-layer."""

    is_keyed: Optional[Dict[str, bool]] = None
    """{"position": bool, "scale": bool, "anchor": bool} —
    `Property.numKeys > 0` per spatial property. SOE refuses to
    reposition layers with `is_keyed.position == True` so a keyed
    animation isn't silently truncated."""

    source_coverage: Optional[float] = None
    """Fraction of comp area covered by world_bounds, clamped [0, 1].
    Computed by Python surveyor in survey_manifest() pre-loop,
    NOT scraped by JSX. None when world_bounds is absent or comp
    dimensions are unknown."""

    typographic_info: Optional[TypographicInfo] = None
    """Typographic DNA (TASK-ENG-01 / #256): font size, line count, character count,
    scraped from TextDocument for typographic hierarchy classification."""

    archetype: Optional[LayerArchetype] = None
    """Semantic archetype classification (PR 4: TYPE, VECTOR_2D, THREE_D, KEYED_ALPHA, LIVE_PRECOMP)."""

    artwork_bounds: Optional[ArtworkBounds] = None
    """Tight visual artwork boundary (alpha hull / vector extrema) for edge-aware relayout."""


    @field_validator("world_bounds")
    @classmethod
    def _world_bounds_finite(cls, v):
        """Reject NaN / Inf and require all four edges if the dict is
        present. Empty dict / None passes through unchanged."""
        if v is None:
            return v
        if not isinstance(v, dict):
            raise ValueError("world_bounds must be a dict or None")
        for key in ("l", "t", "r", "b"):
            if key not in v:
                raise ValueError(f"world_bounds missing edge: {key!r}")
            val = v[key]
            try:
                fval = float(val)
            except (TypeError, ValueError):
                raise ValueError(f"world_bounds[{key!r}] not numeric: {val!r}")
            # math.isfinite blocks both NaN and ±Inf in one shot.
            import math as _math
            if not _math.isfinite(fval):
                raise ValueError(f"world_bounds[{key!r}] not finite: {val!r}")
            v[key] = fval
        return v


class ScrapeManifest(DimensionBaseModel):
    status: str
    project_info: ProjectInfo
    layers: List[LayerModel]

    # ── v5 additions (all Optional — legacy manifests parse unchanged) ─
    scrape_meta: Optional[ScrapeMeta] = None
    errors: Optional[List[ScrapeError]] = None
    render_queue: Optional[List[RenderQueueItem]] = None

    # ── PR-E.1 — Comment gardener report (Python-side enrichment) ─
    # Attached by `python/bridge/sovereign_bridge.py::parse_manifest`
    # after the manifest is loaded. Runtime type is
    # `core.comment_gardener.CommentReport`; declared as `Any` so
    # this model has no hard dep on the gardener module (the
    # gardener depends on this model via duck-typed access, so
    # reversing that would create a cycle).
    #
    # Never written by JSX. Never round-tripped to disk —
    # `atomic_write_json` writes the raw dict from the JSX scrape,
    # which doesn't carry this field. JSON dumps of the ScrapeManifest
    # would emit `comment_report: null` if model_dump is called,
    # but the bridge's existing flow doesn't dump back to disk after
    # enrichment, so the on-disk manifest shape stays clean.
    comment_report: Optional[Any] = None

    # ── AI Comp Brief (Integration 5) ────────────────────────────────────
    # Generated by generate_comp_brief() in ollama_client.py during the
    # surveyor's pass 6 when ollama_enabled is True. Never written by JSX;
    # never round-tripped to disk (survives only in the in-memory manifest
    # for the current session). Additive — schema version NOT bumped.
    ai_brief: Optional[str] = None

    # ── Phase 1 (loud failures) — survey degradation warnings ───────────
    # Human-readable list of every degraded path the surveyor hit while
    # classifying this manifest (AI pass unreachable, prefs unreadable,
    # gardener failure, …). Written by survey_manifest(); None when the
    # run was fully clean. Never written by JSX — a fresh scrape resets
    # it, which is correct (new scrape = new survey state). Additive —
    # schema version NOT bumped. Consumers: CEP panel log, HTML report.
    survey_warnings: Optional[List[str]] = None

    # ── Sprint 3 — Pre-conform diagnostic report ─────────────────────────
    # Attached by `python/core/surveyor.py::survey_manifest()` after the
    # tag-classification pass. Runtime type is
    # `core.comment_gardener.PreflightReport`; declared as `Any` to avoid
    # a circular import (comment_gardener depends on this manifest model
    # via duck-typed access, so reversing the import would create a cycle).
    #
    # Never written by JSX. Never round-tripped to disk. Consumers cast
    # to PreflightReport when they need structured access:
    #   from core.comment_gardener import PreflightReport
    #   report: PreflightReport = manifest.preflight_report
    preflight_report: Optional[Any] = None

    # ── Stage 3a — style profile attachment (advisory only) ─────────────
    # Set when the artist has selected an active style profile for this
    # job. Never written by JSX. Never round-tripped to disk as part of
    # the JSX-authored manifest — set in-memory by the CEP panel /
    # style_cli.py flow after a normal scrape completes, mirroring
    # ai_brief's "session-only" persistence posture. Style proposals
    # (core/style_proposals.py) are computed from this id + the target
    # manifest; they do NOT mutate any layer field. See CLAUDE.md's AI
    # guardrail and .pipeline/plan.md (Stage 3) Boundary C — nothing here
    # is consumed by scale_engine*.py or gravity.py.
    active_style_profile_id: Optional[str] = None
    style_proposals: Optional[List[Any]] = None
    """Runtime type is `List[core.style_proposals.StyleProposal]` (or the
    Pydantic model directly once Item 4 lands) — declared loosely here to
    avoid a hard import of models/style_profile.py into every consumer of
    ScrapeManifest, matching comment_report's and preflight_report's
    existing Any-typed pattern in this same file."""

    # ── v6.3 — Duplication plan (centralized) ───────────────────────────
    duplication_plan: Optional[DuplicationPlan] = None

    @property
    def schema_version(self) -> str:
        """Convenience accessor — returns '4.x' for legacy, else the meta value."""
        if self.scrape_meta and self.scrape_meta.schema_version:
            return self.scrape_meta.schema_version
        return "4.x"

    def is_v5(self) -> bool:
        return self.scrape_meta is not None and self.scrape_meta.schema_version.startswith("5.")

    @model_validator(mode="after")
    def cross_validate_parent_refs(self) -> "ScrapeManifest":
        """Verify parent_uid and parent_index agree for every layer.

        If a designer reorders layers between scrape and conform, parent_index
        may point to a different layer than parent_uid.  Catch it here before
        the math engine silently computes against the wrong parent.

        Slot 12.5 schema 5.1 — recursive scrape emits layers from multiple
        comps in one flat list. layer.index is unique only within a comp,
        so the uid↔index lookup MUST be scoped by `containing_comp_id`.
        Layers without `containing_comp_id` (legacy 5.0 manifests) share a
        single bucket — same behavior as pre-5.1.
        """
        # uid_to_index and index_to_uid are keyed by containing_comp_id so
        # cross-comp index collisions don't false-flag mismatches. The
        # outer key is the comp id (or None for legacy / unstamped layers).
        uid_to_index_by_comp: Dict[Any, Dict[str, int]] = {}
        index_to_uid_by_comp: Dict[Any, Dict[int, Optional[str]]] = {}
        for layer in self.layers:
            comp_key = layer.containing_comp_id
            if comp_key not in uid_to_index_by_comp:
                uid_to_index_by_comp[comp_key] = {}
                index_to_uid_by_comp[comp_key] = {}
            if layer.uid:
                uid_to_index_by_comp[comp_key][layer.uid] = layer.index
            index_to_uid_by_comp[comp_key][layer.index] = layer.uid

        mismatches = []
        for layer in self.layers:
            if layer.parent_index == -1:
                continue  # root layer — nothing to check

            comp_key = layer.containing_comp_id
            index_to_uid = index_to_uid_by_comp.get(comp_key, {})
            uid_to_index = uid_to_index_by_comp.get(comp_key, {})

            # If parent_uid is set, verify the layer at parent_index has that uid
            if layer.parent_uid:
                actual_uid_at_index = index_to_uid.get(layer.parent_index)
                if actual_uid_at_index and actual_uid_at_index != layer.parent_uid:
                    mismatches.append(
                        f"Layer '{layer.name}' (idx {layer.index}, "
                        f"comp {comp_key}): "
                        f"parent_index={layer.parent_index} has uid '{actual_uid_at_index}' "
                        f"but parent_uid='{layer.parent_uid}'"
                    )
                    # Auto-heal: trust the UID (survives reordering), fix the index
                    correct_index = uid_to_index.get(layer.parent_uid)
                    if correct_index is not None:
                        log.warning(
                            "Parent reference healed by UID",
                            extra={
                                "layer": layer.name,
                                "containing_comp_id": comp_key,
                                "old_parent_index": layer.parent_index,
                                "new_parent_index": correct_index,
                                "parent_uid": layer.parent_uid,
                            },
                        )
                        layer.parent_index = correct_index
                    else:
                        log.warning(
                            "Parent UID not found in manifest — layer treated as root",
                            extra={
                                "layer": layer.name,
                                "containing_comp_id": comp_key,
                                "parent_uid": layer.parent_uid,
                            },
                        )
                        layer.parent_index = -1

        if mismatches:
            log.warning(
                "Parent reference mismatches detected and healed",
                extra={"count": len(mismatches), "details": mismatches},
            )

        return self

    @classmethod
    def validate_and_parse(cls, raw_data_str: str) -> "ScrapeManifest":
        """Parse and validate a raw JSON string. Raises RuntimeError on failure.

        Callers (main_window.py, __main__.py) are responsible for handling the
        error and deciding whether to sys.exit — library code never calls sys.exit
        directly, which would bypass any caller cleanup or UI teardown.
        """
        try:
            return cls.model_validate_json(raw_data_str)
        except ValidationError as e:
            log.error(
                "Scrape manifest validation failed",
                extra={"errors": e.error_count(), "detail": e.errors()[:5]},
            )
            raise RuntimeError(
                f"Manifest validation failed ({e.error_count()} error(s)): "
                f"{e.errors()[0]['msg'] if e.errors() else 'unknown'}"
            ) from e
