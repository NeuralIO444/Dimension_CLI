# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial.
# See LICENSE for full terms.

"""Custom-target ops: the "Save as Preset" affordance, headless.

Wraps TargetStoreManager so callers never touch the YAML schema.
"""

from __future__ import annotations

from typing import Any, Optional

from logic.target_store import TargetStoreManager


def add_target_op(
    *,
    label: str,
    width: int,
    height: int,
    subcategory: str = "user",
    duration: Optional[float] = None,
    fps: Optional[float] = None,
    output_name_template: Optional[str] = None,
) -> dict[str, Any]:
    """Persist a user custom target. Returns its id."""
    tsm = TargetStoreManager()
    target = tsm.add_custom(
        label=label,
        width=width,
        height=height,
        subcategory=subcategory,
        duration=duration,
        fps=fps,
        output_name_template=output_name_template,
    )
    return {
        "status": "OK",
        "headless": True,
        "id": target.id,
        "label": target.label,
        "width": target.width,
        "height": target.height,
        "duration": target.duration,
        "fps": target.fps,
        "subcategory": target.subcategory,
        "aspect_label": target.aspect_label,
        "output_name_template": target.output_name_template,
    }


def remove_target_op(*, target_id: str) -> dict[str, Any]:
    """Remove a user custom target by id."""
    tsm = TargetStoreManager()
    removed = tsm.remove_custom(target_id)
    return {
        "status": "OK",
        "headless": True,
        "id": target_id,
        "removed": removed,
    }
