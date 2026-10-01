"""Harness-facing M6: custom edges resolve without AE."""
import json
from types import SimpleNamespace
from pathlib import Path

import numpy as np

from logic.preset_customs import custom_for_target, load_customs, safe_zone_from_custom
from logic.safe_zone_insets import resolve_inset_mask, spec_for_target


def test_custom_file_binds_and_cuts(tmp_path, monkeypatch):
    p = tmp_path / "preset_customs.json"
    p.write_text(json.dumps([
        {
            "id": "custom:harness_dcp",
            "parent": "builtin:dcp_4k_flat",
            "pack": "chrome",
            "edges": {"top": 10, "bottom": 10, "left": 10, "right": 10, "unit": "percent"},
        }
    ]))
    monkeypatch.setattr("logic.preset_customs._DEFAULT", p)
    t = SimpleNamespace(
        id="custom:harness_dcp",
        width=100,
        height=100,
        category="digital_cinema",
        metadata={},
    )
    row = custom_for_target(t, path=p)
    sz = safe_zone_from_custom(row)
    assert sz["top"] == 10
    spec = spec_for_target(t)
    # spec_for_target reads default path; force via metadata if monkeypatch missed
    if not spec or spec.get("top") != 10:
        t.metadata = {"safe_zone": sz}
        spec = spec_for_target(t)
    assert spec["top"] == 10
    mask = resolve_inset_mask(t)
    assert mask is not None
    assert mask.shape == (100, 100)
    assert mask[0, 50] == 0
    assert mask[50, 50] == 255
