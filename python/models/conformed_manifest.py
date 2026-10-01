# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/models/conformed_manifest.py
Dimension Engine v4.5 — Conformed Manifest Pydantic Models

Defines the schema for all post-conform data structures:
  - ConformedTransforms  — the new position/scale/rotation/anchor for one layer
  - ConformedKeyData     — scaled keyframe data for one property
  - ConformedKeys        — all conformed keyframe properties for one layer
  - ConformedLayer       — extends LayerModel with conformed_transforms + conformed_keys
  - ChunkManifest        — a 30-layer slice ready for Babysitter.jsx injection

The 30-layer chunk limit exists because ExtendScript holds the entire chunk in RAM
before flushing. Larger chunks cause AE to lock up on complex compositions.

NaN/Inf guard:
  check_finite_floats() is applied to all float fields at validation time.
  Any NaN or Inf value (which can occur from division by zero in edge-case geometries)
  will raise a ValidationError before writing to disk.
"""

import math
from typing import List, Any, Optional
from pydantic import BaseModel, Field, field_validator

from .scrape_manifest import LayerModel


def check_finite_floats(value: Any) -> Any:
    """
    Recursively validate that no float is NaN or Inf.
    Called by Pydantic field validators on all numeric transform data.
    """
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"NaN or Infinite float detected: {value}")
    elif isinstance(value, list):
        for v in value:
            check_finite_floats(v)
    elif isinstance(value, dict):
        for v in value.values():
            check_finite_floats(v)
    return value


class ConformedCameraProperties(BaseModel):
    """Camera properties after conform. zoom and pointOfInterest are rescaled."""
    zoom: Optional[float] = None
    pointOfInterest: Optional[List[float]] = None
    depthOfField: Optional[bool] = None
    focusDistance: Optional[float] = None
    aperture: Optional[float] = None
    blurLevel: Optional[float] = None

    @field_validator("zoom", "focusDistance", "aperture", "blurLevel", "pointOfInterest")
    @classmethod
    def validate_floats(cls, v: Any) -> Any:
        return check_finite_floats(v)


class ConformedLightProperties(BaseModel):
    """Light properties after conform. radius and falloffDistance are rescaled."""
    lightType: Optional[str] = None
    intensity: Optional[float] = None
    color: Optional[List[float]] = None
    coneAngle: Optional[float] = None
    coneFeather: Optional[float] = None
    falloff: Optional[str] = None
    falloffDistance: Optional[float] = None
    radius: Optional[float] = None
    castsShadows: Optional[bool] = None

    @field_validator("intensity", "coneAngle", "coneFeather", "falloffDistance", "radius", "color")
    @classmethod
    def validate_floats(cls, v: Any) -> Any:
        return check_finite_floats(v)


class ConformedTransforms(BaseModel):
    """Static transform values after conform math is applied.

    The is_root flag is the single source of truth for the root/child
    invariant that prevents shatter:
      - is_root=True  → position is center-remapped, scale is multiplied by S
      - is_root=False → position and scale are passed through unchanged
    Both ScaleEngine and Babysitter.jsx enforce this — the flag travels
    through chunk JSON so the JSX side can double-check."""

    is_root: bool             # MANDATORY — no default. ScaleEngine must explicitly set this.

    position: List[float]   # root: target comp space | child: parent-local (unchanged)
    scale: List[float]      # root: [x%*S, y%*S, z%(*S if 3D cam scene)] | child: unchanged
    rotation: float         # degrees (rotation_z) — always unchanged
    anchor: List[float]     # always unchanged (layer-local pixel space)

    # 3D rotation pass-through (unchanged by conform — rotation doesn't rescale)
    rotation_x: Optional[float] = None
    rotation_y: Optional[float] = None
    orientation: Optional[List[float]] = None

    # Per-kind conformed properties
    camera: Optional[ConformedCameraProperties] = None
    light: Optional[ConformedLightProperties] = None

    # Slot 7.5 Phase 3 Stage A — zero-write signal.
    # When True, Babysitter early-returns from `_processLayerTransforms` and
    # writes NOTHING to AE for this layer (no position, no scale, no anchor,
    # no rotation, no camera intrinsics, no light intrinsics). The transform
    # values above are still populated for non-Babysitter consumers (SOE,
    # report generator) but never reach `setValue` round-trips.
    # Defaults False so existing rule sets (narrow, future widen +
    # equal_different_resolution) are unaffected. Only
    # `_apply_preserve_rule_set` sets this True.
    skip_inject: bool = False

    @field_validator("position", "scale", "anchor", "rotation", "rotation_x", "rotation_y", "orientation")
    @classmethod
    def validate_floats(cls, v: Any) -> Any:
        return check_finite_floats(v)


class ConformedKeyData(BaseModel):
    """Scaled and dead-zone-filtered keyframe data for a single property."""
    times: List[float]
    values: List[Any]
    keyInInterpolationType: Optional[List[int]] = None   # AE interpolation type per keyframe
    keyOutInterpolationType: Optional[List[int]] = None

    @field_validator("times", "values")
    @classmethod
    def validate_arrays(cls, v: Any) -> Any:
        return check_finite_floats(v)


class ConformedKeys(BaseModel):
    """All conformed keyframe properties for one layer. None = no keyframes for that property."""
    position: Optional[ConformedKeyData] = None
    # Per-axis position streams for layers with Separate Dimensions on
    # Position. When any of these are set, the injector MUST write them via
    # transform.property("X Position"/"Y Position"/"Z Position") — attempting
    # to set `transform.position` while dimensions are separated raises
    # "property or a parent property is hidden" in AE.
    position_x: Optional[ConformedKeyData] = None
    position_y: Optional[ConformedKeyData] = None
    position_z: Optional[ConformedKeyData] = None
    scale: Optional[ConformedKeyData] = None
    rotation: Optional[ConformedKeyData] = None
    anchor: Optional[ConformedKeyData] = None
    opacity: Optional[ConformedKeyData] = None
    # Camera-specific animated properties — scaled by S during conform
    camera_zoom: Optional[ConformedKeyData] = None
    camera_focusDistance: Optional[ConformedKeyData] = None
    camera_pointOfInterest: Optional[ConformedKeyData] = None


class ConformedEffectParam(BaseModel):
    """One effect or layer-style parameter after scale math is applied.
    Only emitted when scale_rule is non-pass-through — PASS_THROUGH params
    are omitted to keep chunk JSON compact."""
    match_name: str
    display_name: str
    conformed_static: Optional[Any] = None
    conformed_keys: Optional[ConformedKeyData] = None

    @field_validator("conformed_static")
    @classmethod
    def validate_static(cls, v: Any) -> Any:
        return check_finite_floats(v)


class ConformedEffect(BaseModel):
    """One effect instance with its conformable parameters."""
    index: int          # 1-based position in AE effect stack
    match_name: str     # effect matchName for stack lookup
    display_name: str
    params: List[ConformedEffectParam]


class ConformedLayerStyle(BaseModel):
    """One layer style (Drop Shadow, Stroke, etc.) with its conformable parameters."""
    match_name: str     # style matchName for lookup
    display_name: str
    params: List[ConformedEffectParam]


class ConformedLayer(LayerModel):
    """
    A LayerModel extended with its conformed transform data.
    Written by ScaleEngine.conform(), validated by PayloadSlicer before chunking.
    conformed_keys is None until LerpEngine is wired (Sprint #1).
    """
    conformed_transforms: ConformedTransforms
    conformed_keys: Optional[ConformedKeys] = None
    conformed_effects: Optional[List[ConformedEffect]] = None
    conformed_layer_styles: Optional[List[ConformedLayerStyle]] = None


    # ── Gravity group size (PR #121 follow-up) ────────────────────────────
    # Populated by scale_engine_narrow.py's group-aware gravity pre-pass.
    # Number of layers sharing this layer's (containing_comp_id, canonical_tag)
    # group that the pre-pass anchored together. None when the layer didn't
    # go through the group-aware path (GUIDE/PROTECT/structural, or no
    # gravity rule) — distinct from a meaningful group size of 1.
    # Diagnostic only; does not affect conform math. Babysitter ignores it.
    gravity_group_size: Optional[int] = None

    # ── Variant state (PR-V2) ─────────────────────────────────────────────
    # Populated by ScaleEngine.conform() ONLY for layers whose comment
    # carries a `variant:` directive; absent (None) for every other layer,
    # so a manifest from a comp with no directives is unchanged and
    # Babysitter never touches those layers' video switch.
    #
    # `conformed_enabled` is a visibility instruction for the injector, not
    # transform data — it lives in the `conformed_*` namespace because that
    # is the family Babysitter reads, and it is registered in
    # test_unit_invariant.py's AUDITED_ARTIFACT_KEYS so the coverage gate
    # accounts for it.
    #
    # `variant_buckets` is the declared bucket list (diagnostic — drives the
    # report's Variants section; Babysitter ignores it).
    conformed_enabled: Optional[bool] = None
    variant_buckets: Optional[List[str]] = None


class ChunkManifest(BaseModel):
    """
    A 30-layer slice of the conformed layer list.
    max_length=30 enforced by Pydantic — PayloadSlicer will reject oversized chunks.
    Written atomically to Chunks/chunk_NNN.json by exporter.py.
    Read sequentially by Babysitter.jsx.
    """
    chunk_index: int
    layers: List[ConformedLayer] = Field(..., max_length=30)


class MirrorRewire(BaseModel):
    """Slot 12.5 Stage D Item 3 — one precomp-wrapper rewire in the
    mirror tree.

    Each entry tells Babysitter to find a wrapper layer in one mirror
    comp and `replaceSource()` it onto another mirror comp. The pair
    of `(wrapper_in_source_comp_name, target_mirror_source_name)`
    mirrors the source pair `(containing_source, source_of_wrapper)`
    — the wrapper's cross-comp reference recreated in the target tree
    so the conform output renders against target-dimensioned comps
    instead of the original source-dimensioned ones.

    Item 3 ships the rewire mechanism. Item 4 (wrapper transform
    recompute) consumes the now-resized sources to recompute the
    wrapper's stale source-side transforms. Pre-Item-4, the rewires
    point at the right comps but transforms are still stale — that's
    expected and locked in the Stage D exit criterion.

    Babysitter resolves the target mirror via `_state.mirrorComps`
    (keyed by source comp name; populated by Item 2's setupWorkspace
    N-comp path).

    Q3B forks (forward-compat): when a designer flipped a precomp to
    Q3B (per-consumer fork) in the preflight modal, the planner emits
    consumer-keyed placeholders. The `fork_consumer_layer_uid` field
    forward-compatibly carries the consumer identity; Item 3's
    Babysitter resolution falls back to the shared Q3A mirror when
    forks aren't present (which is always the case until a later item
    builds per-consumer fork mirrors). When forks DO exist in
    `_state.mirrorComps` (a future build step), Babysitter consults
    `fork_consumer_layer_uid` to pick the right one.
    """

    wrapper_layer_uid: str = Field(
        min_length=1,
        description="UID of the precomp wrapper layer (stamped by "
                    "Sovereign_Core into layer.comment as `uid:<hex>`). "
                    "Babysitter finds the layer in the mirror comp via "
                    "`findLayerByUID`.",
    )
    wrapper_layer_name: Optional[str] = Field(
        default=None,
        description="Wrapper's name — used as a fallback when uid "
                    "lookup misses (same pattern as the v5.8 LayerRewire "
                    "name fallback at Babysitter._findConformedLayer).",
    )
    wrapper_in_source_comp_name: str = Field(
        min_length=1,
        description="Source comp name that CONTAINS the wrapper layer. "
                    "Babysitter looks up the matching mirror via "
                    "`_state.mirrorComps[wrapper_in_source_comp_name]`. "
                    "Always the wrapper's `containing_comp_id`-resolved "
                    "source name.",
    )
    target_mirror_source_name: str = Field(
        min_length=1,
        description="Source comp name the wrapper currently points at — "
                    "i.e. the comp whose target-dimensioned MIRROR should "
                    "become the wrapper's new .source. Babysitter looks "
                    "up `_state.mirrorComps[target_mirror_source_name]` "
                    "and calls `wrapper.replaceSource(mirror)`.",
    )
    fork_consumer_layer_uid: Optional[str] = Field(
        default=None,
        description="Q3B forward-compat marker. When set, this rewire "
                    "is for a fork-per-consumer override; Babysitter "
                    "looks up a per-consumer fork in mirrorComps (using "
                    "this uid for disambiguation) instead of the shared "
                    "Q3A mirror. Default None = Q3A (one shared mirror).",
    )


class MirrorTreeEntry(BaseModel):
    """Slot 12.5 Stage D Item 2 — one target-dimensioned mirror comp in
    the Q4A mirror tree.

    The orchestrator emits one entry per unique source comp visited by
    Stage B's recursive scrape, de-duped per Q3A (a shared precomp
    referenced N times produces ONE entry, not N). Babysitter reads
    the spec from `chunk_manifest.mirror_tree`, creates each mirror
    comp at the conform target's dimensions, places it in the target
    bin, and copies the source layers in — preserving stack order and
    parent-child links.

    Item 2 ships the comp creation. Items 3 (wrapper rewires) and 4
    (wrapper transform recompute) consume the created mirror tree;
    they are explicitly out of Item 2's scope.

    Ordering: the list is emitted root-first, then nested precomps in
    Stage B's BFS-walk order. Babysitter creates them in that order so
    the root comp is always present when later items wire it up."""

    source_comp_name: str = Field(
        min_length=1,
        description="Name of the source comp the mirror is duplicated from. "
                    "Babysitter looks the comp up by this name (consistent "
                    "with the existing setupWorkspace expected_comp_name "
                    "pattern; AE comp.id can shift across project saves).",
    )
    source_comp_id: Optional[int] = Field(
        default=None,
        description="AE comp.id captured at scrape time. Diagnostic only; "
                    "Babysitter uses name-based lookup as the authoritative "
                    "identity. Helps audit logs trace which AE item produced "
                    "which mirror comp when names collide across precomps.",
    )
    output_name: str = Field(
        min_length=1,
        description="Resolved output comp name (from "
                    "`core.output_naming.resolve_output_name_for_preset`). "
                    "Babysitter writes this verbatim — no template parsing "
                    "JSX-side per Q4 Path A-minus.",
    )
    name_version: int = Field(
        default=1,
        ge=1,
        description="Collision-bump version. 1 means no collision; >= 2 "
                    "means the resolver bumped `_vN`. Matches "
                    "PrecompDuplicate.name_version semantics.",
    )
    is_root: bool = Field(
        default=False,
        description="True for the active comp (the user's conform target). "
                    "False for every nested precomp. Exactly one entry in "
                    "the tree carries is_root=True. The root comp is where "
                    "Babysitter's chunk pump targets today; nested comps "
                    "are scaffolding for Items 3-4 to populate.",
    )
    keep_source_dims: bool = Field(
        default=False,
        description="Scene-preserve layout (2026-07-02): True on nested "
                    "entries when the precomp is a sealed unit — Babysitter "
                    "creates the mirror at the SOURCE comp's dimensions "
                    "instead of the conform target's, and the engine passes "
                    "its internal layers through untouched. Never True on "
                    "the root entry. Default False = legacy behavior "
                    "(every mirror at target dimensions).",
    )
