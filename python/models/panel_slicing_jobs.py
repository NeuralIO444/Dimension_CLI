# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Panel-slicing-plan bridge job/result models (#346 Part C)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import model_validator

from models.bridge_jobs import _BridgeJobBase, _BridgeResultBase


class PanelSlicingPlanJob(_BridgeJobBase):
    """Execute a multi-panel OOH slicing plan. Mirrors DuplicatePlanJob."""

    type: Literal["panel-slicing-plan"]
    plan: dict
    log_path: str
    progress_log_path: str
    babysitter: str
    version: int = 1
    conformed_comp_id: Optional[int] = None
    conformed_comp_name: Optional[str] = None

    @model_validator(mode="after")
    def _exactly_one_comp_ref(self) -> "PanelSlicingPlanJob":
        has_id = self.conformed_comp_id is not None
        has_name = bool(self.conformed_comp_name)
        if has_id and has_name:
            raise ValueError(
                "PanelSlicingPlanJob: specify conformed_comp_id OR "
                "conformed_comp_name, not both"
            )
        if not has_id and not has_name:
            raise ValueError(
                "PanelSlicingPlanJob: exactly one of conformed_comp_id "
                "or conformed_comp_name is required"
            )
        return self


class BridgePanelSlicingPlanResult(_BridgeResultBase):
    """Result payload for a panel-slicing-plan job."""

    job_type: Literal["panel-slicing-plan"]
    panels_made: Optional[int] = None
    gap_guides_made: Optional[int] = None
    errors: Optional[object] = None
    log: Optional[dict] = None
    detail: Optional[str] = None
