# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/occlusion_engine.py
Backward-compatibility facade for the Spatial Occlusion Engine (SOE 2.0).
Redirects all imports to the modular python/core/occlusion/ package.
"""

from core.occlusion import (
    COST_CUTOFF,
    COST_DISTANCE,
    COST_NUDGE,
    MaskZones,
    OcclusionEngine,
    OcclusionMask,
    OcclusionMaskLoadError,
    REPULSION_EPSILON_PX,
    REPULSION_MARGIN_FLOOR_PX,
    REPULSION_MARGIN_PX,
    REPULSION_MAX_SWEEPS,
    SOECorrection,
    SOE_OVERFLOW_WARNING,
    STRUCTURAL_TAGS,
    THRESH_CUTOFF_MAX,
    THRESH_GO_MIN,
    THRESH_NUDGE_HI,
    THRESH_NUDGE_LO,
    TRANSLATABLE_TAGS,
    compute_world_bounds,
    corrections_to_jsonable,
)
from core.occlusion.mask_solver import _compute_world_bounds

__all__ = [
    "OcclusionEngine",
    "OcclusionMask",
    "OcclusionMaskLoadError",
    "SOECorrection",
    "MaskZones",
    "TRANSLATABLE_TAGS",
    "STRUCTURAL_TAGS",
    "COST_CUTOFF",
    "COST_NUDGE",
    "COST_DISTANCE",
    "THRESH_GO_MIN",
    "THRESH_NUDGE_LO",
    "THRESH_NUDGE_HI",
    "THRESH_CUTOFF_MAX",
    "REPULSION_MARGIN_PX",
    "REPULSION_MARGIN_FLOOR_PX",
    "REPULSION_MAX_SWEEPS",
    "REPULSION_EPSILON_PX",
    "SOE_OVERFLOW_WARNING",
    "compute_world_bounds",
    "_compute_world_bounds",
    "corrections_to_jsonable",
]
