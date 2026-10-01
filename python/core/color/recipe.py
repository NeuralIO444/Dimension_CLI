# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/color/recipe.py
Color recipe schema, context, reports, and backend enum models.
"""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any


class Backend(str, Enum):
    LEVELS = "levels"
    LEVELS_PHOTO = "levels+photo"
    LUT_MESH = "lut_mesh"
    LUT_ELIDE = "lut_elided"


@dataclass
class ColorContext:
    working_space: str = "Rec.709"
    transfer: str = "display"  # "display" | "linear" | "log"
    engine: str = "adobe"      # "adobe" | "ocio"
    bpc: int = 32
    gateway_id: Optional[str] = None
    ocio_config: Optional[str] = None


@dataclass
class LookReport:
    luma_p: Dict[int, float]           # {1: val, 5: val, 50: val, 95: val, 99: val}
    lab_mu: Tuple[float, float, float]
    lab_sigma: Tuple[float, float, float]
    colorfulness: float
    cct: float
    tint: float
    zone_rgb: Dict[str, Tuple[float, float, float]]  # "shadows", "mids", "highlights"
    hue_hist: List[float]               # 36-bin sat-weighted histogram
    signatures: List[str]
    n_pixels: int
    luma_space: str = "rec709"          # "rec709" | "ap1"
    downsample_shape: Tuple[int, int] = (0, 0)


@dataclass
class Residual:
    metric_id: str = "planner_metric_v1"
    R: float = 0.0
    dE_mean: float = 0.0                # Mean CIE76
    dE_p95: float = 0.0
    dE2000_mean: float = 0.0            # Optional audit metric
    W1_luma: float = 0.0                # Wasserstein-1 on 256-bin luma histograms
    hue_emd: float = 0.0                # Circular EMD on 36-bin sat-weighted hue hist
    lab_mu_l2: float = 0.0
    lab_sigma_l2: float = 0.0


@dataclass
class ColorRecipe:
    backend: Backend
    mode: str                           # "shot_match" | "look_transfer"
    levels: Dict[str, Dict[str, float]]
    lut_path: Optional[str] = None
    switcher_slot: int = 2
    extra: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    signatures: List[str] = field(default_factory=list)
    context: Optional[ColorContext] = None
    report_src: Optional[LookReport] = None
    report_ref: Optional[LookReport] = None
    residual: Optional[Residual] = None
