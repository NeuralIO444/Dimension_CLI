# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Spatial Occlusion Engine ops, headless.

`zones` reports how a preset's safe-zone mask divides the frame
(GO / NUDGE / CUTOFF fractions) — the same mask `safe-zone plan`
resolves, now classified by the SOE solver.

`check` runs the SOE relayout pass over a conformed-layers JSON file
(`{"layers": [...]}` as produced by the conform pipeline's
conformed manifest) and reports per-layer corrections without
touching After Effects.
"""

from __future__ import annotations

import json
import os
from typing import Any

from dimension.common import DimensionError
from dimension.ops.safe_zone import resolve_target
from logic.safe_zone_resolver import resolve_mask_for_target
from core.occlusion.engine import OcclusionEngine, corrections_to_jsonable
from core.occlusion.mask_solver import OcclusionMask, OcclusionMaskLoadError


def _mask_for_preset(preset: str) -> tuple[Any, Any]:
    target = resolve_target(preset)
    resolved = resolve_mask_for_target(target)
    if resolved is None:
        raise DimensionError(
            f"MASK_MISSING — no safe-zone mask for preset {preset!r}",
            code="MASK_MISSING",
        )
    try:
        mask = OcclusionMask(resolved, target.width, target.height)
    except OcclusionMaskLoadError as e:
        raise DimensionError(str(e), code="MASK_INVALID") from e
    return target, mask


def zones_op(*, preset: str) -> dict[str, Any]:
    """Zone fractions for the preset's resolved mask."""
    target, mask = _mask_for_preset(preset)
    total = mask.zones.go.size
    return {
        "status": "OK",
        "headless": True,
        "preset": preset,
        "target": {"id": target.id, "width": target.width, "height": target.height},
        "zones": {
            "go": float(mask.zones.go.sum() / total),
            "nudge": float(mask.zones.nudge.sum() / total),
            "cutoff": float(mask.zones.cutoff.sum() / total),
        },
    }


def check_op(*, preset: str, conformed_path: str) -> dict[str, Any]:
    """Run the SOE relayout pass over conformed layers; report corrections."""
    if not os.path.isfile(conformed_path):
        raise DimensionError(
            f"conformed layers file not found: {conformed_path}",
            code="SOURCE_NOT_FOUND",
        )
    try:
        with open(conformed_path, "r", encoding="utf-8") as f:
            conformed = json.load(f)
    except Exception as e:
        raise DimensionError(
            f"could not parse conformed layers JSON: {e}",
            code="MANIFEST_INVALID",
        ) from e
    if not isinstance(conformed, dict) or "layers" not in conformed:
        raise DimensionError(
            "conformed file must be a JSON object with a 'layers' list",
            code="MANIFEST_INVALID",
        )

    target, mask = _mask_for_preset(preset)
    engine = OcclusionEngine(mask, preset_id=preset, resolution=(target.width, target.height))
    try:
        _, corrections = engine.run(conformed)
    except Exception as e:
        raise DimensionError(f"occlusion pass failed: {e}", code="OCCLUSION_FAILED") from e

    items = corrections_to_jsonable(corrections)
    return {
        "status": "OK",
        "headless": True,
        "preset": preset,
        "layers_seen": len(conformed.get("layers", [])),
        "correction_count": len(items),
        "corrections": items,
    }
