# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/grade_switcher.py
TASK-CM-TOOLKIT-02 (Issue #243) — Horizon: A/B/C Grade Revision Switcher.

Manages multi-revision color grade LUT slots (Revision A, Revision B, Revision C)
on an adjustment layer, building pseudo-effect control specifications and
ExtendScript expression bindings.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class GradeRevisionSlot:
    revision_id: str  # e.g. "A", "B", "C"
    label: str        # e.g. "Warm Broadcast 01"
    lut_file_path: Optional[str] = None
    parametric_params: Optional[Dict[str, Any]] = None  # {red: {...}, green: {...}, blue: {...}}
    is_active: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GradeSwitcherConfig:
    layer_name: str
    active_revision: str
    slots: List[GradeRevisionSlot]
    slider_min: int = 1
    slider_max: int = 3
    expression_binding: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer_name": self.layer_name,
            "active_revision": self.active_revision,
            "slider_min": self.slider_min,
            "slider_max": self.slider_max,
            "expression_binding": self.expression_binding,
            "slots": [s.to_dict() for s in self.slots],
        }


class GradeSwitcher:
    """Configures multi-grade revision switcher pseudo-effect controllers."""

    @staticmethod
    def build_slot_expression(slot_1based_index: int, slider_name: str = "Revision Selector") -> str:
        """Generates the ExtendScript expression driving a slot's opacity/enable."""
        return (
            f'var sel = Math.round(effect("{slider_name}")("Slider").value);\n'
            f'sel == {slot_1based_index} ? 100 : 0;'
        )

    @classmethod
    def build_config(
        cls,
        revisions: List[Dict[str, Any]],
        layer_name: str = "HORIZON_GRADE_SWITCHER",
        active_id: str = "A",
    ) -> GradeSwitcherConfig:
        slots: List[GradeRevisionSlot] = []
        for idx, rev in enumerate(revisions):
            rid = rev.get("id", chr(ord("A") + idx))
            label = rev.get("label", f"Grade {rid}")
            lut_path = rev.get("lut_path")
            parametric = rev.get("parametric_params")
            slots.append(GradeRevisionSlot(
                revision_id=rid,
                label=label,
                lut_file_path=lut_path,
                parametric_params=parametric,
                is_active=(rid == active_id),
            ))

        # Expression that drives active LUT selection based on Slider Index (1 = A, 2 = B, 3 = C)
        expr = (
            'var sel = Math.round(effect("Revision Selector")("Slider").value);\n'
            '// 1 -> Grade A, 2 -> Grade B, 3 -> Grade C\n'
            'sel;'
        )

        return GradeSwitcherConfig(
            layer_name=layer_name,
            active_revision=active_id,
            slots=slots,
            slider_min=1,
            slider_max=max(1, len(slots)),
            expression_binding=expr,
        )

