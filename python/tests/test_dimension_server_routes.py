# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_dimension_server_routes.py

HTTP-level coverage for the new/modified dimension_server.py routes added
during the 2026-07-21 Dashboard redesign: the license gate on
/api/execute-batch, /api/license/status, /api/save-conform-option, and the
/api/style/* routes (thin wrappers over logic/style_ops.py).

No prior test in this repo exercises DimensionAPIHandler over a real
socket (existing coverage in test_dimension_server_inject.py calls
run_batch_thread directly) — these routes are thin wrappers over
already-tested logic (license_status.py, core/conform_options.py), so a
real end-to-end HTTP round trip is the most direct way to prove the
wiring itself is correct, rather than re-testing logic covered elsewhere.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

import dimension_server


@pytest.fixture
def isolated_prefs(tmp_path, monkeypatch):
    """Same isolation pattern as test_license_status.py / test_preferences_state.py
    — never touch the real ~/Library state.json."""
    import logic.preferences_state as ps
    target = tmp_path / "state.json"
    monkeypatch.setattr(ps, "state_json_path", lambda: target)
    monkeypatch.setattr(ps.preferences, "_loaded", False)
    monkeypatch.setattr(ps.preferences, "_state", {})
    return target


@pytest.fixture
def running_server(tmp_path, isolated_prefs, monkeypatch):
    """Starts a real DimensionAPIHandler-backed HTTPServer on an ephemeral
    port, pointed at a manifest under tmp_path, torn down after the test.
    Also forces the license dev-bypass path OFF by default (no .git dir
    under tmp_path) so tests explicitly control license state via prefs."""
    manifest_path = tmp_path / "scrape_manifest.json"
    manifest_path.write_text(json.dumps({"layers": []}))

    monkeypatch.setattr(dimension_server, "_DIMENSION_REPO_ROOT", None)

    # execution_state is a process-wide global mutated (and left mutated)
    # by other test modules (e.g. test_dimension_server_inject.py sets it
    # to "failed"/"success" without resetting afterward) — reset to a
    # known idle baseline so this fixture's tests aren't order-dependent
    # on whatever ran before them in the same pytest session.
    with dimension_server.execution_lock:
        dimension_server.execution_state.update({
            "status": "idle", "current_target": 0, "total_targets": 0,
            "progress_pct": 0, "progress_text": "Waiting...", "logs": [],
            "warnings": {"skips": 0, "rewire": 0}, "error": None,
            "target_results": [], "cancel_requested": False,
            "project_root": None,
        })

    server = HTTPServer(("127.0.0.1", 0), dimension_server.handler_factory(str(manifest_path)))
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", tmp_path
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _post(url: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


class TestLicenseStatusRoute:
    def test_fresh_install_reports_none(self, running_server):
        base_url, _ = running_server
        status, body = _get(f"{base_url}/api/license/status")
        assert status == 200
        assert body["license"]["status"] == "none"
        assert body["license"]["canUsePipeline"] is False


class TestExecuteBatchLicenseGate:
    def test_blocked_when_unactivated(self, running_server):
        base_url, _ = running_server
        status, body = _post(f"{base_url}/api/execute-batch", {
            "profile": "default", "mode": "Fit", "bleed": 0.0,
            "targets": [{"width": 1080, "height": 1080}],
        })
        assert status == 403
        assert body["code"] == "license_blocked"
        assert body["license_status"] == "none"
        assert "AE panel" in body["message"]
        with dimension_server.execution_lock:
            assert dimension_server.execution_state["status"] == "idle"

    def test_blocked_when_expired_with_distinct_message(self, running_server):
        from logic.preferences_state import preferences

        base_url, _ = running_server
        preferences.license_key = "ABC"
        preferences.license_activated_at = "2020-01-01T00:00:00Z"
        preferences.license_expires_at = "2020-01-31T00:00:00Z"
        preferences.save()

        status, body = _post(f"{base_url}/api/execute-batch", {
            "targets": [{"width": 1080, "height": 1080}],
        })
        assert status == 403
        assert body["license_status"] == "expired"
        assert "expired" in body["message"].lower()

    def test_allowed_past_gate_when_trial_active(self, running_server, monkeypatch):
        """Once past the license gate, the existing poller-alive check is
        the next gate — confirm we get THAT far (not license_blocked) when
        a trial is active, without needing a real AE session for this
        route-wiring test."""
        from logic.preferences_state import preferences
        from bridge.sovereign_bridge import SovereignBridge

        base_url, _ = running_server
        preferences.license_key = "ABC"
        preferences.license_activated_at = "2026-01-01T00:00:00Z"
        preferences.license_expires_at = "2099-01-01T00:00:00Z"
        preferences.save()

        monkeypatch.setattr(SovereignBridge, "_poller_is_alive", lambda self: (False, "no heartbeat file"))

        status, body = _post(f"{base_url}/api/execute-batch", {
            "targets": [{"width": 1080, "height": 1080}],
        })
        assert status == 400
        assert body.get("code") != "license_blocked"
        assert "AE engine not responding" in body["message"]

    def test_stale_abort_flag_cleared_before_new_batch_starts(self, running_server, monkeypatch):
        """Audit fix (2026-08-27) companion test. The clear-a-leftover-
        abort-flag-from-a-prior-run call moved from inside
        run_batch_thread (right before dispatch — see
        test_run_batch_thread_does_not_erase_a_cancel_written_before_dispatch,
        which proves it's gone from there) to here, in the
        /api/execute-batch handler, before the batch thread starts. This
        proves the new location still does the job the old one was
        originally for: a flag left over from a run that crashed or was
        cancelled last session must not silently kill a brand new run's
        very first chunk tick."""
        from logic.preferences_state import preferences
        from bridge.sovereign_bridge import SovereignBridge

        base_url, tmp_path = running_server
        preferences.license_key = "ABC"
        preferences.license_activated_at = "2026-01-01T00:00:00Z"
        preferences.license_expires_at = "2099-01-01T00:00:00Z"
        preferences.save()

        monkeypatch.setattr(SovereignBridge, "_poller_is_alive", lambda self: (True, ""))

        abort_path = tmp_path / dimension_server.INJECT_ABORT_REQUEST_FILENAME
        abort_path.write_text('{"requested": true, "source": "dashboard"}')
        assert abort_path.exists()

        status, body = _post(f"{base_url}/api/execute-batch", {
            "targets": [{"width": 1080, "height": 1080}],
        })
        assert status == 200
        assert not abort_path.exists(), (
            "a stale abort flag from a prior run must be cleared before "
            "a new batch's own cancel window can open"
        )


class TestSaveConformOptionRoute:
    def test_writes_conform_options_next_to_manifest(self, running_server):
        base_url, tmp_path = running_server
        status, body = _post(f"{base_url}/api/save-conform-option", {
            "key": "camera_depth_mode", "value": "S",
        })
        assert status == 200
        assert body["options"]["camera_depth_mode"] == "S"

        on_disk = json.loads((tmp_path / "conform_options.json").read_text())
        assert on_disk["camera_depth_mode"] == "S"

    def test_merges_with_existing_options_rather_than_clobbering(self, running_server):
        base_url, tmp_path = running_server
        (tmp_path / "conform_options.json").write_text(
            json.dumps({"some_other_key": "keep-me"})
        )
        status, body = _post(f"{base_url}/api/save-conform-option", {
            "key": "camera_depth_mode", "value": "K",
        })
        assert status == 200
        on_disk = json.loads((tmp_path / "conform_options.json").read_text())
        assert on_disk["some_other_key"] == "keep-me"
        assert on_disk["camera_depth_mode"] == "K"

    def test_missing_key_is_400(self, running_server):
        base_url, _ = running_server
        req = urllib.request.Request(
            f"{base_url}/api/save-conform-option",
            data=json.dumps({"value": "S"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as e:
            assert e.code == 400





class TestHistoryRoute:
    """/api/history — filesystem-scan session listing, Python port of
    cep/js/reports_ui.js's _listReports/_groupReportsBySession."""

    def test_no_reports_is_empty_sessions_list(self, running_server):
        base_url, _ = running_server
        status, body = _get(f"{base_url}/api/history")
        assert status == 200
        assert body["sessions"] == []

    def test_single_report_is_one_session_one_report(self, running_server):
        base_url, tmp_path = running_server
        (tmp_path / "conform_report__87N_Reels__1080x1920.html").write_text("<html></html>")

        status, body = _get(f"{base_url}/api/history")
        assert status == 200
        assert len(body["sessions"]) == 1
        session = body["sessions"][0]
        assert len(session["reports"]) == 1
        report = session["reports"][0]
        assert report["slug"] == "87N_Reels"
        assert report["dims"] == "1080x1920"
        assert report["name"] == "conform_report__87N_Reels__1080x1920.html"

    def test_reports_within_60s_cluster_into_one_session(self, running_server):
        base_url, tmp_path = running_server
        a = tmp_path / "conform_report__87N_Reels__1080x1920.html"
        b = tmp_path / "conform_report__87N_Reels__1920x1080.html"
        a.write_text("<html></html>")
        b.write_text("<html></html>")
        now = time.time()
        os.utime(a, (now, now))
        os.utime(b, (now, now - 30))  # 30s apart — same session

        status, body = _get(f"{base_url}/api/history")
        assert status == 200
        assert len(body["sessions"]) == 1
        assert len(body["sessions"][0]["reports"]) == 2

    def test_reports_over_60s_apart_are_separate_sessions(self, running_server):
        base_url, tmp_path = running_server
        a = tmp_path / "conform_report__87N_Reels__1080x1920.html"
        b = tmp_path / "conform_report__old_run__1920x1080.html"
        a.write_text("<html></html>")
        b.write_text("<html></html>")
        now = time.time()
        os.utime(a, (now, now))
        os.utime(b, (now - 3600, now - 3600))  # an hour apart

        status, body = _get(f"{base_url}/api/history")
        assert status == 200
        assert len(body["sessions"]) == 2

    def test_non_report_files_are_ignored(self, running_server):
        base_url, tmp_path = running_server
        (tmp_path / "scrape_manifest.json").write_text("{}")
        (tmp_path / "conform_report__decoy.txt").write_text("not html")
        (tmp_path / "notes.html").write_text("<html></html>")

        status, body = _get(f"{base_url}/api/history")
        assert status == 200
        assert body["sessions"] == []


class TestReportRoute:
    """/api/report?name=... — serves one report file's raw HTML.
    Filename validation is the only defense against path traversal, so
    that's the part this class weighs most heavily."""

    def test_valid_report_returns_its_html(self, running_server):
        base_url, tmp_path = running_server
        content = "<html><body>report</body></html>"
        (tmp_path / "conform_report__87N_Reels__1080x1920.html").write_text(content)

        url = f"{base_url}/api/report?name=conform_report__87N_Reels__1080x1920.html"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            assert "text/html" in resp.headers.get("Content-Type", "")
            assert resp.read().decode("utf-8") == content

    def test_missing_report_is_404(self, running_server):
        base_url, _ = running_server
        url = f"{base_url}/api/report?name=conform_report__nope__1080x1920.html"
        try:
            urllib.request.urlopen(url, timeout=5)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 404

    @pytest.mark.parametrize("bad_name", [
        "../../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "conform_report__x__1080x1920.html/../../secret",
        "not_a_report.html",
        "conform_report__x__1080x1920.htm",  # not .html
        "",
    ])
    def test_non_matching_filename_is_400_never_touches_filesystem(self, running_server, bad_name):
        base_url, _ = running_server
        from urllib.parse import quote
        url = f"{base_url}/api/report?name={quote(bad_name)}"
        try:
            urllib.request.urlopen(url, timeout=5)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 400

    def test_no_name_param_is_400(self, running_server):
        base_url, _ = running_server
        try:
            urllib.request.urlopen(f"{base_url}/api/report", timeout=5)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 400


class TestCancelRoute:
    def test_no_batch_running_is_400(self, running_server):
        base_url, _ = running_server
        status, body = _post(f"{base_url}/api/execute-batch/cancel", {})
        assert status == 400
        assert "no batch" in body["message"].lower()

    def test_running_during_conform_sets_cancel_flag(self, running_server):
        base_url, _ = running_server
        with dimension_server.execution_lock:
            dimension_server.execution_state["status"] = "running"
            dimension_server.execution_state["progress_pct"] = 20

        status, body = _post(f"{base_url}/api/execute-batch/cancel", {})
        assert status == 200
        assert "current target" in body["message"].lower()
        with dimension_server.execution_lock:
            assert dimension_server.execution_state["cancel_requested"] is True

    def test_running_past_conform_phase_writes_abort_flag(self, running_server):
        base_url, tmp_path = running_server
        with dimension_server.execution_lock:
            dimension_server.execution_state["status"] = "running"
            dimension_server.execution_state["progress_pct"] = 95
            dimension_server.execution_state["project_root"] = str(tmp_path)

        status, body = _post(f"{base_url}/api/execute-batch/cancel", {})
        assert status == 200
        assert "chunk boundary" in body["message"].lower()
        abort_path = tmp_path / dimension_server.INJECT_ABORT_REQUEST_FILENAME
        assert abort_path.is_file()
        payload = json.loads(abort_path.read_text(encoding="utf-8"))
        assert payload["requested"] is True
        assert payload["source"] == "dashboard"


class TestApiErrorsAreJson:
    """Every /api/ error body must be JSON, never http.server's default
    `<!DOCTYPE HTML>` error page.

    The Dashboard's fetch() helpers call res.json() unconditionally, so an
    HTML error body surfaces in the panel as
    `SyntaxError: Unexpected token '<'` — masking the real message and
    making a failed call look like a silent no-op. That's how a broken
    "+ add current comp as reference" reached manual QA looking like a
    dead button rather than a reportable error.
    """

    def _raw_error(self, url: str, payload: dict) -> tuple[int, str, str]:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read().decode("utf-8")

    @pytest.mark.parametrize("path,payload", [
        ("/api/save-conform-option", {"value": "S"}),
        ("/api/does-not-exist", {}),
    ])
    def test_error_body_parses_as_json(self, running_server, path, payload):
        base_url, _ = running_server
        code, content_type, raw = self._raw_error(f"{base_url}{path}", payload)
        assert code >= 400
        assert not raw.lstrip().startswith("<"), (
            f"{path} returned an HTML error body: {raw[:60]!r}")
        assert "application/json" in content_type
        body = json.loads(raw)
        assert body["status"] == "error"
        assert body["message"]

    def test_non_api_404_keeps_html_error_page(self, running_server):
        """The JSON override is scoped to /api/ — static file requests
        keep http.server's normal HTML page."""
        base_url, _ = running_server
        try:
            urllib.request.urlopen(f"{base_url}/no-such-file.css", timeout=5)
            raise AssertionError("expected HTTPError")
        except urllib.error.HTTPError as e:
            assert e.code == 404
            assert e.read().decode("utf-8").lstrip().startswith("<")
