# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/color/__init__.py
Horizon Color Engine & Planner Package — Decoupled, Product-Agnostic Color Tools.
"""

from core.color.recipe import (
    Backend,
    ColorContext,
    LookReport,
    Residual,
    ColorRecipe,
)
from core.color.gateway import LogSpaceGateway, ColorTransformResult
from core.color import levels_spline
from core.color import lut_mesh
from core.color import features
from core.color import simulate
from core.color import residual
from core.color import planner
from core.color.planner import plan

__all__ = [
    "Backend",
    "ColorContext",
    "LookReport",
    "Residual",
    "ColorRecipe",
    "LogSpaceGateway",
    "ColorTransformResult",
    "levels_spline",
    "lut_mesh",
    "features",
    "simulate",
    "residual",
    "planner",
    "plan",
]
