# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/panel_slicer.py
TASK-P2-01 (#249) — Multi-Panel Triptych Slicing Engine.

Implements ADR 01 from
docs/roadmap/presets-2-0-pre-coding-rigor-2026-08-28.md ("Multi-Panel
Triptych Architecture — Unified Canvas with Cropping Children"): one
Master Design Comp sized to the full physical installation (panels +
physical pillar gaps), plus N child cropping comps that each show one
panel's window into the master via a static X-position offset.

This module is PURE geometry planning, matching the shape of
`core/duplication_planner.py`: it consumes a `multi_panel` spec (the
raw dict shipped on OOH targets in `config/ooh_specs.json`, e.g.
OOH-064's Market 15 Liveboard) and a base comp name, and emits a
`PanelSlicingPlan`. No I/O, no AE calls, no side effects. The JSX half
of TASK-P2-01 (materializing this plan into real AE comps inside
`Babysitter.jsx`) is separate, not-yet-implemented work — this planner
only produces the data contract that side will consume.

Scope: horizontal panel slicing only. Every multi_panel spec in the
shipped 78-spec OOH dataset (transit_triptychs category) is a
horizontal strip of same-height panels; there is no vertical
multi-panel spec today. A future vertical variant would generalize
the Y-axis math symmetrically to the X-axis math below.

── Math (ADR 01 §5.1, ratified in the pre-coding-rigor doc) ──────────

For N panels of width Wp, height Hp, separated by physical gap Gp:

    Master canvas width:  W_total = N * Wp + (N - 1) * Gp
    Master canvas height: H_total = Hp

    Panel k's crop window in master-canvas space (k in [0, N-1]):
        x_start(k) = k * (Wp + Gp)
        x_end(k)   = x_start(k) + Wp

    Position of the nested Master-Comp layer inside child comp k
    (the static offset that reveals exactly panel k's window):
        X_offset(k) = Wp / 2 - k * (Wp + Gp)
        Y_offset    = Hp / 2   (constant — horizontal-only slicing)

    Physical pillar gap i sits between panel i and panel i+1
    (i in [0, N-2]):
        gap_x_start(i) = (i + 1) * Wp + i * Gp
        gap_x_end(i)   = gap_x_start(i) + Gp

All four formulas are verified against the worked Market 15 Liveboard
example in the ADR (N=3, Wp=1080, Hp=1920, Gp=221 → W_total=3682,
panel offsets 540 / -761 / -2062, gap zones [1080..1301] and
[2381..2602]) by this module's test suite.

── Downstream consumers (not built by this module) ───────────────────

- `Babysitter.jsx` (TASK-P2-01's JSX half): builds the actual AE comps
  from this plan — 1 master + N children, inside a single
  `beginUndoGroup` per the Anti-Shatter Threat Model's Comp Count
  Invariant (`PanelSlicingPlan.item_count`).
- SOE (`occlusion_engine.py`, future wiring): the `gap_zones` in the
  plan are Scenario 3's "Hard Occlusion Zones" — physical pillars that
  must never have text or legal copy placed across them. This module
  only computes the zone geometry; SOE wiring is separate work.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from pydantic import BaseModel, Field, model_validator

_DEFAULT_SPLIT_NAMES: Tuple[str, ...] = ("LEFT", "CENTER", "RIGHT")


class MultiPanelSpec(BaseModel):
    """Validated form of the raw `multi_panel` dict carried on an OOH
    target's metadata (see `config/ooh_specs.json`, e.g. OOH-064's
    Market 15 Liveboard, and `data/target_catalog.py`'s
    `metadata["multi_panel"]` passthrough). This model validates and
    normalizes that dict before the planner runs — it does not replace
    the on-disk schema."""

    panel_count: int = Field(
        ..., ge=2,
        description="Number of synchronized panels. >=2 — a single "
                    "panel isn't a multi-panel installation.")
    panel_width: int = Field(..., gt=0, description="Width of one child panel, px.")
    panel_height: int = Field(..., gt=0, description="Height of one child panel, px.")
    gap_px: int = Field(
        ..., ge=0,
        description="Physical gap between adjacent panels (e.g. an "
                    "architectural pillar), px. 0 means panels are "
                    "contiguous — no gap guides are emitted.")
    split_naming: Optional[List[str]] = Field(
        default=None,
        description="Per-panel display names in panel order, e.g. "
                    "['LEFT', 'CENTER', 'RIGHT']. Must have exactly "
                    "panel_count entries when provided. Falls back to "
                    "the LEFT/CENTER/RIGHT convention for panel_count==3 "
                    "and PANEL_<k> otherwise.")

    @model_validator(mode="after")
    def _check_split_naming_length(self) -> "MultiPanelSpec":
        if self.split_naming is not None and len(self.split_naming) != self.panel_count:
            raise ValueError(
                f"split_naming has {len(self.split_naming)} entries but "
                f"panel_count is {self.panel_count} — they must match")
        return self


class GapZone(BaseModel):
    """One physical pillar gap between two adjacent panels. Non-
    rendering guide geometry (Scenario 3, pre-coding-rigor doc §2) —
    SOE treats these as hard occlusion zones so text never lands
    behind a pillar."""

    index: int = Field(..., ge=0, description="0-based gap index; gap i sits between panel i and panel i+1.")
    name: str = Field(..., description='e.g. "GAP_PILLAR_01" — matches the ADR\'s guide-layer naming.')
    x_start: int = Field(..., description="Left edge of the gap in master-canvas space, px.")
    x_end: int = Field(..., description="Right edge of the gap in master-canvas space, px.")
    width: int = Field(..., gt=0, description="x_end - x_start; equals the spec's gap_px.")
    guide_layer: bool = Field(
        default=True,
        description="Always True — these zones never render. Carried "
                    "explicitly so downstream JSX doesn't need to "
                    "infer non-rendering status.")


class PanelSpec(BaseModel):
    """One child cropping comp: a static window into the Master
    Design Comp."""

    index: int = Field(..., ge=0, description="0-based panel index, left to right.")
    name: str = Field(..., description='Display name, e.g. "LEFT", "CENTER", "RIGHT", or "PANEL_0".')
    comp_name: str = Field(..., description='e.g. "CAMPAIGN_LIVEBOARD_LEFT_1080x1920".')
    width: int = Field(..., gt=0)
    height: int = Field(..., gt=0)
    crop_x_start: int = Field(..., ge=0, description="Left edge of this panel's window in master-canvas space, px.")
    crop_x_end: int = Field(..., gt=0, description="Right edge of this panel's window in master-canvas space, px.")
    master_layer_position: Tuple[float, float] = Field(
        ..., description="ADR 01 (X_offset(k), Hp/2) — the position at "
                          "which Babysitter places the nested "
                          "Master-Comp layer inside this child comp so "
                          "it reveals exactly this panel's window.")
    spatial_token: Optional[str] = Field(
        default=None, description="Disambiguated spatial token e.g. Panel01_LEFT (TASK-SUB-05)")


class PanelSlicingPlan(BaseModel):
    """Complete slicing plan for one multi-panel installation. Contract
    between this pure Python planner and Babysitter.jsx's (separate,
    not-yet-implemented) execution side."""

    master_comp_name: str = Field(..., description='e.g. "CAMPAIGN_LIVEBOARD_MASTER_3682x1920".')
    master_width: int = Field(..., gt=0)
    master_height: int = Field(..., gt=0)

    panel_count: int = Field(..., ge=2)
    panel_width: int = Field(..., gt=0)
    panel_height: int = Field(..., gt=0)
    gap_px: int = Field(..., ge=0)

    panels: List[PanelSpec] = Field(default_factory=list)
    gap_zones: List[GapZone] = Field(default_factory=list)

    @property
    def item_count(self) -> int:
        """Comp Count Invariant (Anti-Shatter Threat Model §6.3): 1
        Master Comp + N child comps must land in AE — no more, no
        fewer, no orphans."""
        return 1 + len(self.panels)


class PanelSlicingPlanner:
    """Builds `PanelSlicingPlan`s from a `MultiPanelSpec`. Stateless
    beyond construction args, mirroring `DuplicationPlanner`'s shape —
    `plan()` is callable repeatedly and yields fresh, independent plan
    objects each time."""

    def __init__(self, spec: MultiPanelSpec, base_name: str):
        if not base_name:
            raise ValueError("base_name must be a non-empty string")
        self.spec = spec
        self.base_name = base_name

    @classmethod
    def from_metadata(cls, metadata: dict, base_name: str) -> "PanelSlicingPlanner":
        """Build from a raw `multi_panel` dict, e.g.
        `target.metadata["multi_panel"]` off a Target loaded from
        `config/ooh_specs.json`."""
        return cls(MultiPanelSpec.model_validate(metadata), base_name)

    # ── Pure math (independently testable) ───────────────────────────

    @property
    def master_width(self) -> int:
        s = self.spec
        return s.panel_count * s.panel_width + (s.panel_count - 1) * s.gap_px

    @property
    def master_height(self) -> int:
        return self.spec.panel_height

    def crop_window(self, index: int) -> Tuple[int, int]:
        """(x_start, x_end) of panel `index`'s window in master-canvas
        space."""
        self._check_panel_index(index)
        s = self.spec
        x_start = index * (s.panel_width + s.gap_px)
        return x_start, x_start + s.panel_width

    def master_layer_position(self, index: int) -> Tuple[float, float]:
        """ADR 01: X_offset(k) = Wp/2 - k*(Wp+Gp); Y fixed at Hp/2."""
        self._check_panel_index(index)
        s = self.spec
        x_offset = (s.panel_width / 2.0) - index * (s.panel_width + s.gap_px)
        return x_offset, s.panel_height / 2.0

    def gap_window(self, gap_index: int) -> Tuple[int, int]:
        """(x_start, x_end) of the physical pillar gap between panel
        `gap_index` and panel `gap_index + 1`."""
        if not (0 <= gap_index < self.spec.panel_count - 1):
            raise ValueError(
                f"gap_index {gap_index} out of range for "
                f"{self.spec.panel_count} panels (valid: 0..{self.spec.panel_count - 2})")
        s = self.spec
        x_start = (gap_index + 1) * s.panel_width + gap_index * s.gap_px
        return x_start, x_start + s.gap_px

    def panel_name(self, index: int) -> str:
        self._check_panel_index(index)
        if self.spec.split_naming is not None:
            return self.spec.split_naming[index]
        if self.spec.panel_count == len(_DEFAULT_SPLIT_NAMES):
            return _DEFAULT_SPLIT_NAMES[index]
        return f"PANEL_{index}"

    def _check_panel_index(self, index: int) -> None:
        if not (0 <= index < self.spec.panel_count):
            raise ValueError(
                f"panel index {index} out of range for "
                f"{self.spec.panel_count} panels")

    # ── Plan assembly ─────────────────────────────────────────────────

    def plan(self) -> PanelSlicingPlan:
        s = self.spec

        panels: List[PanelSpec] = []
        for k in range(s.panel_count):
            x_start, x_end = self.crop_window(k)
            name = self.panel_name(k)
            panels.append(PanelSpec(
                index=k,
                name=name,
                comp_name=f"{self.base_name}_{name}_{s.panel_width}x{s.panel_height}",
                width=s.panel_width,
                height=s.panel_height,
                crop_x_start=x_start,
                crop_x_end=x_end,
                master_layer_position=self.master_layer_position(k),
                spatial_token=f"Panel{k + 1:02d}_{name}",
            ))

        gap_zones: List[GapZone] = []
        if s.gap_px > 0:
            for i in range(s.panel_count - 1):
                x_start, x_end = self.gap_window(i)
                gap_zones.append(GapZone(
                    index=i,
                    name=f"GAP_PILLAR_{i + 1:02d}",
                    x_start=x_start,
                    x_end=x_end,
                    width=s.gap_px,
                ))

        return PanelSlicingPlan(
            master_comp_name=f"{self.base_name}_MASTER_{self.master_width}x{self.master_height}",
            master_width=self.master_width,
            master_height=self.master_height,
            panel_count=s.panel_count,
            panel_width=s.panel_width,
            panel_height=s.panel_height,
            gap_px=s.gap_px,
            panels=panels,
            gap_zones=gap_zones,
        )


def synthesize_gap_zone_mask(plan: PanelSlicingPlan) -> np.ndarray:
    """Builds an in-memory safe-zone mask (issue #346's SOE half) treating
    this plan's `gap_zones` as hard CUTOFF strips and everywhere else as
    GO — the same uint8 0/255 grayscale shape
    `core/occlusion/mask_solver.py::OcclusionMask` already accepts
    directly via its ndarray constructor branch, so nothing in the SOE
    engine itself needs to change for this to work.

    Sized to `(master_height, master_width)` — the exact coordinate
    space `gap_zones[].x_start`/`x_end` are computed in (ADR 01 §5.1),
    which is also the target's own declared `width_px`/`height_px` for
    every multi_panel spec in the shipped catalog (verified against
    OOH-064/OOH-066: 3682x1920 on the target equals this plan's
    master_width/master_height exactly) — so a caller can pass this
    straight into `OcclusionMask(mask, target_w, target_h)` with no
    resizing.

    A `gap_px=0` spec produces zero gap_zones (contiguous panels, no
    physical pillar) and this returns an all-GO mask — correct, not a
    special case: there is nothing to occlude.
    """
    mask = np.full((plan.master_height, plan.master_width), 255, dtype=np.uint8)
    for gap in plan.gap_zones:
        mask[:, gap.x_start:gap.x_end] = 0
    return mask
