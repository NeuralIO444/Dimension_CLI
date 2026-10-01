# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Issue #346 Part C — panel-slicing-plan job schema + dispatch helper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from models.bridge_jobs import BRIDGE_SCHEMA_VERSION, parse_result, parse_typed_job
from models.panel_slicing_jobs import PanelSlicingPlanJob, BridgePanelSlicingPlanResult

_REPO = Path(__file__).resolve().parents[2]


def _job_dict(**overrides):
    base = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "assets": "/tmp/assets",
        "ts": 1.0,
        "type": "panel-slicing-plan",
        "plan": {"master_width": 3682, "panels": []},
        "log_path": "/tmp/panel_slicing_log.json",
        "progress_log_path": "/tmp/transfer_status.log",
        "babysitter": "/tmp/Babysitter.jsx",
        "conformed_comp_name": "[DIMENSION] Market 15 Liveboard",
    }
    base.update(overrides)
    return base


def test_panel_slicing_job_accepts_name_ref():
    j = PanelSlicingPlanJob.model_validate(_job_dict())
    assert j.type == "panel-slicing-plan"
    assert j.conformed_comp_name == "[DIMENSION] Market 15 Liveboard"
    assert j.plan["master_width"] == 3682


def test_panel_slicing_job_requires_exactly_one_comp_ref():
    d = _job_dict()
    del d["conformed_comp_name"]
    with pytest.raises(ValidationError):
        PanelSlicingPlanJob.model_validate(d)
    with pytest.raises(ValidationError):
        PanelSlicingPlanJob.model_validate(_job_dict(conformed_comp_id=12))


def test_parse_typed_job_union_accepts_panel_slicing_plan():
    j = parse_typed_job(_job_dict())
    assert isinstance(j, PanelSlicingPlanJob)
    assert j.type == "panel-slicing-plan"


def test_parse_result_union_accepts_panel_slicing_plan():
    r = parse_result({
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "job_type": "panel-slicing-plan",
        "status": "OK",
        "panels_made": 3,
        "gap_guides_made": 2,
        "errors": 0,
    })
    assert isinstance(r, BridgePanelSlicingPlanResult)
    assert r.panels_made == 3


def test_apply_panel_slicing_plan_builds_kebab_job(tmp_path):
    from bridge.panel_slicing import apply_panel_slicing_plan
    from bridge.sovereign_bridge import SovereignBridge

    log_path = tmp_path / "panel_slicing_log.json"
    captured = {}

    def _fake_execute(job, timeout_s):
        captured["job"] = job
        captured["timeout_s"] = timeout_s
        return {"status": "OK", "panels_made": 3, "gap_guides_made": 2, "errors": 0}

    bridge = SovereignBridge(project_root=str(tmp_path))
    with patch.object(bridge, "_execute_bridge_job", side_effect=_fake_execute):
        payload = apply_panel_slicing_plan(
            bridge,
            {"master_width": 3682},
            log_path=str(log_path),
            conformed_comp_name="[DIMENSION] Market 15 Liveboard",
        )

    assert payload["status"] == "OK"
    assert captured["job"]["type"] == "panel-slicing-plan"
    assert captured["job"]["conformed_comp_name"] == "[DIMENSION] Market 15 Liveboard"
    assert "conformed_comp_id" not in captured["job"]
    assert captured["job"]["plan"]["master_width"] == 3682


def test_apply_panel_slicing_plan_rejects_missing_plan():
    from bridge.panel_slicing import apply_panel_slicing_plan
    from bridge.sovereign_bridge import SovereignBridge

    bridge = SovereignBridge(project_root="/tmp")
    with pytest.raises(ValueError, match="plan is required"):
        apply_panel_slicing_plan(
            bridge, None, log_path="/tmp/x.json", conformed_comp_name="x"
        )


def test_maybe_apply_noop_when_plan_is_none():
    from bridge.panel_slicing import maybe_apply_panel_slicing_plan
    from bridge.sovereign_bridge import SovereignBridge

    bridge = SovereignBridge(project_root="/tmp")
    with patch.object(bridge, "_execute_bridge_job") as mock_exec:
        out = maybe_apply_panel_slicing_plan(
            bridge, None, log_path="/tmp/x.json", conformed_comp_name="x"
        )
    assert out is None
    mock_exec.assert_not_called()


def test_maybe_apply_dispatches_when_plan_present(tmp_path):
    from bridge.panel_slicing import maybe_apply_panel_slicing_plan
    from bridge.sovereign_bridge import SovereignBridge

    captured = {}

    def _fake_execute(job, timeout_s):
        captured["job"] = job
        return {"status": "OK", "panels_made": 3}

    bridge = SovereignBridge(project_root=str(tmp_path))
    with patch.object(bridge, "_execute_bridge_job", side_effect=_fake_execute):
        payload = maybe_apply_panel_slicing_plan(
            bridge,
            {"master_width": 3682},
            log_path=str(tmp_path / "panel_slicing_log.json"),
            conformed_comp_id=99,
        )
    assert payload["status"] == "OK"
    assert captured["job"]["type"] == "panel-slicing-plan"
    assert captured["job"]["conformed_comp_id"] == 99


def test_sovereign_bridge_method_delegates(tmp_path):
    from bridge.sovereign_bridge import SovereignBridge

    captured = {}

    def _fake_execute(job, timeout_s):
        captured["job"] = job
        return {"status": "OK", "panels_made": 1}

    bridge = SovereignBridge(project_root=str(tmp_path))
    with patch.object(bridge, "_execute_bridge_job", side_effect=_fake_execute):
        payload = bridge.apply_panel_slicing_plan(
            {"master_width": 3682},
            log_path=str(tmp_path / "panel_slicing_log.json"),
            conformed_comp_name="MASTER",
        )
        skipped = bridge.maybe_apply_panel_slicing_plan(
            None,
            log_path=str(tmp_path / "panel_slicing_log.json"),
            conformed_comp_name="MASTER",
        )
    assert payload["status"] == "OK"
    assert captured["job"]["type"] == "panel-slicing-plan"
    assert skipped is None


def test_socket_server_has_panel_slicing_plan_branch():
    text = (_REPO / "Scripts" / "Dimension_Assets" / "socket_server.jsx").read_text(
        encoding="utf-8"
    )
    assert 'request.type === "panel-slicing-plan"' in text
    assert "Bps.applyPanelSlicingPlan" in text


def test_launcher_has_panel_slicing_plan_handler():
    text = (_REPO / "Scripts" / "Dimension_Launcher.jsx").read_text(encoding="utf-8")
    assert 'jobType === "panel-slicing-plan"' in text
    assert "_handlePanelSlicingPlanJob" in text
    assert "applyPanelSlicingPlan" in text


def test_host_jsx_fast_fails_panel_slicing_plan():
    text = (_REPO / "cep" / "jsx" / "host.jsx").read_text(encoding="utf-8")
    assert "panel-slicing-plan" in text
    assert "_claimAndRejectUnsupported" in text
