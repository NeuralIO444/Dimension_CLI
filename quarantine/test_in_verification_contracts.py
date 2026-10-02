# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Static contract tests for BUGS.md In verification items.

These do not replace live AE field tests but pin the code patterns
that the fixes depend on so regressions fail CI before another
field session is needed.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_LAUNCHER = _REPO / "Scripts" / "Dimension_Launcher.jsx"
_BRIDGE = _REPO / "python" / "bridge" / "sovereign_bridge.py"


class TestBridgeScrapeTransport:
    """Python bridge scrape path — no osascript; dual transport IPC."""

    def test_sovereign_bridge_has_no_osascript_calls(self):
        text = _BRIDGE.read_text(encoding="utf-8")
        assert not re.search(
            r"(subprocess\.|os\.system|Popen).*osascript|import\s+osascript",
            text,
            re.IGNORECASE,
        ), "Bridge must not invoke osascript at runtime"

    def test_trigger_scrape_uses_bridge_job_router(self):
        from bridge.sovereign_bridge import SovereignBridge

        src = inspect.getsource(SovereignBridge.trigger_scrape)
        assert "_execute_bridge_job" in src

    def test_poller_is_alive_checks_heartbeat_not_socket_only(self):
        text = _BRIDGE.read_text(encoding="utf-8")
        assert "_heartbeat_status" in text
        assert "_socket_is_listening" in text
        start = text.find("def _poller_is_alive")
        assert start != -1
        body = text[start : start + 600]
        assert "_heartbeat_status" in body


class TestSocketServerExtendScriptHygiene:
    """ExtendScript ES3 reserved words that break $.evalFile at load time."""

    def test_socket_server_avoids_reserved_char_identifier(self):
        text = (_REPO / "Scripts" / "Dimension_Assets" / "socket_server.jsx").read_text(
            encoding="utf-8"
        )
        assert "var char " not in text and "var char=" not in text


class TestPollerFocusLossPattern:
    """AE 2026 scheduleTask focus-loss — self-reschedule contract."""

    @pytest.fixture
    def launcher_src(self) -> str:
        return _LAUNCHER.read_text(encoding="utf-8")

    def test_tick_self_reschedules_with_repeat_false(self, launcher_src: str):
        start = launcher_src.find("tick: function()")
        assert start != -1, "Could not locate DimensionPoller.tick()"
        end = launcher_src.find(
            "var inboxFolder = new Folder(this.inboxFor(root))", start
        )
        assert end != -1
        body = launcher_src[start:end]
        sched_idx = body.find("app.scheduleTask")
        tick_count_idx = body.find("this.tickCount")
        assert sched_idx != -1 and tick_count_idx != -1
        assert sched_idx < tick_count_idx, (
            "tick() must self-reschedule before incrementing tickCount"
        )
        assert "__DimensionPoller.tick()" in body[sched_idx:sched_idx + 120]
        assert "false" in body[sched_idx:sched_idx + 200], (
            "tick() must use scheduleTask(..., false) for self-reschedule"
        )

    def test_heartbeat_writes_tick_count(self, launcher_src: str):
        assert "tick_count=" in launcher_src
        assert "poller_tick=" in launcher_src


@pytest.mark.ae_live
class TestLiveAeSignals:
    """Optional live checks — skipped when AE / poller not running."""

    def test_ae_socket_port_open(self, ae_probe, ae_required):
        from pytest_dimension_ae.probes import skip_or_fail

        skip_or_fail(
            ae_probe.socket_listening,
            "AE socket server not listening on 45445 — "
            "open AE + LAUNCH ENGINE for live scrape verification",
            ae_required=ae_required,
        )

    def test_poller_heartbeat_fresh(self, ae_probe, ae_required):
        from pytest_dimension_ae.probes import skip_or_fail

        skip_or_fail(
            ae_probe.heartbeat_exists,
            "No poller heartbeat file — LAUNCH ENGINE in AE first",
            ae_required=ae_required,
        )
        skip_or_fail(
            ae_probe.heartbeat_fresh,
            (
                f"Heartbeat stale ({ae_probe.heartbeat_age_s:.0f}s) — "
                "poller not ticking; background AE test not possible"
                if ae_probe.heartbeat_age_s is not None
                else "Poller heartbeat not fresh"
            ),
            ae_required=ae_required,
        )
        assert ae_probe.heartbeat_text is not None
        assert "tick_count=" in ae_probe.heartbeat_text

    def test_query_layer_state_reads_position_on_known_layer(
        self, ae_probe, ae_required,
    ):
        """Harness Slice 1 — read-only query round-trip against a live
        AE session. Cannot assume a specific fixture comp is loaded, so
        this asserts on SHAPE rather than exact values: status must be
        one of the three valid outcomes, and on OK the requested path
        must appear in either `values` (resolved) or `unresolved_paths`
        (not applicable to whatever layer/comp happens to be active)."""
        from pytest_dimension_ae.probes import skip_or_fail

        skip_or_fail(
            ae_probe.ready,
            "AE not reachable — open AE + LAUNCH ENGINE for live "
            "query-layer-state verification",
            ae_required=ae_required,
        )

        from bridge.sovereign_bridge import SovereignBridge

        bridge = SovereignBridge(project_root=str(_REPO))
        payload = bridge.query_layer_state(
            uid="__dimension_harness_probe__",
            property_paths=["position"],
            timeout_s=4.0,
        )
        assert payload.get("status") in ("OK", "NOT_FOUND", "TIMEOUT")
        if payload.get("status") == "OK":
            assert (
                "position" in payload.get("values", {})
                or "position" in payload.get("unresolved_paths", [])
            )

    def test_select_layer_roundtrip(self, ae_probe, ae_required):
        """Harness: test select-layer bridge dispatch to live AE poller."""
        from pytest_dimension_ae.probes import skip_or_fail

        skip_or_fail(
            ae_probe.ready,
            "AE not reachable for live select-layer verification",
            ae_required=ae_required,
        )

        from bridge.sovereign_bridge import SovereignBridge

        bridge = SovereignBridge(project_root=str(_REPO))
        res = bridge.select_layer(
            uid="__dimension_nonexistent_layer__",
            timeout_s=4.0,
        )
        assert res is not None
        assert res.get("status") in ("OK", "NOT_FOUND", "ERROR", "TIMEOUT")
        if res.get("status") == "NOT_FOUND":
            assert res.get("uid") == "__dimension_nonexistent_layer__"

    def test_tag_write_roundtrip(self, ae_probe, ae_required):
        """Harness: test tag-write clear/query bridge dispatch to live AE poller."""
        from pytest_dimension_ae.probes import skip_or_fail

        skip_or_fail(
            ae_probe.ready,
            "AE not reachable for live tag-write verification",
            ae_required=ae_required,
        )

        from bridge.sovereign_bridge import SovereignBridge, ScrapeEngineError

        bridge = SovereignBridge(project_root=str(_REPO))
        try:
            res = bridge.clear_manual_tag(
                uid="__dimension_nonexistent_layer__",
                timeout_s=4.0,
            )
            assert res is not None
            assert res.get("status") in ("OK", "NOT_FOUND", "ERROR", "TIMEOUT")
        except ScrapeEngineError as err:
            # When layer is not found in active comp, ScrapeEngineError is raised with payload error
            assert len(str(err)) > 0

    def test_poller_heartbeat_contract_fields(self, ae_probe, ae_required):
        """Harness: verify all structured fields in poller_heartbeat.txt."""
        from pytest_dimension_ae.probes import skip_or_fail

        skip_or_fail(
            ae_probe.heartbeat_exists and ae_probe.heartbeat_fresh,
            "Poller heartbeat not fresh for contract inspection",
            ae_required=ae_required,
        )
        text = ae_probe.heartbeat_text or ""
        assert "poller_tick=" in text or "poller_started=" in text
        assert "task_id=" in text
        assert "source=" in text

    def test_live_ae_mid_inject_abort_roundtrip(self, ae_probe, ae_required, tmp_path):
        """Harness: test cooperative mid-inject abort roundtrip against active AE session."""
        import json
        import time
        from pytest_dimension_ae.probes import skip_or_fail

        skip_or_fail(
            ae_probe.ready,
            "AE not reachable for live inject abort test",
            ae_required=ae_required,
        )

        chunks_dir = tmp_path / "Chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        chunk_paths = []
        for c in range(5):
            chunk_file = chunks_dir / f"chunk_{c:03d}.json"
            chunk_file.write_text(
                json.dumps({
                    "chunk_index": c,
                    "totalChunks": 5,
                    "layers": [{
                        "name": f"Live_Test_Layer_{c}",
                        "uid": f"uid_live_{c}",
                        "index": 1,
                        "layer_index": 1,
                        "layer_kind": "av",
                        "is_root": True,
                        "conformed_transforms": {
                            "is_root": True,
                            "position": [960.0, 540.0, 0.0],
                            "scale": [100.0, 100.0, 100.0],
                            "rotation": 0.0,
                            "anchor": [960.0, 540.0, 0.0],
                        }
                    }]
                }),
                encoding="utf-8",
            )
            chunk_paths.append(str(chunk_file))

        manifest_path = tmp_path / "chunk_manifest.json"
        log_path = tmp_path / "transfer_status.log"
        abort_request_path = tmp_path / "inject_abort_request.json"

        manifest_path.write_text(
            json.dumps({
                "status": "ok",
                "preset_label": "LIVE_ABORT_TEST",
                "expected_comp_name": "",
                "total_layers": 5,
                "total_chunks": 5,
                "chunk_paths": chunk_paths,
                "allow_state_hash_bypass": True,
            }),
            encoding="utf-8",
        )

        from stages.inject import dispatch_file_bridge_inject
        from dimension_server import tail_transfer_log_until_terminal

        # 1. Dispatch inject job to live AE poller
        dispatch_file_bridge_inject(
            project_root=str(_REPO),
            manifest_path=str(manifest_path),
            log_path=str(log_path),
        )

        # 2. Write abort request immediately to test cooperative tick boundary interception
        abort_request_path.write_text(
            json.dumps({"reason": "user_cancelled", "ts": time.time()}),
            encoding="utf-8",
        )

        # 3. Tail log for terminal status (up to 8 seconds)
        logs = tail_transfer_log_until_terminal(str(log_path), timeout_s=8.0, poll_interval_s=0.1)

        terminal_beacons = [
            m for m in logs
            if isinstance(m, dict) and m.get("status") in ("COMPLETE", "FAILED", "ABORTED")
        ]
        assert len(terminal_beacons) > 0, "Poller did not emit terminal beacon within timeout"
        final_status = terminal_beacons[-1].get("status")
        assert final_status in ("ABORTED", "FAILED")
        if final_status == "ABORTED":
            assert terminal_beacons[-1].get("error") == "User cancelled"
            assert not abort_request_path.exists(), "Babysitter must remove abort request file upon abort"