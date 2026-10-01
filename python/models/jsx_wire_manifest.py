# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/models/jsx_wire_manifest.py
JSX scrape-manifest wire profile — strict validation for fresh scrapes.

ScrapeManifest (scrape_manifest.py) uses extra="allow" so Python-side
enrichment and legacy fields never break runtime parsing. This module
is the *wire* profile: extra="forbid" catches unknown keys JSX should
not emit and keys dropped by V5_LAYER_KEYS allow-list regressions.

Used only when ``is_fresh_jsx_manifest()`` is true — manifests with
``recursive_scrape_meta`` and no ``survey_stats``. Enriched / surveyed
manifests skip wire-strict validation and parse through ScrapeManifest.
"""

from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

# Keys whose absence on a layer is worth auditing when siblings carry them.
CRITICAL_LAYER_WIRE_KEYS = (
    "content_tag",
    "content_tag_source",
    "uid",
    "world_bounds",
    "comment",
)


class RecursiveScrapeMetaWire(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comps_visited: int
    queued_remaining: int
    total_layers: int


class ProjectInfoWire(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    width: int
    height: int
    fps: Optional[float] = 30.0
    preview_path: Optional[str] = None


class JsxLayerWire(BaseModel):
    """Wire shape for one layer in a fresh JSX scrape manifest.

    Required identity fields match what Sovereign_Core.jsx always writes.
    All other keys observed on real session fixtures are Optional so
    extra="forbid" still accepts known JSX output without requiring every
    math field on every layer kind.
    """
    model_config = ConfigDict(extra="forbid")

    # Required identity fields JSX always writes
    id: int
    index: int
    name: str
    uid: str

    # Critical tag round-trip fields (PR #45 guard)
    content_tag: Optional[str] = None
    content_tag_source: Optional[str] = None
    content_tag_confidence: Optional[float] = None
    comment: Optional[str] = None

    # Next-Gen Relayout & Boundaries (PR #292)
    archetype: Optional[str] = None
    artwork_bounds: Optional[Any] = None

    # Optional fields from 87n_fresh_manifest.json layer keys
    parent_index: Optional[int] = None
    parent_uid: Optional[str] = None
    is_dependency: Optional[bool] = None
    isBrittle: Optional[bool] = None
    collapseTransformations: Optional[bool] = None
    layer_kind: Optional[str] = None
    threeD: Optional[bool] = None
    position: Optional[List[float]] = None
    scale: Optional[List[float]] = None
    rotation_x: Optional[float] = None
    rotation_y: Optional[float] = None
    rotation_z: Optional[float] = None
    orientation: Optional[List[float]] = None
    anchor: Optional[List[float]] = None
    camera: Optional[Any] = None
    light: Optional[Any] = None
    breadcrumb: Optional[List[str]] = None
    dependencies: Optional[List[Any]] = None
    expressions: Optional[Any] = None
    temporal_data: Optional[Any] = None
    containing_comp_id: Optional[int] = None
    containing_comp_uid: Optional[str] = None
    wrapper_layer_uid: Optional[str] = None
    nesting_depth: Optional[int] = None
    layer_references: Optional[List[Any]] = None
    flags: Optional[Any] = None
    source_item: Optional[Any] = None
    track_matte: Optional[Any] = None
    time_info: Optional[Any] = None
    properties: Optional[List[Any]] = None
    effects: Optional[List[Any]] = None
    masks: Optional[List[Any]] = None
    layer_styles: Optional[List[Any]] = None
    markers: Optional[List[Any]] = None
    v5_errors: Optional[List[Any]] = None
    profile_suggested_tag: Optional[str] = None
    profile_conflict_reason: Optional[str] = None
    typographic_info: Optional[Any] = None
    # Stage 2 pairing passthrough — DELIBERATELY RETAINED. The runtime
    # LayerModel fields these mirror were deleted in DCE Phase 2c
    # (2026-08-19) along with core/pairing.py, but every manifest Python
    # re-serialized between 2026-06-30 and that date carries
    # "paired_with": null (and siblings) on every layer. Because this model
    # is extra="forbid" and is_fresh_jsx_manifest() mis-classifies
    # Python-rewritten manifests as fresh, removing these four would
    # hard-fail conform on manifests already on users' disks — measured at
    # 92 validation errors on 87n_fresh_manifest.json and 780 on
    # parallax_manifest.json. Same precedent as duplication_plan below
    # (added 2026-07-06 after it halted every conform after a re-save).
    # Do NOT delete without a manifest-migration plan.
    paired_with: Optional[str] = None
    pair_confidence: Optional[float] = None
    pair_source: Optional[str] = None
    pair_candidates: Optional[List[Any]] = None
    source_rect: Optional[List[float]] = None
    world_bounds: Optional[Any] = None
    hero_time: Optional[float] = None
    is_keyed: Optional[Any] = None
    source_coverage: Optional[float] = None
    label: Optional[int] = None
    match_name: Optional[str] = None


class JsxScrapeManifestWire(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["OK", "FAILED"]
    project_info: ProjectInfoWire
    layers: List[JsxLayerWire]
    schema_version: Optional[str] = None
    recursive_scrape_meta: Optional[RecursiveScrapeMetaWire] = None
    active_style_profile_id: Optional[str] = None
    style_proposals: Optional[Any] = None
    # Python-enrichment passthrough — the runtime model's optional
    # duplication_plan serializes as null on manifest rewrites; without
    # this, extra="forbid" halts every conform after a re-save (field
    # added 2026-07-06, caught live on the 87N session).
    duplication_plan: Optional[Any] = None
    comment_garden: Optional[Any] = None
    comment_report: Optional[Any] = None
    # Python-enrichment passthrough — manifests written when AI/Ollama was active
    # carry ai_brief. Because JsxScrapeManifestWire is extra="forbid", removing
    # this field causes extra_forbidden validation errors on manifests already on
    # users' disks (measured in parallax_manifest.json and 87n_fresh_manifest.json).
    # Spared 2026-08-19 in DCE Phase 3a following the paired_with / duplication_plan
    # precedent. Do NOT delete without a manifest-migration plan.
    ai_brief: Optional[str] = None
    errors: Optional[List[Any]] = None
    preflight_report: Optional[Any] = None
    render_queue: Optional[List[Any]] = None
    scrape_meta: Optional[Any] = None
    survey_warnings: Optional[List[str]] = None
    survey_stats: Optional[Any] = None


def is_fresh_jsx_manifest(raw: dict) -> bool:
    """True when manifest looks like a fresh JSX scrape (wire-strict path)."""
    return (
        isinstance(raw, dict)
        and "recursive_scrape_meta" in raw
        and "survey_stats" not in raw
    )


def parse_jsx_wire_manifest(raw: dict) -> JsxScrapeManifestWire:
    """Validate raw dict against the JSX wire profile. Raises ValidationError."""
    return JsxScrapeManifestWire.model_validate(raw)


def audit_critical_layer_keys(manifest: JsxScrapeManifestWire) -> list[str]:
    """Soft audit: warn when a key present on any layer is missing on siblings.

    Returns human-readable warning strings. Does not raise — callers decide
    whether missing keys are hard failures (contract tests may assert empty).
    """
    warnings: list[str] = []
    if not manifest.layers:
        return warnings

    keys_present_globally: set[str] = set()
    for layer in manifest.layers:
        dumped = layer.model_dump(exclude_none=True)
        for key in CRITICAL_LAYER_WIRE_KEYS:
            if key in dumped:
                keys_present_globally.add(key)

    if not keys_present_globally:
        return warnings

    for layer in manifest.layers:
        dumped = layer.model_dump(exclude_none=True)
        missing = keys_present_globally - set(dumped.keys())
        for key in sorted(missing):
            warnings.append(
                f"Layer '{layer.name}' (index {layer.index}) missing "
                f"{key!r} while other layers in manifest carry it"
            )
    return warnings


__all__ = [
    "CRITICAL_LAYER_WIRE_KEYS",
    "JsxLayerWire",
    "JsxScrapeManifestWire",
    "ProjectInfoWire",
    "RecursiveScrapeMetaWire",
    "ValidationError",
    "audit_critical_layer_keys",
    "is_fresh_jsx_manifest",
    "parse_jsx_wire_manifest",
]