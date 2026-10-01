# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/typographic_micro_guard.py
Issue #339 (Track E.5, narrow slice) -- typographic micro-guard: prevents
a BOTTOM/LEGALS disclaimer's stroke from visually dropping to sub-pixel
width after a large downscale conform.

NOT the same premise as the deleted `effect_conformer.py`. That module
scaled effect parameters by S on the theory that they're compositor
pixel offsets needing proportional adjustment -- wrong, because AE
applies effects in layer space BEFORE the layer transform, so scaling
the transform already scales the rendered effect on screen. Applying S
again on top double-transformed 87N's CC Lens Center and was deleted
2026-08-19 (CLAUDE.md's sharp edge on `effect_conformer.py`).

This module does the opposite kind of correction, deliberately: it only
intervenes in the ONE degenerate case where the transform's automatic
scaling would push a stroke below visibility (S small enough that
raw_width_px * S < 1.0), and even then does not apply S to the raw
value -- it computes the INVERSE (raw_width_px = 1.0 / S) so the
post-transform effective result lands at exactly the floor, not at some
S-multiplied value. Every other case (effective width already >= floor)
passes the raw value through completely unchanged. This is a targeted
floor-clamp for one specific visual-dropout failure mode, not a general
"scale effect params by S" rule -- the class of change already proven
unsafe in this codebase.

STATUS: computed, NOT YET WIRED to Babysitter. There is currently no
live effect-property writer in Babysitter.jsx (CLAUDE.md's sharp edge:
"Babysitter's effect writer... is dead too and is still present;
deleting it needs manual AE QA and is a separate follow-up"). Nothing
calls this module in production yet -- restoring/building that writer
is tracked as its own follow-up issue, requiring real AE QA that can't
be done headless. See `python/tests/test_reachability.py`'s TRANSITIONAL
allowlist entry for this module.
"""

from __future__ import annotations

MIN_VISIBLE_STROKE_PX = 1.0


def clamp_stroke_width_px(raw_width_px: float, scale_factor: float) -> float:
    """Given a layer's raw (unscaled) stroke width in pixels and the
    uniform scale factor S about to be applied to that layer's
    transform, return the raw stroke width AE should actually be given
    so that the ON-SCREEN EFFECTIVE stroke width (raw_width_px *
    scale_factor, since AE renders effects before the layer transform)
    never falls below `MIN_VISIBLE_STROKE_PX`.

    Pass-through (no boost) whenever the effective width is already at
    or above the floor -- this function only ever pushes the raw value
    UP to prevent disappearance, never down, and never touches a raw
    value whose effective result is already safe.

    Degenerate `scale_factor` (<= 0) is not a real conform scale factor
    and is passed through unchanged rather than raising or dividing by
    zero -- callers are expected to validate S upstream; this function's
    only job is the floor clamp.
    """
    if scale_factor <= 0:
        return raw_width_px
    if raw_width_px <= 0:
        return raw_width_px

    effective_px = raw_width_px * scale_factor
    if effective_px >= MIN_VISIBLE_STROKE_PX:
        return raw_width_px

    return MIN_VISIBLE_STROKE_PX / scale_factor
