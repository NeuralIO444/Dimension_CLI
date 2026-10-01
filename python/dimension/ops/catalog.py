# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial.
# See LICENSE for full terms.

"""Catalog ops: the preset/target catalog and studio profiles. Headless.

The catalog is the delivery-matrix vocabulary — every platform,
aspect, and safe-zone target Dimension can conform to, plus the
studio-profile prefix rules the surveyor applies.
"""

from __future__ import annotations

from typing import Any

from dimension.common import DimensionError
from dimension.ops.safe_zone import preset_manager
from logic.studio_profile_registry import StudioProfileRegistry


def presets_op() -> dict[str, Any]:
    """Every resolvable preset: id, label, dimensions, subcategory."""
    mgr = preset_manager()
    items = []
    for key in sorted(mgr.presets):
        preset = mgr.presets[key]
        items.append(
            {
                "id": key,
                "label": getattr(preset, "label", key),
                "width": getattr(preset, "width", None),
                "height": getattr(preset, "height", None),
                "subcategory": getattr(preset, "subcategory", None),
            }
        )
    return {
        "status": "OK",
        "headless": True,
        "preset_count": len(items),
        "presets": items,
    }


def show_preset_op(*, preset: str) -> dict[str, Any]:
    """Full detail for one preset, including its resolved target."""
    mgr = preset_manager()
    target = mgr.get_target(preset)
    if target is None:
        raise DimensionError(
            f"unknown preset: {preset!r}", code="PRESET_NOT_FOUND"
        )
    return {
        "status": "OK",
        "headless": True,
        "preset": preset,
        "id": getattr(target, "id", None),
        "label": getattr(target, "label", None),
        "width": getattr(target, "width", None),
        "height": getattr(target, "height", None),
        "subcategory": getattr(target, "subcategory", None),
        "channel": getattr(target, "channel", None),
        "aspect_label": getattr(target, "aspect_label", None),
        "metadata": getattr(target, "metadata", None) or {},
    }


def profiles_op() -> dict[str, Any]:
    """Studio profiles (prefix rules for the surveyor)."""
    registry = StudioProfileRegistry()
    profiles = registry.list_profiles()
    items = []
    for p in profiles:
        items.append(
            {
                "id": getattr(p, "profile_id", getattr(p, "id", "?")),
                "name": getattr(p, "name", ""),
                "description": getattr(p, "description", ""),
            }
        )
    active = registry.get_active()
    return {
        "status": "OK",
        "headless": True,
        "active_profile": (
            getattr(active, "profile_id", getattr(active, "id", None))
            if active is not None
            else None
        ),
        "profile_count": len(items),
        "profiles": items,
    }
