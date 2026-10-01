# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/occlusion/constants.py
Data structures, constants, and error definitions for the Spatial Occlusion Engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import numpy as np


class OcclusionMaskLoadError(Exception):
    """Raised when the mask PNG can't be loaded or classified.
    Caller catches this and falls back to a conform-only ship —
    SOE never blocks the pipeline."""


# Tags that SOE will translate or otherwise reposition. Canonical
# only — `_canon()` normalises legacy aliases at comparison time,
# so a layer tagged `#TT` in a v5.9 comp still resolves to TOP
# and lands in TRANSLATABLE_TAGS.
TRANSLATABLE_TAGS = frozenset({"TOP", "BOTTOM", "CENTER"})

# Tags that get strict position-preservation (never moved).
STRUCTURAL_TAGS = frozenset({"GUIDE", "PROTECT"})

# Cost weights — class-level for easy tuning. Cutoff vastly
# outweighs everything else so the solver always prefers a clear
# placement to a near-clear one.
COST_CUTOFF = 1000.0
COST_NUDGE = 1.0
COST_DISTANCE = 0.01

# Pixel intensity thresholds for the three-zone classifier.
THRESH_GO_MIN = 240
THRESH_NUDGE_LO = 113
THRESH_NUDGE_HI = 143
THRESH_CUTOFF_MAX = 15

# Inter-layer repulsion parameters
REPULSION_MARGIN_PX = 16.0
REPULSION_MARGIN_FLOOR_PX = 4.0
REPULSION_MAX_SWEEPS = 12
REPULSION_EPSILON_PX = 0.5

# Importance weights for an inter-layer repulsion collision.  A heavier
# layer yields less: when two movable layers overlap, each layer's share
# of the required separation is proportional to the *other* layer's
# weight.  This keeps titles closest to their intended placement while
# allowing centered content, then legals, to absorb more of the nudge.
# Equal tags intentionally retain the legacy 50/50 split exactly.
REPULSION_TAG_WEIGHTS = {
    "TOP": 3.0,
    "CENTER": 2.0,
    "BOTTOM": 1.0,
}

# Emitted on `warnings.soe_overflow` when a stack cannot be separated
# even at REPULSION_MARGIN_FLOOR_PX.
SOE_OVERFLOW_WARNING = "SOE_OVERFLOW_WARNING"


@dataclass(frozen=True)
class MaskZones:
    """Boolean zone masks at comp resolution. Same shape as the
    input mask. ``True`` marks membership."""
    go: np.ndarray
    nudge: np.ndarray
    cutoff: np.ndarray


@dataclass
class SOECorrection:
    """One per-layer correction record. JSON-serializable; the
    report generator reads these to render the table."""
    layer_index: int
    layer_uid: Optional[str]
    layer_name: str
    content_tag: Optional[str]
    original_position: List[float]
    corrected_position: List[float]
    zone_hit: str  # CLEAR | NUDGE | CUTOFF | UNKNOWN — final state
    strategy: str  # NONE / TRANSLATE / TIGHTEN_MARGIN / ANCHOR_SHIFT
                   # / SKIPPED_KEYED / SKIPPED_NO_BOUNDS
                   # / SKIPPED_STRUCTURAL / SOE_FAILED
                   # / SKIPPED_UNIT_KEYED
                   # / REPULSION / REPULSION_OVERFLOW
    move_distance_px: float
    original_zone_hit: Optional[str] = None
    notes: str = ""
