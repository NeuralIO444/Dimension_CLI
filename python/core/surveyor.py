# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/surveyor.py
Dimension Engine — Semantic Layer Surveyor

Ported and adapted from Aspect Architect v5.7.0 (Surveyor.ts + LayerTagging.ts).

Classifies each layer in a ScrapeManifest with a semantic content tag:
    TOP | BOTTOM | FILL | CENTER | OVERLAY | GUIDE | PROTECT | NULL

v5.10.1 — heuristic emission migrated from the legacy vocabulary
(TYPE/LEGALS/BACKGROUND/KEYART) to the canonical vocabulary
(TT/LGL/BG/HERO) so the surveyor and SovCore_Layer.jsx emit the same
identifiers. v6.0 — migrated to 4-tag canonical vocabulary
(TOP/BOTTOM/FILL/CENTER); legacy forms still pass Pass 1 because
VALID_TAGS includes both canonical ids and aliases; conform-pipeline
tag comparisons normalize via REGISTRY.normalize() so the two forms
are treated identically downstream.

Two-pass strategy:
    1. Manual tags (comment/#syntax, name [BRACKET] syntax, label color)
       — already populated by SovCore_Layer.jsx at scrape time.
       — confidence = 1.0, source = "manual_*"
    2. Heuristic scoring (keyword + spatial DNA)
       — runs Python-side on the manifest, no AE required.
       — confidence = scored 0.0–0.99, source = "heuristic"

Structural pre-classifier always runs first and hard-overrides:
    - camera / light        → skip (protect=True, tag=None)
    - null_layer            → NULL (protect=True) — own bucket, not "unclassified"
    - adjustment layer      → skip (protect=True, tag=None)
    - guide layer flag      → GUIDE (protect=True)

    NOTE: source_item is an AE runtime object never serialized into the
    manifest JSON. Layers without source_item fall through to keyword scoring
    rather than being treated as ghost layers — see _spatial_dna() for details.

Usage:
    from core.surveyor import survey_manifest
    survey_manifest(manifest)   # mutates content_tag fields in-place
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from core.alpha_hull import classify_layer_archetype
from core.layer_utils import canon_tag, is_structural
from core.logger import log
from models.scrape_manifest import LayerModel

# ── Load keyword dictionaries ─────────────────────────────────────────────────

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_HEURISTICS_PATH = os.path.join(_DATA_DIR, "heuristics.json")

# Phase 1 (loud failures) — records why the heuristics file failed to load
# at import time so survey_manifest() can surface it per run instead of the
# surveyor silently scoring every layer with empty keyword tables.
_HEURISTICS_LOAD_ERROR: Optional[str] = None


def _load_heuristics() -> dict:
    global _HEURISTICS_LOAD_ERROR
    try:
        with open(_HEURISTICS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        _HEURISTICS_LOAD_ERROR = str(e)
        return {"keywords": {}, "spatial_weights": {}}

_HEURISTICS = _load_heuristics()
_KEYWORDS: dict = _HEURISTICS.get("keywords", {})
_WEIGHTS: dict  = _HEURISTICS.get("spatial_weights", {})

# ── Blend-mode decode table (AE integer id → slug) ───────────────────────────
# Source: After Effects scripting DOM BlendingMode enumeration.
# Used by Rule 1 (compositing blend → BG) and Rule 7b (isBrittle + blend → BG).
_AE_BLEND_MODE_SLUGS = {
    "5212": "normal",       "5213": "dissolve",     "5215": "darken",
    "5216": "multiply",     "5217": "color_burn",   "5218": "linear_burn",
    "5220": "add",          "5221": "lighten",      "5222": "screen",
    "5223": "color_dodge",  "5224": "linear_dodge", "5226": "overlay",
    "5227": "soft_light",   "5228": "hard_light",   "5229": "linear_light",
    "5230": "vivid_light",  "5231": "pin_light",    "5233": "difference",
    "5234": "exclusion",    "5235": "subtract",     "5236": "divide",
    "5237": "hue",          "5238": "saturation",   "5239": "color",
    "5240": "luminosity",
}

# Blend modes that typically indicate compositing elements (grain, grunge,
# texture, VFX overlays) rather than content layers. These fire Rule 1 and
# Rule 7b to route such layers to BG.
_COMPOSITING_BLEND_MODES = frozenset({
    "screen", "add", "overlay", "multiply", "soft_light",
    "hard_light", "linear_light", "vivid_light", "color_dodge", "linear_dodge",
})


def _get_blend_slug(layer) -> str:
    """Decode a layer's blend mode to a lowercase slug.

    Returns "normal" when flags or blend_mode is absent. Also handles
    string slugs passed through from newer scrape paths (falls back to
    dict lookup so integer-encoded and slug-encoded blend modes both work).
    """
    flags = getattr(layer, "flags", None)
    if flags is None:
        return "normal"
    raw = getattr(flags, "blend_mode", "normal") or "normal"
    return _AE_BLEND_MODE_SLUGS.get(str(raw), str(raw).lower() if isinstance(raw, str) else "normal")

# ── Tag vocabulary ────────────────────────────────────────────────────────────

# v5.10: VALID_TAGS derives from the canonical tag registry at
# `config/tag_registry.yaml`. v5.10.1 migrated heuristic emission to
# canonical form (TT/HERO/LGL/BG); the alias half of VALID_TAGS
# remains so older AE comps tagged with legacy forms (TYPE/KEYART/
# LEGALS/BACKGROUND in the layer comment) still pass Pass 1
# unchanged. Aliases stay until v6.0 at the earliest, after legacy
# user data has been migrated.
from core.tag_registry import REGISTRY as _TAG_REGISTRY

VALID_TAGS = frozenset(
    _TAG_REGISTRY.all_ids() | _TAG_REGISTRY.all_aliases()
)


_canon = canon_tag

# Structural layer types — cameras, lights, nulls, adjustment layers are never
# content layers. They get skipped or marked PROTECT.
_STRUCTURAL_MATCHNAMES = frozenset({
    "ADBE Camera Layer",
    "ADBE Light Layer",
})

# ── Natural Comment Keywords Dictionary ─────────────────────────────────────────
_NATURAL_COMMENT_KEYWORDS: dict[str, str] = {
    # CENTER / HERO ANCHOR
    "hero": "CENTER", "logo": "CENTER", "packshot": "CENTER", "product": "CENTER",
    "artwork": "CENTER", "center": "CENTER", "focus": "CENTER", "anchor": "CENTER",
    "foreground": "CENTER", "keyart": "CENTER",
    # TOP / MAIN TITLE
    "title": "TOP", "headline": "TOP", "header": "TOP", "mt": "TOP",
    "tt": "TOP", "supertitle": "TOP", "type": "TOP",
    # FILL / BACKGROUND
    "bg": "FILL", "background": "FILL", "plate": "FILL", "fill": "FILL",
    "solid": "FILL", "environment": "FILL", "texture": "FILL", "backdrop": "FILL",
    # BOTTOM / LEGALS
    "legals": "BOTTOM", "legal": "BOTTOM", "disclaimer": "BOTTOM", "footnote": "BOTTOM",
    "cta": "BOTTOM", "terms": "BOTTOM", "disclaim": "BOTTOM", "lgl": "BOTTOM",
    # OVERLAY
    "overlay": "OVERLAY", "grain": "OVERLAY", "dust": "OVERLAY", "vignette": "OVERLAY",
    # PROTECT
    "protect": "PROTECT", "lock": "PROTECT", "locked": "PROTECT", "ignore": "PROTECT",
    "hold": "PROTECT", "ref": "PROTECT", "reference": "PROTECT",
    # GUIDE
    "guide": "GUIDE", "grid": "GUIDE",
}


def _extract_natural_comment_keyword(comment: Optional[str]) -> Optional[str]:
    """Extract semantic tag from natural words in a layer's comment string."""
    if not comment or not isinstance(comment, str) or not comment.strip():
        return None
    # Tokenize into alphanumeric word tokens
    words = re.findall(r'[A-Za-z0-9_]+', comment.lower())
    for w in words:
        if w in _NATURAL_COMMENT_KEYWORDS:
            return _NATURAL_COMMENT_KEYWORDS[w]
    return None


# ── TagResult ────────────────────────────────────────────────────────────────

class TagResult:
    __slots__ = ("tag", "source", "confidence", "reason", "protect")

    def __init__(
        self,
        tag: Optional[str],
        source: Optional[str],
        confidence: float,
        reason: str,
        protect: bool = False,
    ):
        self.tag = tag
        self.source = source
        self.confidence = confidence
        self.reason = reason
        self.protect = protect

    def __repr__(self) -> str:
        return (
            f"TagResult(tag={self.tag!r}, source={self.source!r}, "
            f"conf={self.confidence:.2f}, protect={self.protect})"
        )


# ── Structural pre-classifier ─────────────────────────────────────────────────

def _structural_classify(layer: LayerModel) -> Optional[TagResult]:
    """
    Hard overrides for layers whose type is definitively known.
    Returns a TagResult (skip scoring) or None (fall through to scoring).

    Camera / Light  → tag=None, protect=True, source="structural"
    Null layer      → tag="NULL", protect=True, source="structural"
    Guide flag      → tag=GUIDE, protect=True, source="structural"

    Adjustment layers are NOT caught here — they fall through to
    `_score_layer` where Rule 2 classifies them as BG (conf 0.97).
    This allows adjustment layers to participate in the conform pipeline
    rather than being silently excluded.

    No source_item guard: source_item is an AE runtime ref never serialized
    to manifest JSON.  Treating None source_item as a structural skip would
    kill heuristic classification for ALL av layers in legacy manifests.
    Layers without source_item fall through to keyword scoring instead.
    """
    if is_structural(layer):
        layer_kind = (getattr(layer, "layer_kind", None) or "av").lower()
        if layer_kind in ("camera", "light"):
            return TagResult(None, "structural", 1.0, "[STRUCTURAL: Camera/Light — skipped]", protect=True)

    flags = getattr(layer, "flags", None)
    if flags:
        # Null objects — layout controls, not content. Own tag so they
        # count in their own bucket instead of disappearing into
        # "unclassified" or the generic structural_skip total.
        if getattr(flags, "null_layer", False):
            return TagResult("NULL", "structural", 1.0, "[STRUCTURAL: Null Object]", protect=True)

        # NOTE: Adjustment layers are NOT classified as structural here.
        # They are FX compositing layers that should be tagged BG (Rule 2
        # in _score_layer). Routing them through structural_classify would
        # give them tag=None and protect=True, which excludes them from
        # the gravity pipeline entirely — but adjustment layers live in
        # the layer stack and their position matters for conform.

        # AE guide layer flag
        if getattr(flags, "guide", False):
            # Logo/brand precomps sometimes have the AE Guide flag set
            # by mistake. If the layer is a precomp AND its name contains
            # a logo keyword, fall through to heuristic scoring → CENTER.
            # Requires BOTH conditions (precomp + logo name) to avoid
            # misclassifying guide solids named "logo_bg" etc.
            _is_precomp = (
                layer.source_item is not None
                and layer.source_item.kind == "comp"
            )
            _name_lower = (getattr(layer, "name", "") or "").lower()
            _LOGO_KWS = ("logo", "brand", "lock", "ident")
            _is_logo_name = any(_kw in _name_lower for _kw in _LOGO_KWS)
            if not (_is_precomp and _is_logo_name):
                return TagResult("GUIDE", "structural", 1.0,
                                 "[STRUCTURAL: AE Guide Layer]", protect=True)
            # else: fall through to heuristic scoring (→ CENTER via keyword)

    return None


# ── Keyword scoring ───────────────────────────────────────────────────────────

def _name_score(name_lower: str, tag: str) -> float:
    """Return 0.5 if any keyword for `tag` appears in `name_lower`, else 0."""
    name_normalized = name_lower.replace("_", " ").replace("-", " ")
    for kw in _KEYWORDS.get(tag, []):
        if kw in name_lower or kw in name_normalized:
            return 0.5
    return 0.0


# ── Spatial DNA ───────────────────────────────────────────────────────────────

def _spatial_dna(layer: LayerModel, layer_index: int, total_layers: int,
                 comp_width: int = 0, comp_height: int = 0) -> dict:
    """
    Resolution-independent spatial signals derived from the manifest.
    Returns a dict of bool/float signals used by the scoring matrix.

    v5.5 Tier B — when v5.2.5 SOE-Scrape `source_rect` is populated, we
    can compute real fill ratios. Pre-SOE manifests still work; the
    fill signals just stay 0 / False for those layers.

    Stack-position signals always work; text-layer flag works when
    `layer.flags.is_text_layer` is set by the JSX scrape.
    """
    bot_off  = int(_WEIGHTS.get("bottom_stack_offset", 2))
    near_off = int(_WEIGHTS.get("near_bottom_offset", 4))
    top_off  = int(_WEIGHTS.get("top_stack_offset", 2))
    near_top_off = int(_WEIGHTS.get("near_top_offset", 4))
    is_bottom_stack = layer_index >= max(1, total_layers - bot_off)
    is_near_bottom  = layer_index >= max(1, total_layers - near_off)
    is_top_stack = layer_index <= top_off
    is_near_top  = layer_index <= near_top_off

    # v5.5 Tier B — coverage signals from SOE-Scrape source_rect.
    # `source_rect` is {top, left, width, height} in source-comp pixel
    # space. Compute width/height fill ratio against comp dimensions.
    w_fill = 0.0
    h_fill = 0.0
    is_fullscreen = False
    is_wide_strip = False

    # Prefer world_bounds (in comp space) over source_rect (layer-local).
    # world_bounds is dict {top, right, bottom, left}; source_rect is
    # a 4-list [left, top, width, height] of layer-local size.
    wb = getattr(layer, "world_bounds", None)
    sr = getattr(layer, "source_rect", None)
    has_source = wb is not None or sr is not None

    if wb is not None and comp_width > 0 and comp_height > 0:
        # Schema uses {"l", "t", "r", "b"} short keys per
        # `models.scrape_manifest.LayerModel.world_bounds` validator.
        try:
            wb_w = float(wb.get("r", 0)) - float(wb.get("l", 0))
            wb_h = float(wb.get("b", 0)) - float(wb.get("t", 0))
            w_fill = abs(wb_w) / comp_width
            h_fill = abs(wb_h) / comp_height
            is_fullscreen = w_fill >= 0.7 and h_fill >= 0.7
            is_wide_strip = w_fill >= 0.7 and h_fill < 0.25
        except (TypeError, ValueError, AttributeError):
            pass
    elif sr is not None and comp_width > 0 and comp_height > 0:
        # source_rect [left, top, width, height] — layer-local. Use as a
        # weaker signal than world_bounds; only meaningful when scale=100.
        try:
            sr_w = float(sr[2]) if len(sr) > 2 else 0.0
            sr_h = float(sr[3]) if len(sr) > 3 else 0.0
            w_fill = sr_w / comp_width
            h_fill = sr_h / comp_height
            is_fullscreen = w_fill >= 0.7 and h_fill >= 0.7
            is_wide_strip = w_fill >= 0.7 and h_fill < 0.25
        except (TypeError, ValueError, IndexError):
            pass

    # Text-layer flag: works when JSX serialised `flags.is_text_layer`.
    flags = getattr(layer, "flags", None)
    is_text = bool(getattr(flags, "is_text_layer", False)) if flags else False

    # ── Typographic DNA (TASK-ENG-01 / ADR 01 / Pre-Mortem Scenario 2) ──
    r_typo = 0.0
    typo = getattr(layer, "typographic_info", None)
    if typo is not None and comp_height > 0:
        fs = getattr(typo, "font_size_pt", 0.0) or 0.0
        try:
            fs_val = float(fs)
            if fs_val > 0.0 and not (fs_val != fs_val) and not (fs_val == float("inf") or fs_val == float("-inf")):
                r_typo = fs_val / float(comp_height)
        except (TypeError, ValueError):
            r_typo = 0.0

    # ADR 01: R_typo < 0.025 (<2.5% comp height) -> micro-text (LEGAL/DISCLAIMER)
    #         R_typo > 0.060 (>6.0% comp height) -> macro-text (HERO/TITLE/HEADLINE)
    is_micro_text = (is_text and r_typo > 0.0 and r_typo < 0.025)
    is_macro_text = (is_text and r_typo > 0.060)

    return {
        "w_fill": w_fill,
        "h_fill": h_fill,
        "is_fullscreen": is_fullscreen,
        "is_wide_strip": is_wide_strip,
        "is_bottom_stack": is_bottom_stack,
        "is_near_bottom": is_near_bottom,
        "is_top_stack": is_top_stack,
        "is_near_top": is_near_top,
        "is_text": is_text,
        "has_source": has_source,
        "r_typo": r_typo,
        "is_micro_text": is_micro_text,
        "is_macro_text": is_macro_text,
    }


# ── v5.5 Tier B: Filename-pattern signals ───────────────────────────────────
#
# Augment the keyword lexicon (data/heuristics.json) with a second pass that
# looks at filename extensions and substring patterns common in client work.
# Field-test 2026-04-25: heuristic missed `87N.mov` (classified as ARTWORK
# instead of HERO/LOGO). Filename-pattern boosts catch the kind of evidence
# that lives in the *name* but not in the structural lexicon.

_VIDEO_EXTS = (".mov", ".mp4", ".mkv", ".m4v", ".avi", ".webm")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".psd")

# Substring patterns. Order matters — first match wins for `pattern_tag`.
# Boosts ≥ 0.55 fire alone (above the 0.5 min_score_threshold). Lower
# boosts only contribute when stacked with a lexicon match.
_FILENAME_PATTERNS = (
    # (substring, suggested_tag, score_boost)
    # v6.0: tag column migrated to 4-tag canonical vocab (TOP/BOTTOM/FILL/CENTER).
    ("backplate",  "FILL",   0.60),       # strongest FILL signal
    ("grunge",     "FILL",   0.55),       # field-test 2026-04-25
    ("texture",    "FILL",   0.55),
    ("plate",      "FILL",   0.55),
    ("outline",    "TOP",    0.55),       # "EVERYTHING Outlines" → TOP
    ("legal",      "BOTTOM", 0.55),
    ("rating",     "BOTTOM", 0.55),
    ("disclaimer", "BOTTOM", 0.55),
    ("logo",       "CENTER", 0.40),       # CENTER lexicon already covers this
    ("lockup",     "CENTER", 0.40),
    ("brandmark",  "CENTER", 0.40),
    ("wordmark",   "CENTER", 0.40),
    ("hero",       "CENTER", 0.40),
    ("keyart",     "CENTER", 0.40),
    ("noise",      "FILL",   0.30),
    ("paper",      "FILL",   0.30),
    ("mentions",   "BOTTOM", 0.55),
    ("hinweis",    "BOTTOM", 0.55),
    ("titre",      "TOP",    0.55),
    ("titel",      "TOP",    0.55),
    ("titulo",     "TOP",    0.55),
    ("título",     "TOP",    0.55),
    ("titolo",     "TOP",    0.55),
    ("fondo",      "FILL",   0.55),
    ("fond",       "FILL",   0.55),
    ("sfondo",     "FILL",   0.55),
)


def _filename_signals(name_lower: str) -> dict:
    """v5.5 Tier B — extract filename-derived hints. Returns:
        is_video       — name ends in a known video extension
        is_image       — name ends in a known image extension
        pattern_tag    — best-match tag from substring patterns, or None
        pattern_boost  — score to add to that tag (0..0.5)

    Cheap (one lower-cased pass) and runs alongside the existing
    keyword lexicon. The lexicon stays authoritative for tags it
    covers; this layer adds tag candidates the lexicon didn't pick.
    """
    is_video = name_lower.endswith(_VIDEO_EXTS)
    is_image = name_lower.endswith(_IMAGE_EXTS)
    pattern_tag: Optional[str] = None
    pattern_boost = 0.0
    for substr, tag, boost in _FILENAME_PATTERNS:
        if substr in name_lower:
            pattern_tag = tag
            pattern_boost = boost
            break
    return {
        "is_video": is_video,
        "is_image": is_image,
        "pattern_tag": pattern_tag,
        "pattern_boost": pattern_boost,
    }


# ── Scoring matrix ────────────────────────────────────────────────────────────

def _score_layer(layer: LayerModel, layer_index: int, total_layers: int,
                 comp_width: int = 0, comp_height: int = 0) -> TagResult:
    """
    Keyword + spatial DNA scoring matrix. Mirrors Surveyor.ts analyzeLayerStructure().
    Returns best-scoring TagResult, or unclassified (tag=None, confidence=0).

    v5.5 Tier B — also consults filename-pattern signals + SOE-Scrape
    coverage data when available. Field-test gap was layers like
    `87N.mov` and `Contrast_BW_Grunge_4.jpg` whose *filenames* clearly
    suggest content type but whose *structural lexicon* was generic.
    """
    name_lower = (getattr(layer, "name", "") or "").lower()
    dna = _spatial_dna(layer, layer_index, total_layers,
                       comp_width=comp_width, comp_height=comp_height)
    fn = _filename_signals(name_lower)

    best_tag: Optional[str] = None
    best_score: float = 0.0
    best_reason = ""

    def _set(tag: str, score: float, reason: str) -> None:
        nonlocal best_tag, best_score, best_reason
        if score > best_score:
            best_score = score
            best_tag = tag
            best_reason = reason

    # ── Rule 2: Adjustment layer → FILL, conf 0.97 (early return) ────────────
    # Adjustment layers are full-frame FX processors by definition — they
    # apply to everything below them in the comp. No ambiguity. Was previously
    # caught in _structural_classify as tag=None; now routed to FILL so they
    # participate in the conform gravity pipeline.
    flags = getattr(layer, "flags", None)
    if flags and getattr(flags, "adjustment", False):
        return TagResult("FILL", "heuristic", 0.97, "[Rule 2: Adjustment Layer → FILL]")

    # ── Rule 1: Compositing blend mode → FILL, conf 0.82 ─────────────────────
    # Screen / Add / Overlay / Multiply etc. on an AV layer = full-frame
    # compositing pass (grain, shake, glow, colour grade). Fires before
    # keyword scoring so a layer named "Shake" with blend=Screen → FILL, not
    # CENTER.
    layer_kind = (getattr(layer, "layer_kind", None) or "av").lower()
    blend_slug = _get_blend_slug(layer)
    if blend_slug in _COMPOSITING_BLEND_MODES and layer_kind == "av":
        _set("FILL", 0.82, f"[Rule 1: Blend mode {blend_slug!r} → FILL]")

    # ── Rule 7b: Expression-driven + compositing blend → FILL, conf 0.78 ──────
    is_brittle = getattr(layer, "isBrittle", False) or False
    if is_brittle and blend_slug in _COMPOSITING_BLEND_MODES:
        _set("FILL", 0.78, "[Rule 7b: Expression + compositing blend → FILL]")

    # ── Rule 3: Source coverage → FILL (fires after Rule 1 so blend wins) ─────
    # Guard: skip if a non-FILL tag is already set with conf ≥ 0.55 to avoid
    # overriding confident classifications (e.g. CENTER from video extension
    # on a hero .mov file). Also guard precomps containing hero/logo/type keywords.
    coverage = getattr(layer, "source_coverage", None)
    if coverage is not None:
        if best_tag is None or best_tag == "FILL" or best_score < 0.55:
            _is_precomp = (getattr(layer, "layer_kind", None) == "precomp" or
                           getattr(getattr(layer, "source_item", None), "kind", "") == "comp")
            _has_hero_kw = any(kw in name_lower for kw in (
                "logo", "neon", "hero", "title", "type", "anima", "anim", "card", "lockup", "text", "outlines", "wordmark"
            ))
            if _is_precomp and _has_hero_kw:
                _set("CENTER", 0.90, "[Rule 3b: Hero/Type Precomp Lockup → CENTER]")
            elif not (_is_precomp and _has_hero_kw):
                if coverage >= 0.90:
                    _set("FILL", 0.85, f"[Rule 3: Coverage {coverage:.2f} ≥ 0.90 → FILL]")
                elif coverage >= 0.75:
                    _set("FILL", 0.75, f"[Rule 3: Coverage {coverage:.2f} ≥ 0.75 → FILL]")

    # ── GUIDE: name keywords take priority (production layers, not content) ──
    guide_score = _name_score(name_lower, "GUIDE")
    if guide_score > 0:
        _set("GUIDE", guide_score, f"Lexicon: GUIDE (name keyword)")

    # ── BOTTOM: text layer + keyword = definitive; wide strip near bottom ─────
    # Must be evaluated before TOP so text layers with legal keywords win.
    legals_score = _name_score(name_lower, "BOTTOM")
    if dna["is_text"] and legals_score > 0:
        legals_score += float(_WEIGHTS.get("text_layer_legals_bonus", 1.0))
    if dna["is_wide_strip"] and dna["is_near_bottom"] and dna["is_text"]:
        legals_score += float(_WEIGHTS.get("wide_strip_legals_bonus", 0.6))
    # ADR 01 Typographic DNA: R_typo < 0.025 (<2.5% comp height) biases toward BOTTOM (LEGAL/DISCLAIMER)
    if dna["is_micro_text"]:
        typo_legal_bonus = float(_WEIGHTS.get("typo_micro_legals_bonus", 0.8))
        if legals_score > 0:
            legals_score += typo_legal_bonus
        elif dna["is_near_bottom"]:
            legals_score = typo_legal_bonus
    if legals_score > 0:
        _set("BOTTOM", legals_score, f"Scoring: BOTTOM [strip={dna['is_wide_strip']} typo={dna['r_typo']:.3f}]")

    # ── CENTER: keyword-only, before TOP so lockup/logo/anim/artwork beat "title" ──
    center_score = _name_score(name_lower, "CENTER")
    if center_score > 0:
        _set("CENTER", center_score, f"Lexicon: CENTER")

    # ── FILL: evaluated before TOP/CENTER so "background_element" → FILL ─────
    # Spatial bonuses only amplify an existing keyword hit — they never fire cold.
    # Without this gate, any bottom-stack source layer (FX, grain, shake) misclassifies
    # as FILL due to the unconditional bottom_stack_bonus.
    bg_score = _name_score(name_lower, "FILL")
    if bg_score > 0:
        if dna["is_bottom_stack"] and dna["has_source"]:
            bg_score += float(_WEIGHTS.get("bottom_stack_bonus", 1.0))
        if dna["is_fullscreen"] and dna["is_bottom_stack"]:
            bg_score += float(_WEIGHTS.get("fullscreen_bg_bonus", 0.5))
    if bg_score > 0:
        _set("FILL", bg_score, f"Scoring: FILL [fill={dna['w_fill']:.2f}]")

    # ── TOP: text layer flag is the strongest signal ──────────────────────────
    # Evaluated after BOTTOM/CENTER/FILL so more specific tags win ties.
    type_score = _name_score(name_lower, "TOP")
    if dna["is_text"]:
        type_score += float(_WEIGHTS.get("text_layer_type_bonus", 1.0))
    # ADR 01 Typographic DNA: R_typo > 0.060 (>6.0% comp height) biases toward TOP (HERO/TITLE/HEADLINE)
    if dna["is_macro_text"]:
        typo_title_bonus = float(_WEIGHTS.get("typo_macro_title_bonus", 0.8))
        if type_score > 0:
            type_score += typo_title_bonus
        elif dna["is_near_top"]:
            type_score = typo_title_bonus
    if type_score > 0:
        _set("TOP", type_score, f"Scoring: TOP [text={dna['is_text']} typo={dna['r_typo']:.3f}]")

    # ── v5.5 Tier B: Filename-pattern hint ──────────────────────────
    # Adds a tag candidate from substring matching (logo/grunge/legal/
    # outline/etc.). Boost stacks with any lexicon score for the same
    # tag — so a layer that's ALSO in the lexicon under that tag gets
    # a higher final confidence than one matched by pattern alone.
    if fn["pattern_tag"]:
        boosted = best_score + fn["pattern_boost"] if best_tag == fn["pattern_tag"] else fn["pattern_boost"]
        _set(fn["pattern_tag"], boosted,
             f"Filename pattern: '{fn['pattern_tag']}' boost {fn['pattern_boost']:+.2f}")

    # ── v5.5 Tier B: Video file w/o other signal → CENTER ─────────────
    # `*.mov` / `*.mp4` etc. is almost always animated content. If
    # nothing else matched, default to CENTER; if it ALSO matched
    # KEYART/HERO via the pattern path above, that already won.
    if fn["is_video"] and best_tag is None:
        _set("CENTER", 0.55, "Filename: video extension")

    # ── v5.5 Tier B: SOE coverage signal ───────────────────────────
    # Layer covers ≥70% of the comp on both axes → strong FILL candidate.
    # Boost FILL so it can outrank a generic CENTER keyword.
    if dna["is_fullscreen"]:
        bg_boost = best_score + 0.40 if best_tag == "FILL" else 0.55
        _set("FILL", bg_boost,
             f"Coverage: fullscreen [w={dna['w_fill']:.2f} h={dna['h_fill']:.2f}]")

    threshold = float(_WEIGHTS.get("min_score_threshold", 0.5))
    if best_tag and best_score >= threshold:
        confidence = min(best_score, 0.99)
        return TagResult(best_tag, "heuristic", confidence, best_reason)

    return TagResult(None, None, 0.0, "No structural DNA match")





# ── Public API ────────────────────────────────────────────────────────────────

def classify_layer(
    layer: LayerModel,
    layer_index: int,
    total_layers: int,
    profile: Optional[Any] = None,
    comp_width: int = 0,
    comp_height: int = 0,
    is_foreign_comment: bool = False,
) -> TagResult:
    """
    Classify a single layer. Returns a TagResult.

    Priority:
      1. Manual tag already on the layer (from JSX scrape) — instant return
      2. Active Studio Profile prefix lookup (v5.2.1)
      3. Structural pre-classifier (camera/null/adjustment/guide)
      4. Keyword + spatial heuristic scoring (v5.5 Tier B: filename
         patterns + SOE coverage signals when available)

    `is_foreign_comment` (PR-E.1): when True, Pass 4 (heuristic) is
    skipped. The layer's comment field carries non-Dimension content
    (the user's render notes, asset IDs, etc.) and the heuristic
    name+keyword scorer would conflate that noise with the layer's
    actual identity. Earlier passes still run: a layer with a
    [BRACKET]-tagged name or an AE label color (manual_name /
    manual_label) keeps its explicit user signal; profile and
    structural matches don't depend on comment content. Only the
    inferred-from-context heuristic gets skipped, returning an
    unclassified result with a notes string the report can surface.
    """
    # Special pre-passes for adj + 3D (force for correctness; log override if manual present)
    # These run before general manual so they can apply FILL/CENTER and record [AdjOverride] or similar.
    # General manual then wins over profile/heuristic for non-special layers.
    _flags = getattr(layer, "flags", None)
    if _flags and getattr(_flags, "adjustment", False):
        _adj_manual_tag = getattr(layer, "content_tag", None)
        _adj_manual_src = getattr(layer, "content_tag_source", None) or ""
        _adj_has_manual = (
            _adj_manual_tag is not None
            and _adj_manual_src in ("manual_label", "manual_comment", "manual_name")
        )
        _adj_blend = _get_blend_slug(layer)
        if _adj_blend in _COMPOSITING_BLEND_MODES:
            _base_reason = "[Rule 9: Adjustment + compositing blend → OVERLAY]"
            if _adj_has_manual:
                _base_reason += (
                    " [AdjOverride: manual_tag=" + _adj_manual_tag
                    + " src=" + _adj_manual_src + " → OVERLAY]"
                )
            return TagResult("OVERLAY", "heuristic", 0.90, _base_reason)
        _base_reason = "[Rule 2: Adjustment Layer → FILL (pre-pass)]"
        if _adj_has_manual:
            _base_reason += (
                " [AdjOverride: manual_tag=" + _adj_manual_tag
                + " src=" + _adj_manual_src + " → FILL]"
            )
        return TagResult("FILL", "heuristic", 0.97, _base_reason)

    if (_flags and getattr(_flags, "three_d", False)
            and getattr(layer, "source_item", None) is not None
            and getattr(layer.source_item, "kind", "") == "comp"):
        _pos = getattr(layer, "position", None)
        if _pos and len(_pos) >= 3 and abs(float(_pos[2] or 0)) > 0.1:
            return TagResult("CENTER", "heuristic", 0.85,
                             "[Rule 10: 3D precomp at Z≠0 → CENTER]")

    # Pass 0 — Structural pre-classifier (Cameras, Lights, Nulls, Guides have structural immunity)
    structural = _structural_classify(layer)
    if structural is not None:
        return structural

    # Pass 1 — manual tag always wins for regular layers (fix pre-pass override for heuristics/profile)
    existing = getattr(layer, "content_tag", None)
    if existing and existing in VALID_TAGS:
        src = getattr(layer, "content_tag_source", "manual_comment") or "manual_comment"
        return TagResult(existing, src, 1.0, f"Manual tag from JSX ({src})")

    # Pass 1b — Natural comment keyword extraction (e.g. "hero", "title", "bg", "legals")
    comment_kw = _extract_natural_comment_keyword(getattr(layer, "comment", None))
    if comment_kw and comment_kw in VALID_TAGS:
        return TagResult(comment_kw, "manual_comment", 1.0, f"Natural comment keyword ({comment_kw})")

    # Pass 2 — Studio Profile prefix lookup (v5.2.1).
    # The profile's prefix→tag table is the deterministic studio policy
    # layer. A hit here yields a high-confidence "profile" source — bumps
    # the layer above heuristics, below manual. Profile tags use the
    # Studio Profile vocabulary (TT, HERO, LGL, …) which is wider than
    # the legacy VALID_TAGS set; we record verbatim so v5.2.2 Gravity
    # can read tag + gravity together.
    if profile is not None:
        try:
            rule = profile.resolve(getattr(layer, "name", "") or "")
        except Exception:
            rule = None
        if rule is not None:
            return TagResult(
                rule.tag,
                "profile",
                0.99,
                f"Studio Profile match: {rule.match}* → {rule.tag}",
            )

    # PR-E.1 — Skip Pass 4 if the gardener flagged this layer's
    # comment as FOREIGN. The heuristic's spatial-DNA + keyword
    # scoring can't usefully reason about studio render notes or
    # asset IDs; better to return unclassified and let the user
    # tag manually than emit a noise-driven heuristic guess.
    if is_foreign_comment:
        return TagResult(
            None, None, 0.0,
            "Skipped heuristic — layer comment carries non-Dimension content",
        )

    # Pass 4 — heuristic scoring
    return _score_layer(layer, layer_index, total_layers,
                        comp_width=comp_width, comp_height=comp_height)


def survey_manifest(
    manifest,
    comp_width: int = 0,
    comp_height: int = 0,
    profile: Optional[Any] = None,
) -> dict:
    """
    Run the full surveyor pass over a ScrapeManifest.
    Mutates content_tag / content_tag_source / content_tag_confidence on each layer.
    Returns a summary dict for logging.

    `profile` is an optional `models.studio_profile.StudioProfile`.
    When provided, layer names are resolved against the profile's
    prefix table BEFORE heuristics run — see classify_layer Pass 2.

    comp_width / comp_height are injected so spatial DNA can compute fill ratios.
    If not provided, falls back to manifest.project_info dimensions.

    Returned dict (v5.2.1):
        manual / profile / heuristic / structural_skip / null /
        unclassified / total           — bucket counts
        tag_breakdown: dict[str, int]  — per-tag counts (incl. profile tags)
        profile_id: str | None         — id of the profile used, if any
    """
    pi = getattr(manifest, "project_info", None)
    if not comp_width and pi:
        comp_width = getattr(pi, "width", 0) or 0
    if not comp_height and pi:
        comp_height = getattr(pi, "height", 0) or 0

    layers = getattr(manifest, "layers", []) or []
    total = len(layers)

    # PR-E.1 — Build the set of layer indices flagged FOREIGN by the
    # gardener. Done once before the loop so the per-layer check is
    # an O(1) set lookup, not an O(n) scan of the report.
    #
    # Manifests parsed before PR-E.1 (or whose gardener pass was
    # skipped on failure) have comment_report=None. In that case
    # the foreign set is empty and the surveyor behaves identically
    # to pre-PR-E.1 — full backward compatibility.
    foreign_indices: set[int] = set()
    report = getattr(manifest, "comment_report", None)
    if report is not None:
        try:
            from core.comment_gardener import CommentClass
            foreign_summaries = report.by_class.get(
                CommentClass.FOREIGN, []
            )
            foreign_indices = {
                int(s.layer_index) for s in foreign_summaries
            }
        except Exception as _ge:  # noqa: BLE001 — surveyor must never
                                  # block a scrape on a gardener-side bug
            foreign_indices = set()
            _gardener_error = str(_ge)
        else:
            _gardener_error = None
    else:
        _gardener_error = None

    counts: dict = {
        "manual": 0, "profile": 0, "heuristic": 0,
        "structural_skip": 0, "null": 0, "unclassified": 0,
        "foreign_skip": 0,
        # Slot 11.5 — count layers whose manual tag (manual_comment /
        # manual_name / manual_label) shadows a profile prefix rule
        # that would have matched the same layer name. This is the
        # banner's trap-state gate: a manual override hiding an
        # active-profile rule is the place tagging UX confusion comes
        # from. Observation-only — no mutation of layer state.
        "shadowed_rule_count": 0,
        # Task 4 — list of adjustment layers whose manual tag was
        # discarded by the pre-pass. Each entry is a dict with keys:
        # layer_name, manual_tag, manual_src, resolved_tag.
        # Always present; empty list when no overrides occurred.
        "adjustment_overrides": [],
        "total": total,
        "tag_breakdown": {},
        "profile_id": getattr(profile, "id", None),
        # Phase 1 (loud failures) — human-readable degradation notes for
        # every silently-degraded path this run hit. Always present;
        # empty list on a fully clean run. Mirrored onto
        # manifest.survey_warnings at the end of this function.
        "warnings": [],
    }
    tag_breakdown: dict = counts["tag_breakdown"]
    warnings: list = counts["warnings"]

    if _HEURISTICS_LOAD_ERROR:
        warnings.append(
            "Heuristic keyword tables failed to load — name-based scoring "
            f"is running with empty tables ({_HEURISTICS_LOAD_ERROR})")
    if _gardener_error:
        warnings.append(
            "Comment gardener report unreadable — foreign-layer detection "
            f"skipped, all layers treated as native ({_gardener_error})")

    for i, layer in enumerate(layers):
        # Inject comp dimensions for spatial DNA (not stored on layer objects)
        layer._comp_width = comp_width
        layer._comp_height = comp_height

        # Ensure source_coverage always computed (audit fix for Rule 3)
        # Use world_bounds (comp space) or source_rect for fill ratio.
        if getattr(layer, "source_coverage", None) is None:
            wb = getattr(layer, "world_bounds", None)
            sr = getattr(layer, "source_rect", None)
            cov = 0.0
            if wb and comp_width > 0 and comp_height > 0:
                try:
                    wb_w = float(wb.get("r", 0)) - float(wb.get("l", 0))
                    wb_h = float(wb.get("b", 0)) - float(wb.get("t", 0))
                    cov = min(1.0, max(0.0, abs(wb_w) / comp_width * abs(wb_h) / comp_height))
                except Exception:
                    pass
            elif sr and comp_width > 0 and comp_height > 0:
                try:
                    sr_w = float(sr[2]) if len(sr) > 2 else 0.0
                    sr_h = float(sr[3]) if len(sr) > 3 else 0.0
                    cov = min(1.0, max(0.0, sr_w / comp_width * sr_h / comp_height))
                except Exception:
                    pass
            layer.source_coverage = round(cov, 4)

        # Diagnostic-only field, mirroring source_coverage above: computed
        # unconditionally per-layer from fields already on every scrape
        # manifest (no new AE data needed — see core/alpha_hull.py's
        # classify_layer_archetype). Never folded into classify_layer(),
        # never read by content_tag resolution or any other pass yet —
        # see the Tag-stage "Boundary A amendment" in CLAUDE.md for why a
        # pass shaped like this is in-contract for the Tag stage.
        if getattr(layer, "archetype", None) is None:
            layer.archetype = classify_layer_archetype(layer)

        is_foreign = int(getattr(layer, "index", 0) or 0) in foreign_indices
        if is_foreign and _extract_natural_comment_keyword(getattr(layer, "comment", None)):
            is_foreign = False

        result = classify_layer(layer, i + 1, total, profile=profile,
                                comp_width=comp_width,
                                comp_height=comp_height,
                                is_foreign_comment=is_foreign)

        # PR-E.1 — Loud-at-log-level signal when the gardener-skip
        # actually changes the surveyor's path. Fires only when
        # foreign content + heuristic-fall-through (manual tags
        # and profile/structural matches override the skip and
        # log nothing here). Field-test diagnosis: grep for this
        # line to see which layers got de-classified.
        if is_foreign and result.source is None:
            log.info(
                "Surveyor heuristic skipped — foreign comment",
                extra={
                    "layer_index": getattr(layer, "index", i + 1),
                    "layer_name":  getattr(layer, "name", "") or "",
                },
            )

        # Write back to layer model
        layer.content_tag = result.tag
        layer.content_tag_source = result.source
        layer.content_tag_confidence = round(result.confidence, 3)

        # Task 4 — collect adjustment override warnings.
        if result.reason and "[AdjOverride:" in result.reason:
            try:
                _m = re.search(
                    r"\[AdjOverride: manual_tag=(\S+) src=(\S+) → (\S+?)\]",
                    result.reason,
                )
                if _m:
                    counts["adjustment_overrides"].append({
                        "layer_name": getattr(layer, "name", "") or "",
                        "manual_tag": _m.group(1),
                        "manual_src": _m.group(2),
                        "resolved_tag": _m.group(3),
                    })
            except Exception:
                pass

        # Tally bucket
        if result.source and result.source.startswith("manual"):
            counts["manual"] += 1
        elif result.source == "profile":
            counts["profile"] += 1
        elif result.source == "heuristic":
            counts["heuristic"] += 1
        elif result.source == "structural":
            # Nulls get their own bucket; cameras/lights/adjustments/guides
            # fall through to structural_skip.
            if result.tag == "NULL":
                counts["null"] += 1
            else:
                counts["structural_skip"] += 1
        elif is_foreign:
            # PR-E.1 — gardener flagged the layer FOREIGN AND no
            # earlier pass produced a tag. Bucketed separately from
            # plain "unclassified" so reports can distinguish
            # "didn't get a tag because nothing matched" from
            # "didn't get a tag because we deliberately skipped
            # the heuristic to avoid noise."
            counts["foreign_skip"] += 1
        else:
            counts["unclassified"] += 1

        # Tag breakdown — exclude structural and untagged so the chip
        # row in the orchestrator doesn't render structural pills.
        # Normalize through the registry so legacy aliases (TYPE,
        # KEYART, LEGALS, BACKGROUND) merge into their canonical
        # buckets — matches scale_engine/occlusion_engine's `_canon()`
        # behaviour so the chip-row display and the conform-pipeline
        # tag readings show the same buckets.
        if result.source != "structural" and result.tag:
            key = _canon(result.tag)
            tag_breakdown[key] = tag_breakdown.get(key, 0) + 1

        # Slot 11.5 — shadowed-rule counter. A layer is "shadowed"
        # when a manual_* source won AND the active profile has a
        # prefix rule that would have matched the layer's name. This
        # is the canonical trap state: the user manually tagged a
        # layer the profile would have caught automatically, so the
        # active profile's policy is silently bypassed for that
        # layer. Reuses `profile.resolve` (the same predicate
        # classify_layer Pass 2 calls); no duplication of the
        # matching logic, no mutation of layer state.
        # Reset before the per-layer profile probe so a re-survey on a
        # layer that no longer conflicts clears its stale ⚠.
        layer.profile_suggested_tag = None
        layer.profile_conflict_reason = None

        if (result.source
                and result.source.startswith("manual")
                and profile is not None):
            try:
                shadow_rule = profile.resolve(
                    getattr(layer, "name", "") or ""
                )
                if shadow_rule is not None:
                    counts["shadowed_rule_count"] += 1
                    # Slot 15.6 — conflict signal (#6). Only record the
                    # would-be tag when it actually disagrees with the
                    # manual tag; matching tags are not a conflict even
                    # though they're technically shadowed.
                    if _canon(shadow_rule.tag) != _canon(result.tag or ""):
                        layer.profile_suggested_tag = shadow_rule.tag
                        # B1 — rich conflict reason for the CEP popover.
                        layer.profile_conflict_reason = (
                            f"Profile rule \"{shadow_rule.match}\" would assign "
                            f"\"{shadow_rule.tag}\" but manual tag is "
                            f"\"{result.tag or '?'}\""
                        )
            except Exception as _se:  # noqa: BLE001 — surveyor must never
                                      # block a survey on a profile-resolve bug
                warnings.append(
                    "Profile rule check failed on layer "
                    f"'{getattr(layer, 'name', '?')}' — manual-vs-profile "
                    f"conflict detection skipped for it ({_se})")



    # ── Sprint 3 — Pre-conform preflight scan ────────────────────────────────
    # Runs after all tag-classification passes so the preflight report has
    # access to the final content_tag values.  Never raises — surveyor must
    # not block a scrape on a prescan-side bug.
    try:
        from core.comment_gardener import prescan_comp as _prescan_comp
        manifest.preflight_report = _prescan_comp(manifest)
    except Exception as _pre:
        warnings.append(
            f"Pre-conform preflight scan failed — report omitted ({_pre})")

    # Phase 1 (loud failures) — dedupe (preserving order) and mirror onto
    # the manifest so every downstream consumer (CEP panel, HTML report)
    # can surface the degradations without re-running the survey. Every
    # warning is also logged so headless runs leave a trail.
    _seen: set = set()
    _unique = [w for w in warnings if not (w in _seen or _seen.add(w))]
    counts["warnings"] = _unique
    for _w in _unique:
        log.warning("Survey degradation", extra={"note": _w})
    try:
        manifest.survey_warnings = _unique or None
    except Exception:  # noqa: BLE001 — test doubles may be frozen/slotted;
                       # counts["warnings"] still carries the list
        pass

    return counts
