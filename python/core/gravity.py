# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/gravity.py
v5.2.2 Gravity Resolver — turn a Studio Profile gravity code + safe area
into a per-layer position rewrite.

Inputs (all coords in target-comp pixel space):
  rule          — `models.studio_profile.ProfileRule`
  src_pos       — [x, y, z?] of the layer's source position
  src_center    — (cx, cy) of the source comp
  tgt_size      — (target_width, target_height)
  safe_area     — `models.studio_profile.SafeArea` (fractional insets)
  base_scale    — uniform scale S (mode-derived; Fit/Fill/Stretch driver)
  fill_scale    — fill_S (max-ratio) — used when rule.gravity == "fill"
                  to override the base mode without making the call site
                  re-derive it.
  scale_z       — bool; if True, Z multiplies by S in 3D camera scenes

Returns:
  (p_conformed, s_multiplier)
    p_conformed  — [x, y, z] new position
    s_multiplier — scalar to multiply the layer's per-axis scale by
                   (typically the base scale S; "fill" returns fill_S so
                   the BG-style behaviour stays intact)

Design:
- Pure function. No registry / IO / Qt. Easy to unit-test, easy to reason
  about during a hot-path conform loop.
- Each gravity case computes the new (x, y) so the layer's *anchor point*
  lands on a deterministic spot inside the target's safe-area box. We
  preserve the X (or Y) center-remap of the original position when the
  gravity only constrains one axis, so layouts inside a gravity zone
  retain their intent.
- "fill" returns fill_scale instead of base_scale so the legacy
  BG-fill override path stays representable in this module.

This is the v5.2.2 first cut. v5.3 may add edge-padding offsets, weight-
driven layer ordering, and per-tag spacing — but the function signature
here is stable.
"""

from __future__ import annotations

from typing import Any, Tuple, Optional, List, Dict, NamedTuple

from core.spatial_math import local_point_to_world


# ── v5.5 Tier A: Baseline gravity rules per tag ─────────────────────────────
#
# Field-test 2026-04-25: heuristic-emitted tags (BACKGROUND, ARTWORK,
# ANIMATION, TYPE) had no rules. ScaleEngine fell through to legacy Fit
# math → BG letterboxed instead of filling, HERO never centred, etc.
# Audit was passing because the math was right; the gap was *no rule
# was being asked for*.
#
# Baseline rules apply **only when no studio profile prefix matches**.
# Studio profiles still win when they match. Matching policy:
#   1. profile.resolve(layer.name)              ← studio policy wins
#   2. baseline_rule_for(layer.content_tag)     ← per-tag default (NEW)
#   3. None → legacy mode-only Fit fallback     ← unchanged
#
# Vocabularies: covers BOTH the studio-profile codes (TT/HERO/LOGO/…)
# AND the heuristic surveyor's codes (BACKGROUND/ARTWORK/ANIMATION/…)
# so a layer classified by either path picks up a rule.
#
# Numerical defaults are the conservative cuts of the Universal_HV
# studio profile — TT/SUP top, HERO/LOGO centre, LGL/DISC/CTA bottom,
# BG fill. Tweak per-tag here if a default starts feeling wrong; v5.6
# can ship `_baseline.yaml` for hot-edit if needed.


class _SafeAreaTuple(NamedTuple):
    top: float
    right: float
    bottom: float
    left: float


# Default safe area used by apply_gravity when no studio profile is
# active (rare in practice — the registry always has a default loaded).
DEFAULT_SAFE_AREA = _SafeAreaTuple(top=0.05, right=0.04, bottom=0.06, left=0.04)


class _BaselineRule:
    """Duck-types `models.studio_profile.ProfileRule` so the same
    `apply_gravity()` call site works whether the rule came from a
    studio profile or the baseline fallback.

    `use_artwork_boundary` defaults True here, unlike ProfileRule's
    False (issue #331) — HP-01's optical-centroid gravity already
    shipped unconditionally for the untagged/baseline path (#436/#440,
    proven live by test_artwork_bounds_gravity.py's
    TestArtworkBoundsThroughRealConform), so baseline tags keep that
    behavior automatically rather than silently regressing it. Only a
    CUSTOM studio profile rule — deliberately hand-tuned per client —
    needs an explicit per-rule opt-in.
    """

    __slots__ = ("tag", "gravity", "scale", "weight", "match", "use_artwork_boundary")

    def __init__(self, tag: str, gravity: str = "center",
                 scale: float = 1.0, weight: str = "normal",
                 use_artwork_boundary: bool = True):
        self.tag = tag
        self.gravity = gravity
        self.scale = scale
        self.weight = weight
        self.match = ""   # baseline rules aren't prefix-matched
        self.use_artwork_boundary = use_artwork_boundary

    def __repr__(self) -> str:
        return (f"_BaselineRule(tag={self.tag!r}, gravity={self.gravity!r}, "
                f"scale={self.scale!r})")


BASELINE_RULES: Dict[str, _BaselineRule] = {
    # ── v6.0 canonical 4-tag vocabulary ─────────────────────────────────
    "FILL":       _BaselineRule("FILL",       gravity="fill",    scale=1.0),
    "CENTER":     _BaselineRule("CENTER",     gravity="center",  scale=1.0),
    "TOP":        _BaselineRule("TOP",        gravity="top",     scale=1.0),
    "BOTTOM":     _BaselineRule("BOTTOM",     gravity="bottom",  scale=0.85),
    # ── Studio-profile vocabulary (from Universal_HV defaults) ──
    "TT":         _BaselineRule("TT",         gravity="top",     scale=1.0),
    "HERO":       _BaselineRule("HERO",       gravity="center",  scale=1.0),
    "LOGO":       _BaselineRule("LOGO",       gravity="center",  scale=1.0),
    "SUP":        _BaselineRule("SUP",        gravity="top",     scale=1.0),
    "BODY":       _BaselineRule("BODY",       gravity="leftMid", scale=1.0),
    "CTA":        _BaselineRule("CTA",        gravity="bottom",  scale=1.0),
    "LGL":        _BaselineRule("LGL",        gravity="bottom",  scale=0.85),
    "DISC":       _BaselineRule("DISC",       gravity="bottom",  scale=0.85),
    "BG":         _BaselineRule("BG",         gravity="fill",    scale=1.0),
    "OVERLAY":    _BaselineRule("OVERLAY",    gravity="center",  scale=1.0),
    # ── Heuristic-surveyor vocabulary (Surveyor emits these from
    #     name keywords + spatial DNA — TYPE/ARTWORK/BACKGROUND etc.) ──
    "TYPE":       _BaselineRule("TYPE",       gravity="top",     scale=1.0),
    "ARTWORK":    _BaselineRule("ARTWORK",    gravity="center",  scale=1.0),
    "ANIMATION":  _BaselineRule("ANIMATION",  gravity="center",  scale=1.0),
    "BACKGROUND": _BaselineRule("BACKGROUND", gravity="fill",    scale=1.0),
    "BOXART":     _BaselineRule("BOXART",     gravity="center",  scale=1.0),
    "KEYART":     _BaselineRule("KEYART",     gravity="center",  scale=1.0),
    "LEGALS":     _BaselineRule("LEGALS",     gravity="bottom",  scale=0.85),
    # GUIDE / PROTECT: no rule (structural — handled upstream by
    # `_structural_classify` + the `tag in ("GUIDE","PROTECT")` skip
    # in scale_engine).
}


def baseline_rule_for(tag: Optional[str]) -> Optional[_BaselineRule]:
    """Look up the per-tag baseline gravity rule. None if no tag was
    set or the tag has no baseline (e.g. GUIDE — structural, never
    moves)."""
    if not tag:
        return None
    return BASELINE_RULES.get(str(tag).upper())


def _safe_box(tgt_w: int, tgt_h: int, safe_area) -> Tuple[float, float, float, float]:
    """Return the safe-area rect as (x, y, w, h) in target-pixel space."""
    sx = safe_area.left * tgt_w
    sy = safe_area.top * tgt_h
    sw = tgt_w - (safe_area.left + safe_area.right) * tgt_w
    sh = tgt_h - (safe_area.top + safe_area.bottom) * tgt_h
    return sx, sy, sw, sh


def apply_gravity(
    rule,
    src_pos: List[float],
    src_center: Tuple[float, float],
    tgt_size: Tuple[int, int],
    safe_area,
    base_scale: float,
    fill_scale: float,
    scale_z: bool = False,
    group_centroid: Optional[Tuple[float, float]] = None,
    artwork_bounds: Optional[Any] = None,
    layer_anchor: Optional[List[float]] = None,
    layer_scale: Optional[List[float]] = None,
    layer_rotation: Optional[float] = None,
) -> Tuple[List[float], float]:
    """Compute the new layer position + the scale multiplier to apply.

    See module docstring for the contract.

    `layer_anchor` / `layer_scale` / `layer_rotation` — this layer's OWN
    anchor point, scale (percent, AE convention: 100 = 1.0), and rotation (degrees
    clockwise), needed to correctly place `artwork_bounds.centroid` in world
    space (see HP-01 below). All default to `None`, which degrades to the historical
    assumption (anchor at origin, 100% scale, 0° rotation) for full backward
    compatibility with every existing call site and test that predates these
    parameters.

    `group_centroid` — group-aware gravity (added after the 87N 5-line
    type-stack bug, 2026-06-29). Every "pinning" gravity (top/bottom/
    center/leftMid/etc.) used to snap to a single fixed point in target
    space, so N layers sharing a tag (e.g. five lines of a synced text
    reveal) all collapsed onto the identical pixel — their 454px of
    relative HD spacing vanished entirely in the conform.

    When the caller passes `group_centroid` (the mean of `src_pos[:2]`
    across every layer sharing this tag within the same comp), the
    pinning branches anchor the GROUP's centroid to the fixed point and
    add back each member's own offset from that centroid (scaled by S).
    A group of size 1 has `group_centroid == (px, py)`, so the offset
    is always zero and the output is byte-identical to the pre-fix
    formula — this generalizes the old behavior rather than replacing
    it. Callers that don't pass `group_centroid` (None) get the
    single-member degenerate case automatically.

    `artwork_bounds` — tight visual bounds and optical center-of-mass (HP-01).
    When provided, adjusts center-pinned layers to true visual center of mass
    and clamps hero layers within safe-width corridors on narrow targets.
    """
    src_cx, src_cy = src_center
    tgt_w, tgt_h = tgt_size
    tgt_cx, tgt_cy = tgt_w / 2.0, tgt_h / 2.0
    sx, sy, sw, sh = _safe_box(tgt_w, tgt_h, safe_area)
    safe_cx = sx + sw / 2.0
    safe_cy = sy + sh / 2.0

    S = base_scale
    s_mul = S

    px = src_pos[0]
    py = src_pos[1]
    pz = src_pos[2] if len(src_pos) > 2 else 0.0

    # Group-aware offset: this member's delta from the group's centroid,
    # scaled by S. Zero when group_centroid is None or equals (px, py) —
    # i.e. every existing single-layer-per-tag call site is unaffected.
    gx, gy = group_centroid if group_centroid is not None else (px, py)
    offset_x = (px - gx) * S
    offset_y = (py - gy) * S

    # Default fall-through values — same as legacy center-remap.
    new_x = ((px - src_cx) * S) + tgt_cx
    new_y = ((py - src_cy) * S) + tgt_cy
    new_z = (pz * S) if scale_z else pz

    g = (rule.gravity or "center").strip()

    if g == "fill":
        # FILL already preserves relative position via an affine
        # transform of (px, py) — no group-aware offset needed; multiple
        # FILL plates never collapsed onto one point.
        s_mul = fill_scale
        new_x = ((px - src_cx) * fill_scale) + tgt_cx
        new_y = ((py - src_cy) * fill_scale) + tgt_cy
        new_z = (pz * fill_scale) if scale_z else pz

    elif g == "top":
        # Anchor the GROUP near the top of the safe area; preserve X
        # center-remap so multi-layer top stacks keep their relative
        # horizontal positions, and add back each member's relative Y
        # offset from the group so multi-layer top stacks keep their
        # relative vertical positions too.
        pin = sy + sh * 0.12
        new_y = pin + offset_y
        # For vertical targets and stacks with z-depth (source y flat, z spread),
        # spread the y using relative z to avoid bunching (e.g. text outlines).
        # Strict condition: only flat-y clusters (source y almost identical) on tall targets.
        # This preserves byte-identical output for normal y-varying layers and 3D scenes.
        if tgt_h > tgt_w * 1.5 and len(src_pos) > 2 and abs(py - gy) < 2.0:
            pz = src_pos[2] or 0.0
            z_off = pz * 0.22
            new_y += z_off * S

    elif g == "topC":
        # Top, plus center horizontally (used for full-frame headers).
        new_x = tgt_cx + offset_x
        new_y = (sy + sh * 0.12) + offset_y

    elif g == "bottom":
        # Anchor near the bottom of the safe area. Mirrors the legacy
        # LEGALS bottom-pin behaviour but available to any tagged layer.
        new_y = (sy + sh * 0.88) + offset_y

    elif g == "bottomC":
        new_x = tgt_cx + offset_x
        new_y = (sy + sh * 0.88) + offset_y

    elif g == "center":
        # Center the GROUP on the safe-area (NOT the comp) — gives the
        # same visual weight regardless of asymmetric safe-area insets —
        # then add back each member's offset from the group centroid.
        new_x = safe_cx + offset_x
        new_y = safe_cy + offset_y
        # Vertical stack spread for tall targets using z (depth text stacks etc.)
        # Only for truly flat-y clusters to keep byte-identical for other cases.
        if tgt_h > tgt_w * 1.5 and len(src_pos) > 2 and abs(py - gy) < 2.0:
            pz = src_pos[2] or 0.0
            new_y += (pz * 0.18) * S

    elif g == "centerH":
        # Horizontal center; preserve the source's vertical placement
        # via the standard center-remap.
        new_x = safe_cx + offset_x
        # new_y stays at the center-remap default

    elif g == "leftMid":
        # Anchor the layer to the left edge of the safe area, centered
        # vertically. Used for body copy in landscape layouts.
        new_x = (sx + sw * 0.05) + offset_x
        new_y = safe_cy + offset_y

    elif g == "rightMid":
        new_x = (sx + sw - sw * 0.05) + offset_x
        new_y = safe_cy + offset_y

    # Optical Centroid & Safe-Width Clamping (HP-01)
    if artwork_bounds is not None and getattr(artwork_bounds, "width", 0) > 0 and getattr(artwork_bounds, "height", 0) > 0:
        centroid = getattr(artwork_bounds, "centroid", None)
        if centroid and len(centroid) >= 2:
            # `centroid` is LAYER-LOCAL -- relative to the layer's own
            # anchor/origin, per SovCore_Layer.jsx's producing formula
            # (`left + width/2, top + height/2` computed from the layer's
            # own local sourceRect). It carries no relationship to the
            # comp's world-space center.
            #
            # Bug #436 (found 2026-09-04): this block used to do
            # `(src_cx - art_cx) * S` -- subtracting a world-space comp
            # center from a layer-local value as if both lived in the same
            # frame. For any layer not sitting at the world origin, that
            # produced a huge bogus displacement (reproduced live: a shape
            # at [400,400] came out at [2246, 1910] against a 1080x1920
            # target, tripping the engine's own
            # "SpatialBoundWarning: centroid exceeds 2x comp bounds").
            #
            # Correct approach: convert the local centroid to world space
            # using THIS layer's own source position, then compare against
            # that same position -- i.e. "how far is the visual mass from
            # this layer's own anchor," not "how far is it from the comp
            # center." A symmetric shape (centroid == [0,0], content
            # centered on its own anchor) now correctly produces zero
            # delta regardless of where the layer sits -- previously this
            # required the layer to coincidentally sit at the comp's exact
            # center to produce zero.
            #
            # General local-to-world conversion (2026-09-04, second pass):
            # the first version of this fix used `world = px + art_cx`,
            # which implicitly assumes the layer's anchor point sits at
            # the local origin and its own scale is 100%. A stress test
            # deliberately targeting a non-zero anchor point (Case 2) and
            # a layer with its own non-100% scale (Case 3) caught that the
            # simplified version silently mispositions both -- Case 2 by
            # exactly the ignored anchor offset, Case 3 by 3x the local
            # offset (a 400% layer scale applied to a -100 local centroid).
            # Fixed via `spatial_math.local_point_to_world` (issue #440
            # consolidated this with `mask_solver.py::compute_world_bounds`'s
            # identical formula, previously duplicated by hand -- see that
            # function's docstring for the AE round-trip that confirms it).
            art_cx, art_cy = centroid[0], centroid[1]
            anchor_x, anchor_y = (layer_anchor[0], layer_anchor[1]) if layer_anchor else (0.0, 0.0)
            layer_sx = (layer_scale[0] / 100.0) if layer_scale else 1.0
            layer_sy = (layer_scale[1] / 100.0) if layer_scale else 1.0
            rot_deg = float(layer_rotation) if layer_rotation is not None else 0.0
            world_art_cx, world_art_cy = local_point_to_world(
                src_pos, (anchor_x, anchor_y), (layer_sx, layer_sy), (art_cx, art_cy), rot_deg
            )
            opt_delta_x = (px - world_art_cx) * S
            opt_delta_y = (py - world_art_cy) * S
            if g in ("center", "centerH"):
                new_x += opt_delta_x
                new_y += opt_delta_y

        # Narrow aspect ratio safe-width clamping: ensure artwork width fits within safe corridor
        if tgt_h > tgt_w * 1.3 and g in ("center", "top", "bottom", "topC", "bottomC", "leftMid", "rightMid"):
            art_w = float(getattr(artwork_bounds, "width", 0) or 0)
            if art_w > 0:
                scaled_art_w = art_w * S
                if scaled_art_w > sw and sw > 0:
                    clamp_ratio = sw / scaled_art_w
                    s_mul = s_mul * clamp_ratio

    return [new_x, new_y, new_z], s_mul
