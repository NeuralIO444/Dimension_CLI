# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_bridge_jobs.py
PR-B Layer 1 — Python-only unit tests for the bridge contract schemas
in python/models/bridge_jobs.py.

What these tests cover (and why synthetic dicts are appropriate)
-----------------------------------------------------------------
The contract under test here IS the Python schema. We're verifying:

  - Required fields enforced
  - Optional fields default correctly
  - Discriminator dispatch routes to the right concrete class
  - Cross-field validators (DuplicatePlanJob exactly-one,
    MaskToggleJob import-requires-mask-path) fire correctly
  - Wire format kebab-case verbatim (NOT snake_case)
  - schema_version mismatch refuses with BridgeSchemaVersionError
  - parse_typed_job vs parse_inject_job vs parse_result behave
    differently as documented

For these assertions, synthetic dicts are correct — there's no
JSX-versus-Python contract to test, just the Python schema's
internal consistency.

Layer 2 (test_bridge_contracts_jsx.py) is the synthetic-fixture
anti-pattern guard. Layer 2 consumes manifests that JSX actually
wrote to verify the Python schema accepts wire reality. Layer 1
(this file) cannot replace Layer 2 — it can only verify the
schema is internally consistent, NOT that JSX writes payloads
matching it.

See CLAUDE.md "Anti-patterns — Trusting synthetic Python test
fixtures..." for the case study (PR #45 + PR #48).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from pydantic import ValidationError  # noqa: E402

from models.bridge_jobs import (  # noqa: E402
    BRIDGE_SCHEMA_VERSION,
    BridgeColorMatchRenderResult,
    BridgeDuplicatePlanResult,
    BridgeMaskToggleResult,
    BridgePanelSlicingPlanResult,
    BridgeQueryLayerStateResult,
    BridgeReconPlanResult,
    BridgeScrapeResult,
    BridgeSchemaVersionError,
    BridgeSelectLayerResult,
    BridgeTagWriteResult,
    ColorMatchRenderJob,
    DuplicatePlanJob,
    InjectJob,
    MaskToggleJob,
    PanelSlicingPlanJob,
    QueryLayerStateJob,
    ReconPlanJob,
    ScrapeJob,
    SelectLayerJob,
    TagWriteJob,
    parse_inject_job,
    parse_result,
    parse_typed_job,
)


# ── Helpers ──────────────────────────────────────────────────────


def _scrape_dict(**overrides):
    base = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "type": "scrape",
        "manifest": "/tmp/m.json",
        "mode": "standard",
        "assets": "/tmp/assets",
        "ts": 1.0,
        "result": "/tmp/r.json",
    }
    base.update(overrides)
    return base


def _tag_write_dict(**overrides):
    base = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "type": "tag-write",
        "uid": "u-1",
        "tag": "HERO",
        "assets": "/tmp/assets",
        "ts": 1.0,
        "result": "/tmp/r.json",
    }
    base.update(overrides)
    return base


def _select_layer_dict(**overrides):
    base = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "type": "select-layer",
        "uid": "u-1",
        "assets": "/tmp/assets",
        "ts": 1.0,
        "result": "/tmp/r.json",
    }
    base.update(overrides)
    return base


def _query_layer_state_dict(**overrides):
    base = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "type": "query-layer-state",
        "uid": "u-1",
        "property_paths": ["position", "scale"],
        "assets": "/tmp/assets",
        "ts": 1.0,
        "result": "/tmp/r.json",
    }
    base.update(overrides)
    return base


def _duplicate_plan_dict(**overrides):
    base = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "type": "duplicate-plan",
        "plan": {"session_id": "sess-1"},
        "log_path": "/tmp/dup.json",
        "progress_log_path": "/tmp/transfer.log",
        "babysitter": "/Scripts/Dimension_Assets/Babysitter.jsx",
        "assets": "/tmp/assets",
        "ts": 1.0,
        "result": "/tmp/r.json",
        "conformed_comp_id": 42,
    }
    base.update(overrides)
    return base


def _panel_slicing_plan_dict(**overrides):
    base = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "type": "panel-slicing-plan",
        "plan": {"master_width": 3682, "panel_count": 3},
        "log_path": "/tmp/panel_slicing_log.json",
        "progress_log_path": "/tmp/transfer.log",
        "babysitter": "/Scripts/Dimension_Assets/Babysitter.jsx",
        "assets": "/tmp/assets",
        "ts": 1.0,
        "result": "/tmp/r.json",
        "conformed_comp_name": "[DIMENSION] Market 15 Liveboard",
    }
    base.update(overrides)
    return base


def _recon_plan_dict(**overrides):
    base = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "type": "recon-plan",
        "plan": {"new_comps": [{"name": "Recon_9x16", "width": 1080, "height": 1920}]},
        "babysitter": "/Scripts/Dimension_Assets/Babysitter.jsx",
        "assets": "/tmp/assets",
        "ts": 1.0,
        "result": "/tmp/r.json",
    }
    base.update(overrides)
    return base


def _mask_toggle_dict(**overrides):
    base = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "type": "mask-toggle",
        "action": "import",
        "mask_path": "/tmp/mask.png",
        "babysitter": "/Scripts/Dimension_Assets/Babysitter.jsx",
        "assets": "/tmp/assets",
        "ts": 1.0,
        "result": "/tmp/r.json",
    }
    base.update(overrides)
    return base


def _color_match_render_dict(**overrides):
    base = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "type": "color-match-render",
        "comp_id": "42",
        "comp_name": "HERO_PRECOMP",
        "output_path": "/tmp/color_match_42_reference.png",
        "assets": "/tmp/assets",
        "ts": 1.0,
        "result": "/tmp/r.json",
    }
    base.update(overrides)
    return base


def _inject_dict(**overrides):
    base = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        # NB: no `type` field — inject is the legacy default
        "manifest": "/tmp/m.json",
        "log": "/tmp/transfer.log",
        "babysitter": "/Scripts/Dimension_Assets/Babysitter.jsx",
        "auditor": "/Scripts/Dimension_Assets/Auditor.jsx",
        "ts": 1.0,
        "version": 1,
    }
    base.update(overrides)
    return base


def _result_dict(job_type, **overrides):
    base = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "job_type": job_type,
        "status": "OK",
    }
    base.update(overrides)
    return base


# ── Discriminator dispatch ───────────────────────────────────────


class TestTypedJobDiscriminator:
    """parse_typed_job routes by `type` to the right concrete class."""

    def test_scrape_dispatches_to_scrape_job(self):
        j = parse_typed_job(_scrape_dict())
        assert isinstance(j, ScrapeJob)
        assert j.type == "scrape"

    def test_tag_write_dispatches_to_tag_write_job(self):
        j = parse_typed_job(_tag_write_dict())
        assert isinstance(j, TagWriteJob)
        assert j.type == "tag-write"

    def test_select_layer_dispatches_to_select_layer_job(self):
        j = parse_typed_job(_select_layer_dict())
        assert isinstance(j, SelectLayerJob)
        assert j.type == "select-layer"

    def test_query_layer_state_dispatches_to_query_layer_state_job(self):
        j = parse_typed_job(_query_layer_state_dict())
        assert isinstance(j, QueryLayerStateJob)
        assert j.type == "query-layer-state"

    def test_duplicate_plan_dispatches_to_duplicate_plan_job(self):
        j = parse_typed_job(_duplicate_plan_dict())
        assert isinstance(j, DuplicatePlanJob)
        assert j.type == "duplicate-plan"

    def test_panel_slicing_plan_dispatches_to_panel_slicing_plan_job(self):
        j = parse_typed_job(_panel_slicing_plan_dict())
        assert isinstance(j, PanelSlicingPlanJob)
        assert j.type == "panel-slicing-plan"

    def test_mask_toggle_dispatches_to_mask_toggle_job(self):
        j = parse_typed_job(_mask_toggle_dict())
        assert isinstance(j, MaskToggleJob)
        assert j.type == "mask-toggle"

    def test_recon_plan_dispatches_to_recon_plan_job(self):
        j = parse_typed_job(_recon_plan_dict())
        assert isinstance(j, ReconPlanJob)
        assert j.type == "recon-plan"

    def test_color_match_render_dispatches_to_color_match_render_job(self):
        j = parse_typed_job(_color_match_render_dict())
        assert isinstance(j, ColorMatchRenderJob)
        assert j.type == "color-match-render"


class TestKebabCaseWireFormat:
    """Wire format is kebab-case verbatim, NOT snake_case. The
    earlier scope doc said `tag_write`, `select_layer`, etc.; the
    actual JSX writes kebab-case. These tests pin the wire values
    so a future regression that normalizes to snake_case fails CI."""

    def test_snake_case_tag_write_rejected(self):
        with pytest.raises(ValidationError):
            parse_typed_job(_tag_write_dict(type="tag_write"))

    def test_snake_case_select_layer_rejected(self):
        with pytest.raises(ValidationError):
            parse_typed_job(_select_layer_dict(type="select_layer"))

    def test_snake_case_duplicate_plan_rejected(self):
        with pytest.raises(ValidationError):
            parse_typed_job(_duplicate_plan_dict(type="duplicate_plan"))

    def test_snake_case_panel_slicing_plan_rejected(self):
        with pytest.raises(ValidationError):
            parse_typed_job(_panel_slicing_plan_dict(type="panel_slicing_plan"))

    def test_snake_case_mask_toggle_rejected(self):
        with pytest.raises(ValidationError):
            parse_typed_job(_mask_toggle_dict(type="mask_toggle"))

    def test_unknown_type_rejected(self):
        with pytest.raises(ValidationError):
            parse_typed_job(_scrape_dict(type="not-a-real-type"))


# ── Schema version refusal ───────────────────────────────────────


class TestSchemaVersionRefusal:
    """Strict refusal globally on schema_version mismatch — see
    bridge_jobs.py module docstring + CLAUDE.md sharp edge on
    synthetic-fixture anti-pattern."""

    def test_typed_job_with_wrong_version_refused(self):
        with pytest.raises(BridgeSchemaVersionError) as exc_info:
            parse_typed_job(_scrape_dict(schema_version="99.99"))
        assert exc_info.value.expected == BRIDGE_SCHEMA_VERSION
        assert exc_info.value.received == "99.99"
        assert exc_info.value.source == "job"

    def test_typed_job_with_missing_version_refused(self):
        d = _scrape_dict()
        del d["schema_version"]
        with pytest.raises(BridgeSchemaVersionError) as exc_info:
            parse_typed_job(d)
        assert exc_info.value.received is None

    def test_inject_job_with_wrong_version_refused(self):
        with pytest.raises(BridgeSchemaVersionError):
            parse_inject_job(_inject_dict(schema_version="0.0"))

    def test_inject_job_with_missing_version_refused(self):
        d = _inject_dict()
        del d["schema_version"]
        with pytest.raises(BridgeSchemaVersionError):
            parse_inject_job(d)

    def test_result_with_wrong_version_refused(self):
        with pytest.raises(BridgeSchemaVersionError):
            parse_result(_result_dict("scrape", schema_version="2.0"))

    def test_result_with_missing_version_refused(self):
        d = _result_dict("scrape")
        del d["schema_version"]
        with pytest.raises(BridgeSchemaVersionError):
            parse_result(d)

    def test_error_message_is_actionable(self):
        """The error message tells the user how to fix the problem."""
        try:
            parse_result(_result_dict("scrape", schema_version="bogus"))
        except BridgeSchemaVersionError as e:
            msg = str(e)
            assert "Reload the AE Dimension panel" in msg
            assert BRIDGE_SCHEMA_VERSION in msg
            assert "bogus" in msg


# ── Cross-field validators ───────────────────────────────────────


class TestDuplicatePlanCompRefValidator:
    """DuplicatePlanJob requires exactly one of conformed_comp_id
    or conformed_comp_name. Mirrors the existing dispatch-side check
    in sovereign_bridge.py:556-565."""

    def test_id_only_accepted(self):
        j = parse_typed_job(_duplicate_plan_dict(
            conformed_comp_id=7, conformed_comp_name=None,
        ))
        assert j.conformed_comp_id == 7
        assert j.conformed_comp_name is None

    def test_name_only_accepted(self):
        d = _duplicate_plan_dict()
        del d["conformed_comp_id"]
        d["conformed_comp_name"] = "[DIMENSION] TIKTOK"
        j = parse_typed_job(d)
        assert j.conformed_comp_name == "[DIMENSION] TIKTOK"
        assert j.conformed_comp_id is None

    def test_both_refused(self):
        with pytest.raises(ValidationError) as exc_info:
            parse_typed_job(_duplicate_plan_dict(
                conformed_comp_id=7,
                conformed_comp_name="[DIMENSION] TIKTOK",
            ))
        assert "not both" in str(exc_info.value)

    def test_neither_refused(self):
        d = _duplicate_plan_dict()
        del d["conformed_comp_id"]
        with pytest.raises(ValidationError) as exc_info:
            parse_typed_job(d)
        assert "exactly one" in str(exc_info.value)


class TestPanelSlicingPlanCompRefValidator:
    """PanelSlicingPlanJob mirrors DuplicatePlanJob's exactly-one
    conformed_comp_id / conformed_comp_name cross-field validator."""

    def test_id_only_accepted(self):
        d = _panel_slicing_plan_dict()
        del d["conformed_comp_name"]
        d["conformed_comp_id"] = 7
        j = parse_typed_job(d)
        assert j.conformed_comp_id == 7
        assert j.conformed_comp_name is None

    def test_name_only_accepted(self):
        j = parse_typed_job(_panel_slicing_plan_dict())
        assert j.conformed_comp_name == "[DIMENSION] Market 15 Liveboard"
        assert j.conformed_comp_id is None

    def test_both_refused(self):
        with pytest.raises(ValidationError) as exc_info:
            parse_typed_job(_panel_slicing_plan_dict(
                conformed_comp_id=7,
                conformed_comp_name="[DIMENSION] Market 15 Liveboard",
            ))
        assert "not both" in str(exc_info.value)

    def test_neither_refused(self):
        d = _panel_slicing_plan_dict()
        del d["conformed_comp_name"]
        with pytest.raises(ValidationError) as exc_info:
            parse_typed_job(d)
        assert "exactly one" in str(exc_info.value)


class TestMaskToggleActionValidator:
    """MaskToggleJob with action="import" requires mask_path."""

    def test_import_with_mask_path_accepted(self):
        j = parse_typed_job(_mask_toggle_dict(
            action="import", mask_path="/tmp/m.png",
        ))
        assert j.action == "import"
        assert j.mask_path == "/tmp/m.png"

    def test_import_without_mask_path_refused(self):
        with pytest.raises(ValidationError) as exc_info:
            parse_typed_job(_mask_toggle_dict(
                action="import", mask_path=None,
            ))
        assert "mask_path is required" in str(exc_info.value)

    def test_remove_without_mask_path_accepted(self):
        """Remove path doesn't need mask_path — Babysitter knows
        which mask to pull based on the active comp/preset."""
        j = parse_typed_job(_mask_toggle_dict(
            action="remove", mask_path=None,
        ))
        assert j.action == "remove"
        assert j.mask_path is None

    def test_remove_with_mask_path_accepted(self):
        """A mask_path on remove is harmless — the JSX side ignores
        it. Schema admits it."""
        j = parse_typed_job(_mask_toggle_dict(
            action="remove", mask_path="/tmp/m.png",
        ))
        assert j.action == "remove"

    def test_unknown_action_rejected(self):
        with pytest.raises(ValidationError):
            parse_typed_job(_mask_toggle_dict(action="toggle"))


# ── Required field enforcement ───────────────────────────────────


class TestRequiredFields:
    """Each job descriptor's required fields fail validation when
    omitted. Spot-checking one required field per job type — the
    full Pydantic field-by-field enforcement is library behavior,
    not something to re-test exhaustively."""

    def test_scrape_requires_manifest(self):
        d = _scrape_dict()
        del d["manifest"]
        with pytest.raises(ValidationError):
            parse_typed_job(d)

    def test_tag_write_requires_uid(self):
        d = _tag_write_dict()
        del d["uid"]
        with pytest.raises(ValidationError):
            parse_typed_job(d)

    def test_select_layer_requires_uid(self):
        d = _select_layer_dict()
        del d["uid"]
        with pytest.raises(ValidationError):
            parse_typed_job(d)

    def test_query_layer_state_requires_uid(self):
        d = _query_layer_state_dict()
        del d["uid"]
        with pytest.raises(ValidationError):
            parse_typed_job(d)

    def test_query_layer_state_requires_non_empty_property_paths(self):
        with pytest.raises(ValidationError):
            parse_typed_job(_query_layer_state_dict(property_paths=[]))

    def test_duplicate_plan_requires_log_path(self):
        d = _duplicate_plan_dict()
        del d["log_path"]
        with pytest.raises(ValidationError):
            parse_typed_job(d)

    def test_mask_toggle_requires_action(self):
        d = _mask_toggle_dict()
        del d["action"]
        with pytest.raises(ValidationError):
            parse_typed_job(d)

    def test_inject_requires_manifest(self):
        d = _inject_dict()
        del d["manifest"]
        with pytest.raises(ValidationError):
            parse_inject_job(d)

    def test_color_match_render_requires_comp_id(self):
        d = _color_match_render_dict()
        del d["comp_id"]
        with pytest.raises(ValidationError):
            parse_typed_job(d)

    def test_color_match_render_requires_output_path(self):
        d = _color_match_render_dict()
        del d["output_path"]
        with pytest.raises(ValidationError):
            parse_typed_job(d)


# ── Optional fields default correctly ────────────────────────────


class TestOptionalFieldDefaults:
    """Optional fields default to None or sensible scalar defaults."""

    def test_scrape_mode_defaults_to_standard(self):
        d = _scrape_dict()
        del d["mode"]
        j = parse_typed_job(d)
        assert j.mode == "standard"

    def test_tag_write_optional_fallbacks_default_none(self):
        """v5.8.13 fallback fields. All three optional."""
        j = parse_typed_job(_tag_write_dict())
        assert j.layer_index is None
        assert j.layer_name is None
        assert j.comp_name is None

    def test_select_layer_optional_fallbacks_default_none(self):
        """Bug F C — SelectLayerJob mirrors TagWriteJob's three
        Optional fallback fields (layer_index, layer_name,
        comp_name). All default to None for backwards-compat with
        existing one-shot select calls."""
        j = parse_typed_job(_select_layer_dict())
        assert j.layer_index is None
        assert j.layer_name is None
        assert j.comp_name is None

    def test_select_layer_with_fallbacks_round_trips(self):
        """Bug F C — when the bridge's select_layer() is called
        with the new kwargs, all three appear on the parsed job."""
        j = parse_typed_job(_select_layer_dict(
            layer_index=17,
            layer_name="hero_layer",
            comp_name="MyComp",
        ))
        assert j.layer_index == 17
        assert j.layer_name == "hero_layer"
        assert j.comp_name == "MyComp"

    def test_query_layer_state_optional_fallbacks_default_none(self):
        """QueryLayerStateJob mirrors SelectLayerJob's three Optional
        fallback fields (layer_index, layer_name, comp_name)."""
        j = parse_typed_job(_query_layer_state_dict())
        assert j.layer_index is None
        assert j.layer_name is None
        assert j.comp_name is None

    def test_query_layer_state_time_s_defaults_none(self):
        """Omitted time_s means JSX reads the rest-pose static value,
        not comp.time. None is the signal for that default path."""
        j = parse_typed_job(_query_layer_state_dict())
        assert j.time_s is None

    def test_inject_assets_optional(self):
        """launcher.py inject doesn't pass `assets` (the babysitter
        and auditor paths are pre-resolved). PR-B relaxed this from
        required-on-base to Optional-on-InjectJob."""
        d = _inject_dict()
        # Already absent in the helper — verify it parses.
        j = parse_inject_job(d)
        assert j.assets is None

    def test_inject_result_optional(self):
        """Inject is heartbeat-driven, not result-file-driven, so it
        never gets a result path stamped onto the descriptor."""
        j = parse_inject_job(_inject_dict())
        assert j.result is None


# ── Result discriminator dispatch ────────────────────────────────


class TestResultDiscriminator:
    """parse_result routes by `job_type` to the right result class."""

    def test_scrape_result(self):
        r = parse_result(_result_dict(
            "scrape", manifest="/tmp/m.json", comp="MyComp",
        ))
        assert isinstance(r, BridgeScrapeResult)
        assert r.manifest == "/tmp/m.json"

    def test_tag_write_result_with_full_success_fields(self):
        r = parse_result(_result_dict(
            "tag-write", uid="u-1", tag="HERO",
            source="manual_comment", label=1,
            layer_index=17, layer_name="hero_layer",
        ))
        assert isinstance(r, BridgeTagWriteResult)
        assert r.tag == "HERO"
        assert r.source == "manual_comment"
        assert r.label == 1

    def test_select_layer_result_with_reason(self):
        r = parse_result(_result_dict(
            "select-layer", status="POLLER_STALE",
            reason="heartbeat 12s old",
        ))
        assert isinstance(r, BridgeSelectLayerResult)
        assert r.status == "POLLER_STALE"
        assert r.reason == "heartbeat 12s old"

    def test_select_layer_result_with_ok_layer_fields(self):
        """OK path: JSX emits uid + layer_index + layer_name.
        Pre-PR-E.2 the schema lacked layer_index/layer_name and a
        strict parse would have rejected the wire shape. Pinned
        2026-04-29 alongside the NOT_FOUND fix (Bug A)."""
        r = parse_result(_result_dict(
            "select-layer", status="OK", uid="u-42",
            layer_index=17, layer_name="hero_layer",
        ))
        assert isinstance(r, BridgeSelectLayerResult)
        assert r.status == "OK"
        assert r.uid == "u-42"
        assert r.layer_index == 17
        assert r.layer_name == "hero_layer"

    def test_select_layer_result_not_found(self):
        """PR-E.2 Bug A: JSX emits status=NOT_FOUND with the echoed
        uid when findLayerByUID returns null. Pre-fix the schema's
        Literal omitted NOT_FOUND so a strict parse would have
        rejected it."""
        r = parse_result(_result_dict(
            "select-layer", status="NOT_FOUND", uid="u-missing",
        ))
        assert isinstance(r, BridgeSelectLayerResult)
        assert r.status == "NOT_FOUND"
        assert r.uid == "u-missing"

    def test_select_layer_result_resolved_via_uid(self):
        """Bug F C: OK payload echoes resolved_via='uid' when the
        primary findLayerByUID lookup succeeded."""
        r = parse_result(_result_dict(
            "select-layer", status="OK", uid="u-42",
            layer_index=17, layer_name="hero_layer",
            resolved_via="uid",
        ))
        assert isinstance(r, BridgeSelectLayerResult)
        assert r.resolved_via == "uid"

    def test_select_layer_result_resolved_via_index_fallback(self):
        """Bug F C: OK payload echoes resolved_via='index' when
        findLayerByUID missed and the new index+name fallback
        resolved the layer. The same field is the only signal
        Python has that the fallback was needed — useful for
        diagnostics + the eventual Bug F B follow-up that stamps
        UIDs during scrape."""
        r = parse_result(_result_dict(
            "select-layer", status="OK", uid="u-fresh",
            layer_index=3, layer_name="MyLayer",
            resolved_via="index",
        ))
        assert isinstance(r, BridgeSelectLayerResult)
        assert r.resolved_via == "index"

    def test_select_layer_result_resolved_via_unknown_rejected(self):
        """Only 'uid' and 'index' are valid resolved_via values."""
        with pytest.raises(ValidationError):
            parse_result(_result_dict(
                "select-layer", status="OK", uid="u-1",
                resolved_via="magic",
            ))

    def test_query_layer_state_result_with_ok_fields(self):
        """OK path: JSX emits uid + layer_index + layer_name +
        resolved_via + values + kinds + unresolved_paths, mirroring
        BridgeSelectLayerResult's OK shape plus the query-specific
        fields."""
        r = parse_result(_result_dict(
            "query-layer-state", status="OK", uid="u-42",
            layer_index=17, layer_name="hero_layer",
            resolved_via="uid", time_s=None,
            values={"position": [960, 540], "scale": [100, 100]},
            kinds={"position": "vec2", "scale": "vec2"},
            unresolved_paths=[],
        ))
        assert isinstance(r, BridgeQueryLayerStateResult)
        assert r.status == "OK"
        assert r.uid == "u-42"
        assert r.layer_index == 17
        assert r.layer_name == "hero_layer"
        assert r.resolved_via == "uid"
        assert r.values == {"position": [960, 540], "scale": [100, 100]}
        assert r.kinds == {"position": "vec2", "scale": "vec2"}
        assert r.unresolved_paths == []

    def test_query_layer_state_result_with_partial_success(self):
        """Best-effort partial success: some requested paths didn't
        resolve on this layer type (e.g. 'zoom' on a non-camera
        layer) but at least one did, so status stays OK."""
        r = parse_result(_result_dict(
            "query-layer-state", status="OK", uid="u-42",
            layer_index=3, layer_name="MyLayer",
            resolved_via="index",
            values={"position": [10, 20]},
            kinds={"position": "vec2"},
            unresolved_paths=["zoom"],
        ))
        assert isinstance(r, BridgeQueryLayerStateResult)
        assert r.status == "OK"
        assert r.values == {"position": [10, 20]}
        assert r.unresolved_paths == ["zoom"]

    def test_query_layer_state_result_time_s_echoed_none_by_default(self):
        """Default (rest-pose) read path echoes time_s=None."""
        r = parse_result(_result_dict(
            "query-layer-state", status="OK", uid="u-1",
            values={"position": [0, 0]}, kinds={"position": "vec2"},
            unresolved_paths=[],
        ))
        assert isinstance(r, BridgeQueryLayerStateResult)
        assert r.time_s is None

    def test_query_layer_state_result_time_s_echoed_as_float(self):
        """Explicit-time read path echoes the requested time_s back."""
        r = parse_result(_result_dict(
            "query-layer-state", status="OK", uid="u-1", time_s=2.5,
            values={"position": [0, 0]}, kinds={"position": "vec2"},
            unresolved_paths=[],
        ))
        assert isinstance(r, BridgeQueryLayerStateResult)
        assert r.time_s == 2.5

    def test_query_layer_state_result_not_found(self):
        """Layer could not be resolved by uid or index+name fallback."""
        r = parse_result(_result_dict(
            "query-layer-state", status="NOT_FOUND", uid="u-missing",
        ))
        assert isinstance(r, BridgeQueryLayerStateResult)
        assert r.status == "NOT_FOUND"
        assert r.uid == "u-missing"

    def test_duplicate_plan_result_errors_is_count_not_list(self):
        """JSX writes `errors` as an int count, not a list. The
        per-item error list lives in `log.errors[]`. Initial schema
        sketch had this inverted; commit 4 corrected it. Pinning
        the corrected shape so a future schema edit can't silently
        revert."""
        r = parse_result(_result_dict(
            "duplicate-plan", duplicates_made=2, rewires_made=5,
            skipped=1, errors=0,
            log={"session_id": "sess-1",
                 "duplicates_made": [], "rewires_made": [],
                 "skipped": [], "errors": []},
        ))
        assert isinstance(r, BridgeDuplicatePlanResult)
        assert r.errors == 0
        assert isinstance(r.log, dict)

    def test_duplicate_plan_result_log_is_dict_not_str(self):
        """Same correction as above — log on the wire is the inline
        dict (mirroring duplication_log.json), not a path string."""
        with pytest.raises(ValidationError):
            parse_result(_result_dict(
                "duplicate-plan",
                log="/tmp/dup.json",  # str rejected
            ))

    def test_panel_slicing_plan_result(self):
        r = parse_result(_result_dict(
            "panel-slicing-plan", panels_made=3, gap_guides_made=2,
            errors=0,
            log={"session_id": "sess-1",
                 "panels_made": [], "gap_guides_made": [],
                 "errors": []},
        ))
        assert isinstance(r, BridgePanelSlicingPlanResult)
        assert r.panels_made == 3
        assert r.gap_guides_made == 2
        assert r.errors == 0

    def test_mask_toggle_result(self):
        r = parse_result(_result_dict(
            "mask-toggle", action="import", mask_path="/tmp/m.png",
        ))
        assert isinstance(r, BridgeMaskToggleResult)

    def test_color_match_render_result(self):
        r = parse_result(_result_dict(
            "color-match-render",
            path="/tmp/color_match_42_reference.png",
            frame_time_s=2.5025025,
            snapped_frame=60,
            bpc=8,
            is_ocio=False,
        ))
        assert isinstance(r, BridgeColorMatchRenderResult)
        assert r.path == "/tmp/color_match_42_reference.png"
        assert r.snapped_frame == 60
        assert r.bpc == 8
        assert r.is_ocio is False

    def test_color_match_render_result_error_status(self):
        r = parse_result(_result_dict(
            "color-match-render", status="ERROR",
            error="Composition not found: id=42 name=None",
        ))
        assert isinstance(r, BridgeColorMatchRenderResult)
        assert r.status == "ERROR"
        assert r.path is None

    def test_unknown_job_type_rejected(self):
        with pytest.raises(ValidationError):
            parse_result(_result_dict("not-a-job-type"))


# ── ReconPlanJob dispatch ────────────────────────────────────────


class TestReconPlanJob:
    """ReconPlanJob — Stage 4 bridge contract tests."""

    def test_dispatches_to_recon_plan_job(self):
        j = parse_typed_job(_recon_plan_dict())
        assert isinstance(j, ReconPlanJob)
        assert j.type == "recon-plan"

    def test_plan_field_is_dict(self):
        j = parse_typed_job(_recon_plan_dict())
        assert isinstance(j.plan, dict)
        assert "new_comps" in j.plan

    def test_log_path_optional(self):
        j = parse_typed_job(_recon_plan_dict())
        assert j.log_path is None

    def test_log_path_accepted_when_set(self):
        j = parse_typed_job(_recon_plan_dict(log_path="/tmp/recon_result.json"))
        assert j.log_path == "/tmp/recon_result.json"

    def test_missing_plan_rejected(self):
        d = _recon_plan_dict()
        del d["plan"]
        with pytest.raises(ValidationError):
            parse_typed_job(d)

    def test_missing_babysitter_rejected(self):
        d = _recon_plan_dict()
        del d["babysitter"]
        with pytest.raises(ValidationError):
            parse_typed_job(d)

    def test_snake_case_type_rejected(self):
        with pytest.raises(ValidationError):
            parse_typed_job(_recon_plan_dict(type="recon_plan"))

    def test_recon_plan_result_ok(self):
        r = parse_result(_result_dict(
            "recon-plan",
            comps_created=[{"name": "Recon_9x16", "ae_id": 42, "width": 1080, "height": 1920, "duration": 30.0, "fps": 24.0}],
            errors=[],
        ))
        assert isinstance(r, BridgeReconPlanResult)
        assert r.status == "OK"
        assert len(r.comps_created) == 1
        assert r.comps_created[0]["name"] == "Recon_9x16"
        assert r.errors == []

    def test_recon_plan_result_error(self):
        r = parse_result(_result_dict(
            "recon-plan",
            status="ERROR",
            error="No AE project open",
            comps_created=[],
            errors=[{"phase": "fatal", "detail": "No AE project open"}],
        ))
        assert isinstance(r, BridgeReconPlanResult)
        assert r.status == "ERROR"
        assert r.error == "No AE project open"
        assert r.comps_created == []

    def test_recon_plan_result_errors_is_list_not_int(self):
        """errors is a list of dicts (unlike BridgeDuplicatePlanResult
        where errors is an int count). Pins the correct wire shape."""
        with pytest.raises(ValidationError):
            parse_result(_result_dict(
                "recon-plan",
                comps_created=[],
                errors=0,  # int rejected — must be list
            ))


# ── Status enum ──────────────────────────────────────────────────


class TestResultStatusEnum:
    """The status enum currently accepts five values: OK, ERROR,
    POLLER_STALE, TIMEOUT, NOT_FOUND. NOT_FOUND was added 2026-04-29
    (PR-E.2 Bug A) to align the schema with what JSX has always
    emitted from `_handleSelectLayerJob` when findLayerByUID
    misses; this is a wire-vs-schema correction (same class as
    PR-B commit 4) so BRIDGE_SCHEMA_VERSION did not bump."""

    @pytest.mark.parametrize(
        "status",
        ["OK", "ERROR", "POLLER_STALE", "TIMEOUT", "NOT_FOUND"],
    )
    def test_known_status_accepted(self, status):
        r = parse_result(_result_dict("scrape", status=status))
        assert r.status == status

    def test_lowercase_status_rejected(self):
        """Wire format is uppercase. Don't accept lowercase 'ok' /
        'error' even if pythonic — matches what JSX writes."""
        with pytest.raises(ValidationError):
            parse_result(_result_dict("scrape", status="ok"))

    def test_unknown_status_rejected(self):
        with pytest.raises(ValidationError):
            parse_result(_result_dict("scrape", status="WAITING"))


# ── parse_typed_job vs parse_inject_job ──────────────────────────


class TestParseHelperRouting:
    """parse_typed_job handles the discriminator-tagged jobs;
    parse_inject_job handles the legacy untyped inject job. The
    helpers route differently because inject has no `type` field
    on the wire and Pydantic's discriminator union wouldn't match."""

    def test_parse_typed_job_rejects_inject_dict(self):
        """An inject dict has no `type` field — typed parse must
        reject it (otherwise we'd silently mis-dispatch)."""
        with pytest.raises(ValidationError):
            parse_typed_job(_inject_dict())

    def test_parse_inject_job_accepts_inject_dict(self):
        j = parse_inject_job(_inject_dict())
        assert isinstance(j, InjectJob)
        assert j.manifest == "/tmp/m.json"

    def test_parse_inject_job_rejects_typed_dict(self):
        """A typed scrape dict has unexpected `type` field — inject
        parse must reject it (extra="forbid" on InjectJob)."""
        with pytest.raises(ValidationError):
            parse_inject_job(_scrape_dict())


# ── BridgeReconPlanResult schema — Task 5 additions ─────────────


class TestBridgeReconPlanResultSchema:
    """Task 5 — layers_populated and population_errors are Optional
    fields added to BridgeReconPlanResult. Backwards-compatible:
    existing callers that parse a result without these fields receive
    None for both. New fields present parse correctly.

    These are Layer 1 (Python-only) tests for schema internal
    consistency per the module docstring's three-layer test taxonomy.
    """

    def test_layers_populated_field_optional(self):
        """Parsing a result dict without layers_populated succeeds;
        the field defaults to None."""
        r = parse_result(_result_dict(
            "recon-plan",
            comps_created=[{"name": "R", "ae_id": 1, "width": 1080,
                            "height": 1920, "duration": 30.0, "fps": 24.0}],
            errors=[],
        ))
        assert isinstance(r, BridgeReconPlanResult)
        assert r.layers_populated is None

    def test_population_errors_field_optional(self):
        """Parsing a result dict without population_errors succeeds;
        the field defaults to None."""
        r = parse_result(_result_dict(
            "recon-plan",
            comps_created=[],
            errors=[],
        ))
        assert isinstance(r, BridgeReconPlanResult)
        assert r.population_errors is None

    def test_layers_populated_int(self):
        """When layers_populated is present as an int it parses correctly."""
        r = parse_result(_result_dict(
            "recon-plan",
            comps_created=[{"name": "R", "ae_id": 1, "width": 1080,
                            "height": 1920, "duration": 30.0, "fps": 24.0,
                            "layers_populated": 12, "population_errors": []}],
            errors=[],
            layers_populated=12,
            population_errors=[],
        ))
        assert isinstance(r, BridgeReconPlanResult)
        assert r.layers_populated == 12
        assert r.population_errors == []
