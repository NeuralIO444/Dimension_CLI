# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_color_match_bridge.py
Color Match (Track D, CM2) — coverage for ColorMatchBridge.render_reference_frame.

Same approach as test_duplication_bridge.py: we don't drive AE. We force
`_socket_is_listening` False so `_execute_bridge_job` falls through to
the file-bridge transport, seed a fresh heartbeat, then run a background
thread that watches `.dimension_inbox/` for the dispatched job
descriptor and writes a synthetic JSX-side result to the `result` path
it carries — the same tmp+rename handshake the real JSX side uses.

This is NOT a substitute for a Layer 2 (real-JSX-fixture) test — see
CLAUDE.md's synthetic-fixture anti-pattern note. No live-AE fixture for
`color-match-render` exists yet; capturing one is flagged as required
manual QA before this job type should be trusted end-to-end.
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


def _spawn_jsx_responder(inbox: Path, response: dict, *,
                         capture_jobs: list,
                         delay_s: float = 0.0):
    """Background thread emulating the JSX side for a color-match-render
    job. Watches the inbox, captures the descriptor, writes the response
    to the result path it carries."""

    stop = threading.Event()

    def run():
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not stop.is_set():
            for name in os.listdir(str(inbox)):
                if not name.startswith("job_") or not name.endswith(".json"):
                    continue
                if name.endswith(".result.json") or name.endswith(".tmp"):
                    continue
                full = inbox / name
                try:
                    desc = json.loads(full.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if desc.get("type") not in ("color-match-render", "color-match-inject"):
                    continue

                capture_jobs.append(desc)
                if delay_s:
                    time.sleep(delay_s)
                result_path = Path(desc["result"])
                tmp = result_path.with_suffix(result_path.suffix + ".tmp")
                tmp.write_text(json.dumps(response), encoding="utf-8")
                os.replace(tmp, result_path)
                return
            time.sleep(0.02)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, stop


@pytest.fixture()
def bridge_factory(tmp_path, monkeypatch):
    """Yield a builder that returns a ColorMatchBridge wired against
    `tmp_path` with the socket transport forced off (file-bridge only)
    and poller-alive patched true."""
    from bridge.color_match_bridge import ColorMatchBridge

    def _make():
        cm = ColorMatchBridge(project_root=str(tmp_path))
        monkeypatch.setattr(cm._bridge, "_socket_is_listening", lambda: False)
        os.makedirs(cm._bridge._inbox_dir(), exist_ok=True)
        Path(cm._bridge._heartbeat_path()).write_text(
            "tick_count=0\n", encoding="utf-8"
        )
        return cm

    return _make


OK_RESPONSE = {
    "schema_version": "1.0",
    "job_type": "color-match-render",
    "status": "OK",
    "path": "/tmp/color_match_42_reference.png",
    "frame_time_s": 2.5025025,
    "snapped_frame": 60,
    "bpc": 8,
    "is_ocio": False,
}


class TestRenderReferenceFrame:
    def test_dispatches_job_with_correct_shape(self, tmp_path, bridge_factory):
        cm = bridge_factory()
        captured = []
        responder, stop = _spawn_jsx_responder(
            Path(cm._bridge._inbox_dir()), OK_RESPONSE, capture_jobs=captured,
        )
        try:
            payload = cm.render_reference_frame(
                "42", "HERO_PRECOMP", tmp_path / "ref.png", timeout_s=4.0,
            )
        finally:
            stop.set(); responder.join(timeout=2)

        assert payload["status"] == "OK"
        assert payload["snapped_frame"] == 60

        assert len(captured) == 1
        desc = captured[0]
        assert desc["type"] == "color-match-render"
        assert desc["comp_id"] == "42"
        assert desc["comp_name"] == "HERO_PRECOMP"
        assert desc["output_path"] == str(tmp_path / "ref.png")
        assert "project_path" not in desc

    def test_includes_project_path_when_given(self, tmp_path, bridge_factory):
        cm = bridge_factory()
        captured = []
        responder, stop = _spawn_jsx_responder(
            Path(cm._bridge._inbox_dir()), OK_RESPONSE, capture_jobs=captured,
        )
        try:
            cm.render_reference_frame(
                "42", "HERO_PRECOMP", tmp_path / "ref.png",
                project_path="/Users/matt/Project.aep", timeout_s=4.0,
            )
        finally:
            stop.set(); responder.join(timeout=2)

        assert captured[0]["project_path"] == "/Users/matt/Project.aep"

    def test_raises_color_match_error_on_ae_error_status(self, tmp_path, bridge_factory):
        from bridge.color_match_bridge import ColorMatchError

        cm = bridge_factory()
        error_response = {
            "schema_version": "1.0",
            "job_type": "color-match-render",
            "status": "ERROR",
            "error": "Composition not found: id=42 name=HERO_PRECOMP",
        }
        responder, stop = _spawn_jsx_responder(
            Path(cm._bridge._inbox_dir()), error_response, capture_jobs=[],
        )
        try:
            with pytest.raises(ColorMatchError, match="Composition not found"):
                cm.render_reference_frame(
                    "42", "HERO_PRECOMP", tmp_path / "ref.png", timeout_s=4.0,
                )
        finally:
            stop.set(); responder.join(timeout=2)

    def test_raises_when_poller_is_dead(self, tmp_path, monkeypatch):
        from bridge.color_match_bridge import ColorMatchBridge, ColorMatchError

        cm = ColorMatchBridge(project_root=str(tmp_path))
        monkeypatch.setattr(cm._bridge, "_socket_is_listening", lambda: False)
        monkeypatch.setattr(
            cm._bridge, "_heartbeat_status", lambda: (False, "no heartbeat file", None)
        )
        with pytest.raises(ColorMatchError):
            cm.render_reference_frame(
                "42", "HERO_PRECOMP", tmp_path / "ref.png", timeout_s=1.0,
            )

    def test_rejects_missing_comp_id(self, bridge_factory, tmp_path):
        cm = bridge_factory()
        with pytest.raises(ValueError, match="comp_id is required"):
            cm.render_reference_frame("", "HERO_PRECOMP", tmp_path / "ref.png")

    def test_rejects_missing_comp_name(self, bridge_factory, tmp_path):
        cm = bridge_factory()
        with pytest.raises(ValueError, match="comp_name is required"):
            cm.render_reference_frame("42", "", tmp_path / "ref.png")

    def test_rejects_missing_output_path(self, bridge_factory):
        cm = bridge_factory()
        with pytest.raises(ValueError, match="output_path is required"):
            cm.render_reference_frame("42", "HERO_PRECOMP", "")


class TestExecuteBridgeJobFacade:
    """SovereignBridge.execute_bridge_job is a thin public forward to
    the private transport dispatcher — Color Match's only sanctioned
    way to reach it from outside the class."""

    def test_forwards_to_private_dispatcher(self, tmp_path, monkeypatch):
        from bridge.sovereign_bridge import SovereignBridge

        b = SovereignBridge(project_root=str(tmp_path))
        seen = {}

        def fake_execute(job, timeout_s):
            seen["job"] = job
            seen["timeout_s"] = timeout_s
            return {"status": "OK"}

        monkeypatch.setattr(b, "_execute_bridge_job", fake_execute)
        result = b.execute_bridge_job({"type": "color-match-render"}, 5.0)
        assert result == {"status": "OK"}
        assert seen["job"] == {"type": "color-match-render"}
        assert seen["timeout_s"] == 5.0


INJECT_OK_RESPONSE = {
    "schema_version": "1.0",
    "job_type": "color-match-inject",
    "status": "OK",
    "adjustment_layer_index": 2,
    "adjustment_layer_name": "Dimension Color Match",
    "effect_match_name": "ADBE Apply Color LUT 2",
    "reused_existing_layer": False,
}


class TestInjectLut:
    def test_dispatches_job_with_correct_shape(self, tmp_path, bridge_factory):
        cm = bridge_factory()
        captured = []
        responder, stop = _spawn_jsx_responder(
            Path(cm._bridge._inbox_dir()), INJECT_OK_RESPONSE, capture_jobs=captured,
        )
        lut_file = tmp_path / "grade.cube"
        lut_file.write_text("LUT_3D_SIZE 2\n0 0 0\n", encoding="utf-8")

        try:
            payload = cm.inject_lut(
                "100", "42", lut_file, target_layer_uid="uid-wrap-1", timeout_s=4.0,
            )
        finally:
            stop.set()
            responder.join(timeout=2)

        assert payload["status"] == "OK"
        assert payload["adjustment_layer_index"] == 2
        assert payload["adjustment_layer_name"] == "Dimension Color Match"

        assert len(captured) == 1
        desc = captured[0]
        assert desc["type"] == "color-match-inject"
        assert desc["parent_comp_id"] == "100"
        assert desc["precomp_comp_id"] == "42"
        assert desc["target_layer_uid"] == "uid-wrap-1"
        assert desc["lut_path"] == str(lut_file)

    def test_rejects_missing_parent_comp_id(self, bridge_factory, tmp_path):
        cm = bridge_factory()
        with pytest.raises(ValueError, match="parent_comp_id is required"):
            cm.inject_lut("", "42", tmp_path / "g.cube")

    def test_rejects_missing_precomp_comp_id(self, bridge_factory, tmp_path):
        cm = bridge_factory()
        with pytest.raises(ValueError, match="precomp_comp_id is required"):
            cm.inject_lut("100", "", tmp_path / "g.cube")

    def test_rejects_missing_lut_path(self, bridge_factory):
        cm = bridge_factory()
        with pytest.raises(ValueError, match="lut_path is required"):
            cm.inject_lut("100", "42", "")

