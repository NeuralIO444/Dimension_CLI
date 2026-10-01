# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""#346 Part C — dispatch helper for panel-slicing-plan jobs."""

from __future__ import annotations

import os
from typing import Any, Optional

from bridge.sovereign_bridge import TRANSFER_LOG, ScrapeEngineError, SovereignBridge
from core.logger import log
from models.bridge_jobs import BRIDGE_SCHEMA_VERSION
from models.panel_slicing_jobs import PanelSlicingPlanJob


def apply_panel_slicing_plan(
    bridge: SovereignBridge,
    plan: Any,
    log_path: str,
    *,
    conformed_comp_id: Optional[int] = None,
    conformed_comp_name: Optional[str] = None,
    timeout_s: float = 30.0,
) -> dict:
    """Send a panel-slicing-plan job. Does not run unless called."""
    if plan is None:
        raise ValueError("plan is required")
    if not log_path:
        raise ValueError("log_path is required")
    has_id = isinstance(conformed_comp_id, int)
    has_name = bool(conformed_comp_name)
    if not has_id and not has_name:
        raise ValueError(
            "exactly one of conformed_comp_id or conformed_comp_name is required"
        )
    if has_id and has_name:
        raise ValueError(
            "specify conformed_comp_id OR conformed_comp_name, not both"
        )

    if hasattr(plan, "model_dump"):
        plan_dict = plan.model_dump(mode="json")
    else:
        plan_dict = dict(plan)

    log_path_abs = os.path.abspath(log_path)
    parent = os.path.dirname(log_path_abs)
    if parent:
        os.makedirs(parent, exist_ok=True)

    job = {
        "type": "panel-slicing-plan",
        "plan": plan_dict,
        "log_path": log_path_abs,
        "progress_log_path": TRANSFER_LOG,
        "babysitter": os.path.join(bridge._assets_dir(), "Babysitter.jsx"),
        "assets": bridge._assets_dir(),
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "ts": 0.0,
    }
    if has_id:
        job["conformed_comp_id"] = int(conformed_comp_id)
    else:
        job["conformed_comp_name"] = str(conformed_comp_name)

    PanelSlicingPlanJob.model_validate(job)

    try:
        payload = bridge._execute_bridge_job(job, timeout_s)
    except Exception as e:
        raise ScrapeEngineError(
            f"Panel-slicing-plan socket execution failed: {e}"
        ) from e

    log.info(
        "applyPanelSlicingPlan complete",
        extra={
            "status": payload.get("status"),
            "panels_made": payload.get("panels_made"),
            "gap_guides_made": payload.get("gap_guides_made"),
            "errors": payload.get("errors"),
        },
    )
    return payload


def maybe_apply_panel_slicing_plan(
    bridge: SovereignBridge,
    plan: Any,
    log_path: str,
    *,
    conformed_comp_id: Optional[int] = None,
    conformed_comp_name: Optional[str] = None,
    timeout_s: float = 30.0,
) -> Optional[dict]:
    """Dedicated post-conform hook. No job if plan is None.

    Non-`multi_panel` targets (263 of 265 catalog) stay byte-identical:
    `stages/conform.py` writes `panel_slicing_plan=None` onto the chunk
    manifest and does not auto-dispatch. Call this only when a live AE
    session should materialise an existing plan.
    """
    if plan is None:
        return None
    return apply_panel_slicing_plan(
        bridge,
        plan,
        log_path,
        conformed_comp_id=conformed_comp_id,
        conformed_comp_name=conformed_comp_name,
        timeout_s=timeout_s,
    )
