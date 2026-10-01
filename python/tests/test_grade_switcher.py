# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

from core.grade_switcher import GradeSwitcher, GradeSwitcherConfig, GradeRevisionSlot


def test_grade_switcher_config_and_expressions():
    revisions = [
        {"id": "A", "label": "Warm Cinema", "lut_path": "/luts/grade_a.cube"},
        {"id": "B", "label": "Cool Broadcast", "lut_path": "/luts/grade_b.cube"},
        {"id": "C", "label": "Punchy Social", "lut_path": "/luts/grade_c.cube"},
    ]
    cfg = GradeSwitcher.build_config(revisions, active_id="B")
    assert isinstance(cfg, GradeSwitcherConfig)
    assert len(cfg.slots) == 3
    assert cfg.active_revision == "B"
    assert cfg.slots[0].is_active is False
    assert cfg.slots[1].is_active is True
    assert cfg.slider_min == 1
    assert cfg.slider_max == 3

    expr_slot_1 = GradeSwitcher.build_slot_expression(1)
    expr_slot_2 = GradeSwitcher.build_slot_expression(2)
    assert "sel == 1 ? 100 : 0;" in expr_slot_1
    assert "sel == 2 ? 100 : 0;" in expr_slot_2


def test_grade_switcher_serialization():
    slot = GradeRevisionSlot(revision_id="A", label="Grade A Direct", is_active=True)
    assert slot.to_dict()["revision_id"] == "A"
    assert slot.to_dict()["is_active"] is True

    revisions = [
        {
            "id": "A",
            "label": "Grade A Quick",
            "parametric_params": {"red": {"gamma": 1.1}, "green": {"gamma": 1.0}, "blue": {"gamma": 0.9}},
        },
        {
            "id": "B",
            "label": "Grade B 3D LUT",
            "lut_path": "/path/to/lut_b.cube",
        },
    ]
    cfg = GradeSwitcher.build_config(revisions, active_id="A")
    data = cfg.to_dict()

    assert data["layer_name"] == "HORIZON_GRADE_SWITCHER"
    assert data["active_revision"] == "A"
    assert len(data["slots"]) == 2
    assert data["slots"][0]["parametric_params"]["red"]["gamma"] == 1.1
    assert data["slots"][1]["lut_file_path"] == "/path/to/lut_b.cube"
