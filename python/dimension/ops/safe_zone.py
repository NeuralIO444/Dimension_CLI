# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial.
# See LICENSE for full terms.

"""Safe-zone ops: which mask a preset resolves to, and zone coverage.

Headless. Uses the same D2 channel-first resolver the conform
pipeline uses (`logic.safe_zone_resolver.resolve_mask_for_target`),
so `safe-zone plan` shows exactly the mask SOE will use.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from dimension.common import DimensionError
from logic.preset_manager import PresetManager
from logic.safe_zone_resolver import available_masks, resolve_mask_for_target
from core.occlusion.mask_solver import OcclusionMask

_mgr: Optional[PresetManager] = None


def preset_manager() -> PresetManager:
    global _mgr
    if _mgr is None:
        _mgr = PresetManager()
    return _mgr


def resolve_target(preset: str):
    target = preset_manager().get_target(preset)
    if target is None:
        raise DimensionError(
            f"unknown preset: {preset!r} (see `dimension catalog presets`)",
            code="PRESET_NOT_FOUND",
        )
    return target


def _describe_source(resolved: Any, target) -> dict[str, Any]:
    """Name the resolver leg that produced `resolved` (best-effort)."""
    if resolved is None:
        return {"kind": "missing", "detail": "MASK_MISSING — no mask for this target"}
    if isinstance(resolved, (str, Path)):
        return {"kind": "png", "detail": str(resolved)}
    # ndarray: synthesized. Attribute to the most specific known leg.
    meta = (getattr(target, "metadata", None) or {})
    if meta.get("multi_panel"):
        return {"kind": "synthesized", "detail": "multi_panel gap-zone geometry"}
    return {"kind": "synthesized", "detail": "channel-derived / inset-pack strategy"}


def mask_plan_op(*, preset: str) -> dict[str, Any]:
    """Resolve the safe-zone mask for a preset and report zone coverage."""
    target = resolve_target(preset)
    resolved = resolve_mask_for_target(target)
    source = _describe_source(resolved, target)
    out: dict[str, Any] = {
        "status": "OK",
        "headless": True,
        "preset": preset,
        "target": {"id": target.id, "width": target.width, "height": target.height},
        "mask": source,
    }
    if resolved is not None:
        mask = OcclusionMask(resolved, target.width, target.height)
        total = mask.zones.go.size
        out["zones"] = {
            "go": float(mask.zones.go.sum() / total),
            "nudge": float(mask.zones.nudge.sum() / total),
            "cutoff": float(mask.zones.cutoff.sum() / total),
        }
    return out


def mask_list_op() -> dict[str, Any]:
    """List the safe-zone mask assets available on disk."""
    return {
        "status": "OK",
        "headless": True,
        "masks": sorted(available_masks()),
    }
