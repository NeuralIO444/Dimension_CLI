# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_scrape_manifest_wire_contract.py
Layer 2 — JSX scrape manifest wire contract (real fixtures only).

Validates fresh session-archive manifests through the extra="forbid"
wire profile. Synthetic inline dicts are forbidden in this module —
see test_layer2_contract_policy.py.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from models.bridge_contract import (  # noqa: E402
    audit_critical_layer_keys,
    parse_jsx_wire_manifest,
)

SESSION_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "session_2026_07_02"
    / "87n_fresh_manifest.json"
)


@pytest.fixture(scope="module")
def fresh_manifest_raw() -> dict:
    assert SESSION_FIXTURE.is_file(), (
        f"Session fixture missing: {SESSION_FIXTURE}"
    )
    return json.loads(SESSION_FIXTURE.read_text(encoding="utf-8"))


class TestScrapeManifestWireContract:
    def test_87n_fresh_manifest_passes_jsx_wire_model(
        self, fresh_manifest_raw: dict,
    ):
        wire = parse_jsx_wire_manifest(fresh_manifest_raw)
        assert wire.status == "OK"
        assert wire.recursive_scrape_meta is not None
        assert wire.recursive_scrape_meta.total_layers == len(wire.layers)

    def test_87n_layers_have_uid(self, fresh_manifest_raw: dict):
        wire = parse_jsx_wire_manifest(fresh_manifest_raw)
        missing = [layer.name for layer in wire.layers if not layer.uid]
        assert missing == [], f"layers missing uid: {missing}"

    def test_critical_keys_not_silently_absent_on_tagged_layers(
        self, fresh_manifest_raw: dict,
    ):
        wire = parse_jsx_wire_manifest(fresh_manifest_raw)
        tagged_without_source = [
            layer.name
            for layer in wire.layers
            if layer.content_tag and not layer.content_tag_source
        ]
        assert tagged_without_source == [], (
            "tagged layers must carry content_tag_source: "
            f"{tagged_without_source}"
        )

    def test_wire_audit_reports_structural_layer_gaps(
        self, fresh_manifest_raw: dict,
    ):
        """Cameras/lights may omit world_bounds — audit is soft, not empty."""
        wire = parse_jsx_wire_manifest(fresh_manifest_raw)
        warnings = audit_critical_layer_keys(wire)
        # Real fixture: Camera 1 lacks world_bounds while AV layers have it.
        assert any("Camera 1" in w for w in warnings)

    def test_profile_conflict_reason_tolerated_on_python_resaved_manifest(
        self, fresh_manifest_raw: dict,
    ):
        """Track B / B1 regression test — caught live in AE smoke testing
        the same day it shipped. The surveyor writes
        profile_conflict_reason onto every layer (None when no conflict);
        is_fresh_jsx_manifest() then mis-classifies that Python-resaved
        manifest as fresh JSX output on the next load, routing it through
        this extra="forbid" model. Mutates a deep copy of the REAL fixture
        (not a synthetic dict literal — test_layer2_contract_policy.py's
        AST scan only bans inline dict literals passed to the parser, not
        mutated copies of a real fixture) to reproduce exactly what a
        post-survey re-save looks like."""
        import copy
        mutated = copy.deepcopy(fresh_manifest_raw)
        for layer in mutated["layers"]:
            layer["profile_conflict_reason"] = None
        mutated["layers"][0]["profile_conflict_reason"] = (
            'Profile rule "TT_" would assign "TOP" but manual tag is "BOTTOM"'
        )
        wire = parse_jsx_wire_manifest(mutated)
        assert wire.status == "OK"
        assert wire.layers[0].profile_conflict_reason == (
            'Profile rule "TT_" would assign "TOP" but manual tag is "BOTTOM"'
        )

    def test_typographic_info_tolerated_on_jsx_manifest(
        self, fresh_manifest_raw: dict,
    ):
        """TASK-ENG-01 regression: SovCore_Layer.jsx emits typographic_info
        on text layers. JsxScrapeManifestWire must accept it without throwing
        extra_forbidden."""
        import copy
        mutated = copy.deepcopy(fresh_manifest_raw)
        mutated["layers"][0]["typographic_info"] = {
            "font_size_pt": 48.0,
            "line_count": 1,
            "char_count": 12,
            "font_name": "Arial",
        }
        wire = parse_jsx_wire_manifest(mutated)
        assert wire.status == "OK"
        assert wire.layers[0].typographic_info["font_size_pt"] == 48.0