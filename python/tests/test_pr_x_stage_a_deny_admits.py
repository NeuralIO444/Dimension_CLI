# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_pr_x_stage_a_deny_admits.py
PR-X Stage A — pin the telemetry surfacing contract.

What this test pins
-------------------
Sovereign_Core.jsx's scrapeUnified merger emits a parallel-run
telemetry entry into the top-level errors array for any v5Layer
field that (a) is not in V5_LAYER_KEYS, (b) is not in
V5_LAYER_DENY_KEYS, and (c) is not in V5_LAYER_HANDLED_ELSEWHERE.
The entry shape is:

    {
        "severity": "info",
        "operation": "v5_keys.deny_admits",
        "error":      "<field-name>",
        "layer_index": <int>
    }

The bridge (`sovereign_bridge.parse_manifest`) surfaces these
through to `log.info` so Stage B telemetry collection has a
queryable signal in the Python log stream.

This test exercises the bridge side of that contract: given a
manifest that carries such an entry (the shape the JSX will
emit), parse_manifest MUST route it to log.info with the
operation, field name, and layer_index preserved in the extras.

Why a doctored fixture, not a JSX integration test
--------------------------------------------------
JSX is untestable from pytest. The bridge surfacing contract is
Python-side and is what would break if the bridge's severity
routing regresses. The JSX emission itself is verified by the
Stage A smoke pass (see docs/roadmap/PR-X.md §5 Stage A ship
gate). This test is the CI guard that catches a bridge regression
between smokes.

Fixture source: a verbatim copy of the Slot 4 clean fixture
(Final Comp, 2 layers, errors: []) with one doctored info entry
appended to errors[]. The base fixture proves "real manifest
shapes parse"; the doctored entry proves "info-severity entries
route to log.info."
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "slot_4"
CLEAN_MANIFEST = FIXTURES_DIR / "final_comp_2layers_may13.json"


def _doctored_manifest_with_admit(tmp_path: Path,
                                  field: str,
                                  layer_index: int) -> Path:
    """Copy the clean Slot 4 fixture, append one Stage A info entry
    to errors[], return the new path. Mirrors the doctored-fixture
    pattern used in Slot 4's drop-detector tests."""
    with open(CLEAN_MANIFEST, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    raw.setdefault("errors", [])
    raw["errors"].append({
        "severity": "info",
        "operation": "v5_keys.deny_admits",
        "error":      field,
        "layer_index": layer_index,
    })

    out = tmp_path / "stage_a_admit_fixture.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(raw, fh)
    return out


class TestStageADenyAdmitSurfacing:
    """The bridge routes severity='info' entries with the
    Stage A operation tag to log.info, carrying the field name
    and layer_index in the extras. Pins the half of the
    contract that lives in Python."""

    def test_info_entry_routes_to_log_info(self, tmp_path, monkeypatch):
        """A manifest with severity='info' and the deny-admit
        operation tag MUST cause log.info to fire — not warning,
        not error. Pre-PR-X this branch routed everything
        non-fatal/error to log.warning, which would have made
        Stage A telemetry indistinguishable from real warnings.
        """
        from bridge import sovereign_bridge

        # Capture log calls by replacing the module-level logger.
        # The dimension logger sets propagate=False, so caplog
        # won't see these — direct monkeypatch is the simplest
        # path that doesn't depend on logger plumbing.
        mock_log = MagicMock()
        monkeypatch.setattr(sovereign_bridge, "log", mock_log)

        manifest_path = _doctored_manifest_with_admit(
            tmp_path, field="future_unknown_field", layer_index=2
        )
        bridge = sovereign_bridge.SovereignBridge(
            project_root=str(tmp_path)
        )
        bridge.parse_manifest(str(manifest_path))

        # Find the call(s) routed to log.info. The bridge logs the
        # "Manifest parsed" line at info too (line ~451), so we
        # filter by the deny-admit operation tag.
        info_calls = [
            c for c in mock_log.info.call_args_list
            if c.kwargs.get("extra", {}).get("operation")
                == "v5_keys.deny_admits"
        ]
        assert len(info_calls) == 1, (
            f"expected exactly one log.info call with operation="
            f"'v5_keys.deny_admits'; saw {len(info_calls)}. "
            f"All info calls: {mock_log.info.call_args_list}"
        )

        extras = info_calls[0].kwargs["extra"]
        assert extras["severity"] == "info"
        assert extras["operation"] == "v5_keys.deny_admits"
        assert extras["message"] == "future_unknown_field", (
            "field name must arrive in the 'message' extra so "
            "Stage B can grep for it"
        )
        assert extras["layer_index"] == 2, (
            "layer_index must be preserved so Stage B can locate "
            "which layer admitted the field"
        )

        # Negative half — the entry MUST NOT route to log.warning
        # or log.error. If it does, the Stage A signal mixes into
        # the existing warning/error streams and review breaks.
        warning_admits = [
            c for c in mock_log.warning.call_args_list
            if c.kwargs.get("extra", {}).get("operation")
                == "v5_keys.deny_admits"
        ]
        error_admits = [
            c for c in mock_log.error.call_args_list
            if c.kwargs.get("extra", {}).get("operation")
                == "v5_keys.deny_admits"
        ]
        assert warning_admits == [], (
            f"deny-admit entries leaked into log.warning: "
            f"{warning_admits}"
        )
        assert error_admits == [], (
            f"deny-admit entries leaked into log.error: "
            f"{error_admits}"
        )
