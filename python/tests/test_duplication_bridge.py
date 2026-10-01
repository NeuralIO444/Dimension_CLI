# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_duplication_bridge.py
v5.8 — coverage for SovereignBridge.apply_duplication_plan wiring.

We don't drive AE here. Instead we mock the file-bridge handshake:
  - patch `_poller_is_alive` to report alive
  - background thread polls the inbox for the dispatched job descriptor,
    reads the `result` path it carries, and writes a synthetic JSX-side
    result file
  - assert that the bridge serialised the plan correctly + returned the
    JSX-side payload to the caller
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


def _make_plan(*, with_dup: bool = True):
    """Construct a minimal DuplicationPlan we can dump to JSON."""
    from models.duplication_plan import (
        DuplicationPlan,
        LayerRewire,
        PrecompDuplicate,
    )
    duplicates = []
    rewires = []
    if with_dup:
        duplicates.append(PrecompDuplicate(
            original_uid="100",
            original_name="Hero",
            duplicate_name="Hero_1080x1920",
            target_folder_path="From Dimensions/TIKTOK_2026",
            original_width=1920,
            original_height=1080,
            reason="SHARED",
            is_protected=False,
            will_be_skipped=False,
        ))
        rewires.append(LayerRewire(
            conformed_layer_uid="L1",
            conformed_layer_name="hero_layer",
            original_source_uid="100",
            new_source_uid="dup:100",
        ))
    return DuplicationPlan(
        session_id="sess-bridge",
        session_folder="From Dimensions/TIKTOK_2026",
        preset_id="TIKTOK",
        target_dimensions=(1080, 1920),
        aspect_ratio_changed=True,
        duplicates=duplicates,
        rewires=rewires,
        depth_max=0,
    )


def _spawn_jsx_responder(inbox: Path, response: dict, *,
                         capture_jobs: list,
                         delay_s: float = 0.0):
    """Background thread emulating the JSX side. Watches the inbox for
    a duplicate-plan job descriptor, captures it, then writes the
    response to the result path the descriptor carries."""

    stop = threading.Event()

    def run():
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not stop.is_set():
            for name in os.listdir(str(inbox)):
                # Skip in-flight tmp files and result placeholders.
                if not name.startswith("job_") or not name.endswith(".json"):
                    continue
                if name.endswith(".result.json") or name.endswith(".tmp"):
                    continue
                full = inbox / name
                try:
                    desc = json.loads(full.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if desc.get("type") != "duplicate-plan":
                    continue

                capture_jobs.append(desc)
                if delay_s:
                    time.sleep(delay_s)
                # Atomic-write the result via tmp + rename, mirroring
                # what the real JSX does.
                result_path = Path(desc["result"])
                tmp = result_path.with_suffix(result_path.suffix + ".tmp")
                tmp.write_text(json.dumps(response), encoding="utf-8")
                os.replace(tmp, result_path)
                return
            time.sleep(0.02)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, stop


# ── Bridge construction ──────────────────────────────────────────────


@pytest.fixture()
def bridge_factory(tmp_path, monkeypatch):
    """Yield a builder that returns a SovereignBridge wired against
    `tmp_path` with poller-alive patched true."""
    from bridge.sovereign_bridge import SovereignBridge

    def _make():
        b = SovereignBridge(project_root=str(tmp_path))
        monkeypatch.setattr(b, "_socket_is_listening", lambda: False)
        os.makedirs(b._inbox_dir(), exist_ok=True)
        Path(b._heartbeat_path()).write_text("tick_count=0\n", encoding="utf-8")
        return b

    return _make


# ── apply_duplication_plan ───────────────────────────────────────────


class TestApplyDuplicationPlan:
    def test_dispatches_plan_with_correct_shape(self, tmp_path, bridge_factory):
        bridge = bridge_factory()
        plan = _make_plan()

        captured = []
        response = {
            # PR-B contract: every result needs schema_version + job_type
            "schema_version":  "1.0",
            "job_type":        "duplicate-plan",
            "status":          "OK",
            "duplicates_made": 1,
            "rewires_made":    1,
            "skipped":         0,
            "errors":          0,
            "log":             {"session_id": "sess-bridge",
                                 "duplicates_made": [
                                     {"original_uid": "100",
                                      "duplicate_uid": "200",
                                      "duplicate_name": "Hero_1080x1920"},
                                 ],
                                 "rewires_made": [],
                                 "skipped": [],
                                 "errors": []},
        }
        responder, stop = _spawn_jsx_responder(
            Path(bridge._inbox_dir()), response, capture_jobs=captured,
        )
        try:
            log_path = tmp_path / ".dimension" / "duplication_log.json"
            payload = bridge.apply_duplication_plan(
                plan,
                conformed_comp_id=42,
                log_path=str(log_path),
                timeout_s=4.0,
            )
        finally:
            stop.set(); responder.join(timeout=2)

        # Bridge returned the JSX-side payload verbatim.
        assert payload["status"] == "OK"
        assert payload["duplicates_made"] == 1

        # Job descriptor on disk had the correct shape.
        assert len(captured) == 1
        desc = captured[0]
        assert desc["type"] == "duplicate-plan"
        assert desc["conformed_comp_id"] == 42
        assert desc["log_path"] == os.path.abspath(str(log_path))
        assert desc["plan"]["session_id"] == "sess-bridge"
        # tuple → list serialisation (mode='json').
        assert desc["plan"]["target_dimensions"] == [1080, 1920]
        assert desc["plan"]["duplicates"][0]["original_uid"] == "100"
        # Babysitter path resolves under the project root's assets dir.
        assert desc["babysitter"].endswith("Babysitter.jsx")
        assert "Scripts/Dimension_Assets" in desc["babysitter"]

    def test_creates_log_path_parent_dir(self, tmp_path, bridge_factory):
        """The bridge must create the parent dir for log_path before
        dispatching — Babysitter expects it to exist."""
        bridge = bridge_factory()
        plan = _make_plan()

        responder, stop = _spawn_jsx_responder(
            Path(bridge._inbox_dir()),
            {"schema_version": "1.0", "job_type": "duplicate-plan",
             "status": "OK", "duplicates_made": 0,
             "rewires_made": 0, "skipped": 0, "errors": 0,
             "log": {"session_id": "sess-bridge",
                      "duplicates_made": [], "rewires_made": [],
                      "skipped": [], "errors": []}},
            capture_jobs=[],
        )
        try:
            deep = tmp_path / "a" / "b" / "c" / "duplication_log.json"
            assert not deep.parent.exists()
            bridge.apply_duplication_plan(
                plan,
                conformed_comp_id=10,
                log_path=str(deep),
                timeout_s=4.0,
            )
            assert deep.parent.is_dir()
        finally:
            stop.set(); responder.join(timeout=2)

    def test_rejects_missing_plan(self, bridge_factory):
        bridge = bridge_factory()
        with pytest.raises(ValueError, match="plan is required"):
            bridge.apply_duplication_plan(
                None, conformed_comp_id=1, log_path="/tmp/x.json",
            )

    def test_rejects_missing_log_path(self, bridge_factory):
        bridge = bridge_factory()
        with pytest.raises(ValueError, match="log_path"):
            bridge.apply_duplication_plan(
                _make_plan(), conformed_comp_id=1, log_path="",
            )

    def test_rejects_when_neither_id_nor_name_supplied(self, bridge_factory):
        bridge = bridge_factory()
        with pytest.raises(ValueError, match="conformed_comp"):
            bridge.apply_duplication_plan(
                _make_plan(),
                log_path="/tmp/x.json",
            )

    def test_rejects_when_both_id_and_name_supplied(self, bridge_factory):
        bridge = bridge_factory()
        with pytest.raises(ValueError, match="not both"):
            bridge.apply_duplication_plan(
                _make_plan(),
                conformed_comp_id=1,
                conformed_comp_name="[DIMENSION] TIKTOK",
                log_path="/tmp/x.json",
            )

    def test_dispatches_plan_with_conformed_comp_name(self, tmp_path, bridge_factory):
        """v5.8 slice 4 path — Python knows the predictable name but
        not the AE-assigned id. Bridge dispatches `conformed_comp_name`
        instead of `_id`; JSX resolves at execute time."""
        bridge = bridge_factory()
        captured = []
        responder, stop = _spawn_jsx_responder(
            Path(bridge._inbox_dir()),
            {"schema_version": "1.0", "job_type": "duplicate-plan",
             "status": "OK", "duplicates_made": 0, "rewires_made": 0,
             "skipped": 0, "errors": 0,
             "log": {"session_id": "sess-bridge",
                      "duplicates_made": [], "rewires_made": [],
                      "skipped": [], "errors": []}},
            capture_jobs=captured,
        )
        try:
            bridge.apply_duplication_plan(
                _make_plan(),
                conformed_comp_name="[DIMENSION] TIKTOK_VERT",
                log_path=str(tmp_path / "log.json"),
                timeout_s=4.0,
            )
        finally:
            stop.set(); responder.join(timeout=2)
        assert len(captured) == 1
        assert captured[0]["conformed_comp_name"] == "[DIMENSION] TIKTOK_VERT"
        assert "conformed_comp_id" not in captured[0]

    def test_raises_when_poller_is_dead(self, tmp_path, monkeypatch):
        from bridge.sovereign_bridge import SovereignBridge
        bridge = SovereignBridge(project_root=str(tmp_path))
        monkeypatch.setattr(
            bridge, "_poller_is_alive",
            lambda: (False, "no heartbeat"),
        )
        with pytest.raises(RuntimeError, match="AE engine not responding"):
            bridge.apply_duplication_plan(
                _make_plan(),
                conformed_comp_id=1,
                log_path=str(tmp_path / "log.json"),
            )

    def test_accepts_pre_dumped_dict(self, tmp_path, bridge_factory):
        """A plan that's already a dict (e.g. from dry-run preview)
        also dispatches cleanly."""
        bridge = bridge_factory()
        plan_dict = _make_plan().model_dump(mode="json")
        captured: list = []
        responder, stop = _spawn_jsx_responder(
            Path(bridge._inbox_dir()),
            {"schema_version": "1.0", "job_type": "duplicate-plan",
             "status": "OK", "duplicates_made": 0, "rewires_made": 0,
             "skipped": 0, "errors": 0,
             "log": {"session_id": "sess-bridge",
                      "duplicates_made": [], "rewires_made": [],
                      "skipped": [], "errors": []}},
            capture_jobs=captured,
        )
        try:
            bridge.apply_duplication_plan(
                plan_dict,
                conformed_comp_id=7,
                log_path=str(tmp_path / "log.json"),
                timeout_s=4.0,
            )
        finally:
            stop.set(); responder.join(timeout=2)
        assert len(captured) == 1
        assert captured[0]["plan"]["session_id"] == "sess-bridge"
