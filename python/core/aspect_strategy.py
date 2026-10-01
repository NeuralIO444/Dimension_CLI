# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/aspect_strategy.py
Slot 7.5 Phase 2 — Aspect strategy classifier for conform routing.

Classifies a (source, target) dimension pair into one of four strategy
labels used by the conform pipeline. Pure function, no side effects,
no I/O. Importable with zero import-time work.

Phase 2 (this module) labels only. Phase 3 will gate behavior on the
label — SOE skip, gravity override, etc. — once product decisions are
locked. This module is observational; it does not change any conform
output.

Strategy labels:
  preserve                     — source AR == target AR (within EPS)
                                 AND pixel dimensions equal.
  equal_different_resolution   — source AR == target AR (within EPS)
                                 AND pixel dimensions differ
                                 (e.g. HD → 4K UHD, both 16:9).
  widen                        — target AR is meaningfully wider than
                                 source (target_ar - source_ar > EPS).
  narrow                       — target AR is meaningfully narrower
                                 than source.

Tolerance (EPS = 0.05) is intentionally generous. Rationale: the
FB/IG landscape cluster at AR ~1.91 sits ~7% from 16:9 (1.778) and
classifies as `widen` — the layouts genuinely are a different shape
to creative. Smaller deltas (sub-5%) are treated as the same shape;
no designer rebuilds a comp for a 5% AR delta.
"""

from dataclasses import dataclass
from enum import Enum


# Aspect-ratio equality tolerance (signed fractional delta of target_ar
# relative to source_ar). Locked at 5% per Slot 7.5 Phase 1 product
# input — see module docstring.
EPS = 0.05


class AspectStrategy(str, Enum):
    PRESERVE = "preserve"
    WIDEN = "widen"
    NARROW = "narrow"
    EQUAL_DIFFERENT_RESOLUTION = "equal_different_resolution"


@dataclass(frozen=True)
class ClassificationResult:
    """Pure data record. `ar_delta_pct` is the signed fractional
    delta of target_ar relative to source_ar (positive = wider,
    negative = narrower). The caller multiplies by 100 for display."""
    strategy: AspectStrategy
    source_ar: float
    target_ar: float
    ar_delta_pct: float


def classify(src_w: int, src_h: int, tgt_w: int, tgt_h: int) -> ClassificationResult:
    """Classify a conform's aspect relationship.

    Args:
        src_w, src_h: source comp dimensions in pixels (must be > 0)
        tgt_w, tgt_h: target comp dimensions in pixels (must be > 0)

    Returns:
        ClassificationResult with the strategy label and diagnostic
        AR values. Never mutates inputs; never raises except on
        invalid dimensions.

    Raises:
        ValueError: if any of the four dimensions is non-positive.
    """
    if src_w <= 0 or src_h <= 0 or tgt_w <= 0 or tgt_h <= 0:
        raise ValueError(
            "All dimensions must be positive; got "
            f"src=({src_w}x{src_h}), tgt=({tgt_w}x{tgt_h})"
        )

    src_ar = src_w / src_h
    tgt_ar = tgt_w / tgt_h
    ar_delta_pct = (tgt_ar - src_ar) / src_ar

    if abs(ar_delta_pct) <= EPS:
        if src_w == tgt_w and src_h == tgt_h:
            strategy = AspectStrategy.PRESERVE
        else:
            strategy = AspectStrategy.EQUAL_DIFFERENT_RESOLUTION
    elif ar_delta_pct > 0:
        strategy = AspectStrategy.WIDEN
    else:
        strategy = AspectStrategy.NARROW

    return ClassificationResult(
        strategy=strategy,
        source_ar=src_ar,
        target_ar=tgt_ar,
        ar_delta_pct=ar_delta_pct,
    )
