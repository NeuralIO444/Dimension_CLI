# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/comment_gardener.py
PR-E.1 — Pre-flight comment gardener.

The AE layer comment field is Dimension's tag persistence layer.
We write `uid:<hex> #TAG` markers there. But studios may use the
same field for render notes, asset DAM IDs, color directives,
footage tracking — anything that fits in a single string. The
gardener is the read-only pre-flight pass that classifies what's
actually in each layer's comment so we know what to expect
before we tag-write.

Read-only. The gardener does not modify any comments. PR-E.2
adds the UI surface (warning banner, foreign-content list,
tag-write confirmation prompts). Subsequent PRs may add
interactive cleanup, namespacing, or coexistence logic.

Classification rules (from PR-E pre-implementation lock-in,
2026-04-28 — see `docs/roadmap/PR-E-preflight-gardener.md` § Locked
decisions). Conservative — anything ambiguous flags as FOREIGN
rather than mis-classifying as a Dimension tag.

  EMPTY                   `comment is None` or `comment.strip()` is empty.
  DIMENSION_TAG           Exactly matches `\\s*uid:<hex> #<TAG>\\s*`
                          or `\\s*#<TAG> uid:<hex>\\s*` (both orders
                          are legitimate Dimension write paths — see
                          issue #371), where `<TAG>` is a canonical
                          id in the REGISTRY.
  DIMENSION_TAG_LEGACY    Exact regex match, but `<TAG>` is a legacy
                          alias (TYPE/KEYART/LEGALS/BACKGROUND). Read
                          path normalises these elsewhere; classifier
                          flags them so the user knows there's
                          pre-canonical-vocab content.
  DIMENSION_TAG_MALFORMED Exact regex match, but `<TAG>` is unknown to
                          the REGISTRY (typo, removed tag, hand-typed
                          mistake).
  MIXED                   Comment contains a `uid:` token AND extra
                          non-whitespace content beyond the strict
                          tag pattern. The studio added context to a
                          previously-tagged layer.
  DIMENSION_DIRECTIVE     Comment is nothing but well-formed artist
                          directives (`variant:`, `nudge:`). Benign:
                          Dimension's own syntax, not studio content.
  FOREIGN                 No `uid:` token, non-empty content. The
                          studio's content. Tag-write would clobber
                          it without confirmation.

PR-V1 (2026-09-01) — comments are classified by what REMAINS after
Dimension's own directive syntax is stripped (see
`docs/knowledge/comment-directive-grammar.md` § 2). Without that, a
layer commented `variant:vertical` classified as FOREIGN, which both
raised the banner and made `surveyor.survey_manifest` skip heuristic
classification for the layer — so asking for a variant silently cost
the layer its tag. A comment with no directives strips to itself, so
every pre-PR-V1 classification is unchanged by construction.

The classifier consumes the `comment` field as JSX writes it —
PR-E.1's prereq commit added that field to the V5_LAYER_KEYS
allow-list and the LayerModel schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from core.comment_directives import has_directives, strip_directives
from core.tag_registry import LEGACY_LABEL_COLOR_MAP as _LEGACY_LABEL_MAP, REGISTRY

# ── Legacy AE label colors ─────────────────────────────────────────────────
#
# `_LEGACY_LABEL_MAP` is imported from `core.tag_registry` (pre-#117
# per-alias colours the live YAML no longer claims). Used by prescan so
# layers coloured under the old 9-tag vocabulary still resolve.
#
# Tags that are "conflicting" when derived from AE label color alone.
# GUIDE: layout guide — never moved by the conform engine.  A designer who
# uses a label color to colour a logo precomp does NOT intend it to be
# excluded from conform; the label is organisational, not semantic.
# TOP: title treatment — a layer coloured blue for visual organisation may
# not actually be a title (e.g. a 3D precomp at Z≠0 labelled blue).
_CONFLICT_TAGS: frozenset = frozenset({"GUIDE", "TOP"})


# ── Tag-write format ─────────────────────────────────────────────
#
# Strict full-match regex — must equal either write order the JSX
# side actually produces. `applyManualTag` (the +TAG button) writes
# `<existing-comment-prefix>uid:<hex> #<TAG>` (uid, then tag).
# `_ensureUidStamped` (the scrape-time identity stamp) appends its
# uid token to whatever the comment already was, so an artist who
# types `#TAG` directly ends up with `#<TAG> uid:<hex>` (tag, then
# uid) — see issue #371. Both are legitimate Dimension-authored
# orderings, so the classifier must accept either. The `<TAG>` is
# uppercase ASCII letters and the uid is lowercase hex with optional
# underscores. The `\s*` flanks tolerate leading / trailing
# whitespace from prior edits but reject any interior junk — that
# path is what produces MIXED.

_DIMENSION_TAG_RE = re.compile(
    r"\s*(?:uid:[a-f0-9_]+ #(?P<tag_a>[A-Z]+)"
    r"|#(?P<tag_b>[A-Z]+) uid:[a-f0-9_]+)\s*"
)

# Bug F C3 (atomic with B) — a bare uid stamp with no tag, no
# foreign content. Emitted by Bug F's scrape-time `_ensureUidStamped`
# call. Strict: only `uid:<hex>` and surrounding whitespace, nothing
# else. Anything else with a `uid:` token falls through to MIXED.
_DIMENSION_STAMPED_RE = re.compile(
    r"\s*uid:[a-f0-9_]+\s*"
)


class CommentClass(Enum):
    """Classification of an AE layer comment by content type."""

    EMPTY = "empty"
    DIMENSION_TAG = "dimension_tag"
    DIMENSION_TAG_LEGACY = "dimension_tag_legacy"
    DIMENSION_TAG_MALFORMED = "dimension_tag_malformed"
    # Bug F: scrape stamps `uid:<hex>` into every layer's comment so
    # findLayerByUID resolves cleanly across rescrapes. A standalone
    # stamp (no tag, no foreign content) is its own benign state —
    # the banner stays quiet on it. Locked decision Q4 in
    # docs/roadmap/bug-f-uid-stamp-and-fallback.md.
    DIMENSION_STAMPED = "dimension_stamped"
    # PR-V1: artist directives (variant:/nudge:) and nothing else.
    # Dimension syntax, so benign — the banner stays quiet and the
    # surveyor still classifies the layer normally.
    DIMENSION_DIRECTIVE = "dimension_directive"
    FOREIGN = "foreign"
    MIXED = "mixed"


# ── Classifier ───────────────────────────────────────────────────


def classify_comment(comment: Optional[str]) -> CommentClass:
    """Classify a single layer's comment string.

    `comment` is the raw value from `LayerModel.comment` (which
    is what JSX writes into the manifest, verbatim). None and
    whitespace-only both fold into EMPTY.

    Conservative on ambiguous input — if a comment contains a
    `uid:` token but doesn't match the strict tag-write format,
    it's MIXED, not DIMENSION_TAG. If it contains neither a
    `uid:` token nor strict-match content, it's FOREIGN.

    Pure function. No I/O. No registry mutation.
    """
    if comment is None or not comment.strip():
        return CommentClass.EMPTY

    # PR-V1 — classify by what the studio actually put there, i.e. the
    # comment minus Dimension's own directive syntax. Malformed
    # directives are deliberately NOT stripped by strip_directives():
    # they do nothing, so they are foreign content and keep the
    # gardener's protection.
    if has_directives(comment):
        remainder = strip_directives(comment)
        if not remainder:
            return CommentClass.DIMENSION_DIRECTIVE
        comment = remainder

    m = _DIMENSION_TAG_RE.fullmatch(comment)
    if m:
        tag = m.group("tag_a") or m.group("tag_b")
        if tag in REGISTRY.all_ids():
            return CommentClass.DIMENSION_TAG
        if tag in REGISTRY.all_aliases():
            return CommentClass.DIMENSION_TAG_LEGACY
        return CommentClass.DIMENSION_TAG_MALFORMED

    # Bug F: bare uid stamp with no tag content — emitted by the
    # scrape-time stamper. Benign (banner stays quiet).
    if _DIMENSION_STAMPED_RE.fullmatch(comment):
        return CommentClass.DIMENSION_STAMPED

    # No strict match. Discriminate by presence of the uid: token.
    if "uid:" in comment:
        return CommentClass.MIXED

    return CommentClass.FOREIGN


# ── Scanner output ───────────────────────────────────────────────


@dataclass(frozen=True)
class LayerSummary:
    """Per-layer entry in the gardener report. Carries enough to
    surface a useful row in the UI without re-walking the manifest.

    PR-E.2: `uid` was added so the UI's "View in AE" button can
    call `bridge.select_layer(uid)` directly without an extra
    manifest lookup per row. Defaults to None for backward
    compatibility with any caller that builds a LayerSummary
    without a uid (existing Layer 1 tests do this)."""

    layer_index: int
    layer_name: str
    comment_preview: str  # first 60 chars of comment, truncated
    uid: Optional[str] = None

    @classmethod
    def from_layer(cls, layer_index: int, layer_name: str,
                   comment: Optional[str],
                   uid: Optional[str] = None) -> "LayerSummary":
        if comment is None:
            preview = ""
        elif len(comment) <= 60:
            preview = comment
        else:
            preview = comment[:57] + "..."
        return cls(
            layer_index=layer_index,
            layer_name=layer_name or "",
            comment_preview=preview,
            uid=uid,
        )


@dataclass(frozen=True)
class CommentReport:
    """Structured gardener report for one scrape pass.

    Consumed by:
    - `python/bridge/sovereign_bridge.py` — attached to the scrape
      result (PR-E.1 commit 4)
    - `python/core/surveyor.py` — uses `by_class[FOREIGN]` to skip
      heuristic classification on flagged layers
    - `python/ui/tagging_page.py` — surfaces the warning banner
      when `has_warnings` (PR-E.2)

    Counts are denormalised from `by_class` for fast UI access.
    `has_warnings` is the single signal the UI uses to decide
    whether to show the banner — true iff any FOREIGN, MALFORMED,
    or MIXED entries exist."""

    total_layers: int
    by_class: dict[CommentClass, List[LayerSummary]] = field(
        default_factory=dict,
    )
    foreign_count: int = 0
    malformed_count: int = 0
    legacy_count: int = 0
    mixed_count: int = 0
    # Bug F C3 — bare uid stamps (no tag yet). Benign; does NOT
    # contribute to has_warnings. Surfaced as a count for gardener
    # INFO log diagnostics.
    stamped_count: int = 0
    # PR-V1 — layers whose comment is directives only. Benign, like
    # stamped_count; surfaced for diagnostics, never for the banner.
    directive_count: int = 0
    has_warnings: bool = False


# ── Scanner ──────────────────────────────────────────────────────


def scan_comp(manifest) -> CommentReport:
    """Walk every layer in `manifest.layers`, classify each
    layer's `comment` field, and assemble a CommentReport.

    `manifest` is a `models.scrape_manifest.ScrapeManifest`
    (Pydantic model). Imported lazily to avoid a circular
    dependency — the manifest module imports nothing from
    `core/`, so this module imports `manifest` only at call
    time via duck-typed access to `manifest.layers`.

    Pure function. No I/O. No mutation of the manifest.

    Performance budget: <250ms for 500 layers. The classifier is
    a regex `fullmatch` per layer plus two set lookups; on modern
    hardware that's well under 1ms per layer for any realistic
    comment length.
    """
    layers = list(getattr(manifest, "layers", []) or [])

    by_class: dict[CommentClass, List[LayerSummary]] = {
        c: [] for c in CommentClass
    }

    for layer in layers:
        index = int(getattr(layer, "index", 0) or 0)
        name = str(getattr(layer, "name", "") or "")
        comment = getattr(layer, "comment", None)
        uid = getattr(layer, "uid", None)

        cls = classify_comment(comment)
        by_class[cls].append(
            LayerSummary.from_layer(index, name, comment, uid=uid),
        )

    foreign_count = len(by_class[CommentClass.FOREIGN])
    malformed_count = len(by_class[CommentClass.DIMENSION_TAG_MALFORMED])
    legacy_count = len(by_class[CommentClass.DIMENSION_TAG_LEGACY])
    mixed_count = len(by_class[CommentClass.MIXED])
    stamped_count = len(by_class[CommentClass.DIMENSION_STAMPED])
    directive_count = len(by_class[CommentClass.DIMENSION_DIRECTIVE])

    # Locked decision Q5 — banner triggers on FOREIGN |
    # DIMENSION_TAG_MALFORMED | MIXED only. DIMENSION_STAMPED and
    # (PR-V1) DIMENSION_DIRECTIVE are benign by design.
    has_warnings = bool(
        foreign_count or malformed_count or mixed_count
    )

    return CommentReport(
        total_layers=len(layers),
        by_class=by_class,
        foreign_count=foreign_count,
        malformed_count=malformed_count,
        legacy_count=legacy_count,
        mixed_count=mixed_count,
        stamped_count=stamped_count,
        directive_count=directive_count,
        has_warnings=has_warnings,
    )


# ── Preflight dataclasses ─────────────────────────────────────────────────


@dataclass
class LabelConflict:
    """A layer whose AE label color resolves to a tag that is likely
    not what the designer intended (e.g. GUIDE on a logo precomp,
    TOP on a 3D world-space precomp).  The label color is a visual
    organisational tool; the fact that Dimension reads a semantic tag
    from it can silently break the conform.

    `suggested_fix` tells the user the cheapest override — writing a
    `#TAG` comment via the +TAG button makes the intent explicit and
    prevents the label-colour read from firing in Pass 1."""

    layer_index: int
    layer_name: str
    ae_label_color: int
    resolved_tag: str   # the tag the label colour resolves to
    suggested_fix: str  # e.g. "Apply #CENTER via +TAG button to override permanently"


@dataclass
class ExpressionIssue:
    """A layer that carries an active AE expression on one or more
    transform properties.  Expressions interact with the conform
    pipeline in unpredictable ways (wiggle, loopOut, value offsetting).
    Babysitter strips expressions before applying new keyframes, which
    can silently break animation intent.  The prescan surfaces these so
    the designer can decide whether to bake or adjust the expression
    before conforming.

    `expression_snippet` is the first 100 characters of the expression
    string — enough to identify it, not enough to drown the report."""

    layer_index: int
    layer_name: str
    property_name: str
    expression_snippet: str  # first 100 chars


@dataclass
class SafeZoneGap:
    """The requested target preset has no safe-zone mask on disk.

    SOE (Spatial Occlusion Engine) will skip the occlusion-aware nudge
    pass for this preset, which means TYPE/LEGALS layers may land under
    platform UI chrome (TikTok caption bar, Instagram story tray, etc.).
    The designer should either add a `<preset_slug>.png` mask or accept
    that SOE will be inert for this format."""

    preset_slug: str
    message: str


@dataclass
class CameraDepthWarning:
    """Preflight warning for a camera whose depth-axis scaling mode
    was determined by animation detection.

    `animated` — True when the camera has keyframes on position Z,
    zoom, or focusDistance (K mode is used to preserve dolly/focus
    intent). False for static cameras (S mode, matches Scale
    Composition uniform-scale behavior).

    `animated_fields` — list of field names that triggered the K
    decision (e.g. ["position_z", "zoom"]).

    `recommended_mode` — "K" or "S".

    `warning_copy` — one-line human-readable warning generated by
    Ollama (or a deterministic fallback when Ollama is unavailable).
    """

    animated: bool
    animated_fields: List[str]
    recommended_mode: str   # "K" | "S"
    warning_copy: str


@dataclass
class OrphanedLabelColor:
    """A layer carrying a nonzero AE label color that the current tag
    registry doesn't claim at all — not even via `_LEGACY_LABEL_MAP`.

    Almost always means the layer was manually tagged under an older
    tag vocabulary whose label colors have since been reassigned or
    consolidated (e.g. the pre-#117 9-tag scheme, where CENTER's
    aliases — ARTWORK, BOXART, HERO, LOGO — each had their own distinct
    label color before the 4-tag simplification kept only one). The
    manual classification silently vanishes on rescan — the layer falls
    through to heuristic/AI with no warning to the artist unless this
    check catches it. Skipped for layers whose final tag still came
    from a comment/name override, since those take precedence over
    label color regardless and the orphaned color is harmless there.

    `current_tag` / `current_tag_source` show what the layer actually
    got classified as instead, so the artist can judge whether that's
    acceptable or needs a manual re-tag.
    """

    layer_index: int
    layer_name: str
    ae_label_color: int
    current_tag: Optional[str]
    current_tag_source: Optional[str]


@dataclass
class PreflightReport:
    """Structured pre-conform diagnostic report produced by
    `prescan_comp()`.

    Consumed by:
    - CEP tagging banner: shows specific warnings per conflict
    - Report generator: includes prescan warnings in the HTML report

    All lists may be empty (a clean comp produces an empty report).
    `camera_depth_warning` is None when no cameras are present.
    """

    label_conflicts: List[LabelConflict]
    expression_issues: List[ExpressionIssue]
    safe_zone_gaps: List[SafeZoneGap]
    camera_depth_warning: Optional["CameraDepthWarning"] = None
    orphaned_label_colors: List[OrphanedLabelColor] = field(default_factory=list)


# ── Prescan ───────────────────────────────────────────────────────────────


def prescan_comp(manifest, target_preset_slug: Optional[str] = None) -> PreflightReport:
    """Pre-conform diagnostic scan.

    Checks:
    1. Label color conflicts — layers where the AE label color resolves
       to GUIDE or TOP (tags that can silently break conforms when set
       by organisational label colouring rather than intentional semantic
       tagging).
    2. Expression issues — layers with active expressions on transform
       properties.
    3. Safe-zone gaps — the target preset has no safe-zone mask on disk
       (SOE will be inert for that format).

    `manifest` is a `models.scrape_manifest.ScrapeManifest` (or any
    object with a `.layers` iterable of duck-typed layer objects).
    `target_preset_slug` is the slug for the intended conform target
    (e.g. `"builtin_tiktok_video"`).  When None, the safe-zone check
    is skipped.

    Pure function — does not modify the manifest.  Never raises; all
    sub-checks degrade gracefully on missing fields.
    """
    # ── Build AE label → tag map ─────────────────────────────────────
    # Primary: live registry.  Supplement with the legacy map for label
    # colours that the registry no longer claims (e.g. label 15 → GUIDE
    # before Sprint 1 Fix 1 removed the mapping).
    label_to_tag: dict = dict(_LEGACY_LABEL_MAP)
    for tag_def in REGISTRY.tags:
        label = tag_def.ae_label_color
        if label != 0:
            label_to_tag[label] = tag_def.id

    layers = list(getattr(manifest, "layers", []) or [])

    label_conflicts: List[LabelConflict] = []
    expression_issues: List[ExpressionIssue] = []
    orphaned_label_colors: List[OrphanedLabelColor] = []

    for layer in layers:
        index = int(getattr(layer, "index", 0) or 0)
        name = str(getattr(layer, "name", "") or "")

        # ── 1. Label color conflict / orphaned color check ───────────
        # `label` is the raw AE label integer stored directly on
        # LayerModel (the v5 scraper writes it from `layer.label`).
        label = int(getattr(layer, "label", 0) or 0)
        if label != 0:
            resolved = label_to_tag.get(label)
            if resolved and resolved in _CONFLICT_TAGS:
                label_conflicts.append(LabelConflict(
                    layer_index=index,
                    layer_name=name,
                    ae_label_color=label,
                    resolved_tag=resolved,
                    suggested_fix=(
                        "Apply #CENTER via +TAG button to override permanently"
                    ),
                ))
            elif resolved is None:
                # Nonzero label, but no current tag (and no legacy-map
                # entry) claims this color — see OrphanedLabelColor
                # docstring. Skip if a comment/name override already
                # produced the final classification; label color is
                # lowest-priority among manual sources, so it being
                # orphaned is harmless in that case.
                # 
                # Also skip if the label matches AE's default color for
                # the layer's type (e.g. Precomp=14, Text=1).
                
                layer_kind = str(getattr(layer, "layer_kind", "unknown") or "unknown")
                source_item = getattr(layer, "source_item", None)
                if source_item:
                    source_kind = getattr(source_item, "kind", "unknown") or "unknown"
                else:
                    source_kind = "unknown"
                
                is_default_color = False
                if layer_kind == "camera" and label == 4: is_default_color = True
                elif layer_kind == "light" and label == 6: is_default_color = True
                elif layer_kind == "shape" and label == 8: is_default_color = True
                elif layer_kind == "text" and label == 1: is_default_color = True
                elif source_kind == "solid" and label == 1: is_default_color = True
                elif source_kind in ("footage", "file", "unknown") and label == 1: is_default_color = True
                elif source_kind == "comp" and label == 14: is_default_color = True
                
                # Check for Null layer (usually layer_kind doesn't specifically say null, it relies on flags)
                flags = getattr(layer, "flags", None)
                if flags:
                    # In older models, nullLayer might not exist, use safe dict lookup if it's a dict
                    if hasattr(flags, "nullLayer") and getattr(flags, "nullLayer", False) and label == 13:
                        is_default_color = True
                    elif isinstance(flags, dict) and flags.get("nullLayer", False) and label == 13:
                        is_default_color = True

                final_source = str(getattr(layer, "content_tag_source", "") or "")
                if not is_default_color and \
                        not final_source.startswith("manual_comment") and \
                        not final_source.startswith("manual_name"):
                    orphaned_label_colors.append(OrphanedLabelColor(
                        layer_index=index,
                        layer_name=name,
                        ae_label_color=label,
                        current_tag=getattr(layer, "content_tag", None),
                        current_tag_source=final_source or None,
                    ))

        # ── 2. Expression issue check ────────────────────────────────
        # `expressions` is a Dict[str, Optional[str]] on LayerModel.
        # Any non-empty string value means an active expression is set
        # on that property.
        expressions = getattr(layer, "expressions", None) or {}
        for prop_name, expr_str in expressions.items():
            if expr_str and str(expr_str).strip():
                snippet = str(expr_str)[:100]
                expression_issues.append(ExpressionIssue(
                    layer_index=index,
                    layer_name=name,
                    property_name=str(prop_name),
                    expression_snippet=snippet,
                ))

    # ── 3. Safe-zone gap check ───────────────────────────────────────
    safe_zone_gaps: List[SafeZoneGap] = []
    if target_preset_slug is not None:
        try:
            from logic.safe_zone_resolver import available_masks as _available_masks
            available = _available_masks()
            if target_preset_slug not in available:
                safe_zone_gaps.append(SafeZoneGap(
                    preset_slug=target_preset_slug,
                    message=(
                        "No safe-zone mask found for preset "
                        + repr(target_preset_slug)
                        + " — SOE will be skipped for this format"
                    ),
                ))
        except Exception:
            # safe_zone_resolver unavailable (e.g. tests without the
            # full repo on PYTHONPATH).  Degrade silently.
            pass

    # ── 4. Camera depth-axis mode detection ─────────────────────────
    # Cameras with animated depth (position Z, zoom, or focusDistance
    # keyframes) should use K mode to preserve dolly/focus intent.
    # Static cameras use S (uniform scale, matches Scale Composition).
    camera_depth_warning: Optional[CameraDepthWarning] = None
    _depth_fields = ("position_z", "camera_zoom", "camera_focusDistance")
    _cam_layers = [l for l in layers
                   if str(getattr(l, "layer_kind", "") or "") == "camera"]
    if _cam_layers:
        animated_fields: List[str] = []
        for _cam in _cam_layers:
            td = getattr(_cam, "temporal_data", None)
            if td is not None:
                for _field in _depth_fields:
                    _stream = getattr(td, _field, None)
                    if _stream is not None:
                        _times = (
                            _stream.get("times") if isinstance(_stream, dict)
                            else getattr(_stream, "times", None)
                        )
                        if _times:
                            _canonical = _field.replace("camera_", "")
                            if _canonical not in animated_fields:
                                animated_fields.append(_canonical)
        animated = bool(animated_fields)
        recommended_mode = "K" if animated else "S"

        warning_copy = ""

        if not warning_copy:
            if animated:
                warning_copy = (
                    "Camera has depth animation ("
                    + ", ".join(animated_fields)
                    + ") — using perspective mode (K). Override to S?"
                )
            else:
                warning_copy = (
                    "Camera is static — using uniform scale (S) "
                    "to match source proportions. Override to K?"
                )

        camera_depth_warning = CameraDepthWarning(
            animated=animated,
            animated_fields=animated_fields,
            recommended_mode=recommended_mode,
            warning_copy=warning_copy,
        )

    return PreflightReport(
        label_conflicts=label_conflicts,
        expression_issues=expression_issues,
        safe_zone_gaps=safe_zone_gaps,
        camera_depth_warning=camera_depth_warning,
        orphaned_label_colors=orphaned_label_colors,
    )


__all__ = [
    "CameraDepthWarning",
    "CommentClass",
    "CommentReport",
    "ExpressionIssue",
    "LabelConflict",
    "LayerSummary",
    "OrphanedLabelColor",
    "PreflightReport",
    "SafeZoneGap",
    "classify_comment",
    "prescan_comp",
    "scan_comp",
]
