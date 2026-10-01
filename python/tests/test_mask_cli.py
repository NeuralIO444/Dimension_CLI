# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_mask_cli.py

#400 -- CLI-spawn path for the CEP panel's "Show Safe Zone" toggle.
SovereignBridge.toggle_safe_zone_mask() and the JSX
importSafeZoneMask/removeSafeZoneMask pair in Babysitter were fully
built with zero caller anywhere in the product; this is the first one.
Mocks SovereignBridge.toggle_safe_zone_mask (an AE round trip) — the
real round trip is covered by this PR's live-AE verification, not by
a synthetic fixture (see CLAUDE.md's "trusting synthetic Python test
fixtures" anti-pattern; a mocked bridge call proves argv/JSON-envelope
handling only, which is all this suite claims to prove).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


def _run_cli(monkeypatch, argv):
    import mask_cli
    monkeypatch.setattr(sys, "argv", ["mask"] + argv)
    mask_cli.main()


class TestShow:
    def test_missing_preset_reports_mask_missing_and_exits_1(self, monkeypatch, capsys, tmp_path):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, ["show", "no_such_preset"])
        assert exc_info.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "ERROR"
        assert "MASK_MISSING" in out["error"]

    def test_found_preset_calls_bridge_with_import_action_and_resolved_path(self, monkeypatch, capsys, tmp_path):
        monkeypatch.chdir(tmp_path)
        safe_zones = tmp_path / "config" / "safe_zones"
        safe_zones.mkdir(parents=True)
        mask_png = safe_zones / "tiktok.png"
        mask_png.write_bytes(b"\x89PNG\r\n\x1a\n")

        calls = {}

        def fake_toggle(self, action, mask_path=None, **kwargs):
            calls["action"] = action
            calls["mask_path"] = mask_path
            return {"status": "OK", "layer_index": 1, "name": "[ SOE_MASK_DEBUG ]"}

        from bridge.sovereign_bridge import SovereignBridge
        monkeypatch.setattr(SovereignBridge, "toggle_safe_zone_mask", fake_toggle)

        _run_cli(monkeypatch, ["show", "tiktok"])
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "OK"
        assert calls["action"] == "import"
        assert calls["mask_path"] == str(mask_png)

    def test_bridge_error_reports_json_error_and_exits_1(self, monkeypatch, capsys, tmp_path):
        monkeypatch.chdir(tmp_path)
        safe_zones = tmp_path / "config" / "safe_zones"
        safe_zones.mkdir(parents=True)
        (safe_zones / "tiktok.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        from bridge.sovereign_bridge import SovereignBridge, ScrapeEngineError

        def fake_toggle(self, action, mask_path=None, **kwargs):
            raise ScrapeEngineError("AE engine not responding")

        monkeypatch.setattr(SovereignBridge, "toggle_safe_zone_mask", fake_toggle)

        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, ["show", "tiktok"])
        assert exc_info.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "ERROR"
        assert "AE engine not responding" in out["error"]


class TestHide:
    def test_hide_calls_bridge_with_remove_action(self, monkeypatch, capsys, tmp_path):
        monkeypatch.chdir(tmp_path)
        calls = {}

        def fake_toggle(self, action, mask_path=None, **kwargs):
            calls["action"] = action
            calls["mask_path"] = mask_path
            return {"status": "OK", "removed": 1}

        from bridge.sovereign_bridge import SovereignBridge
        monkeypatch.setattr(SovereignBridge, "toggle_safe_zone_mask", fake_toggle)

        _run_cli(monkeypatch, ["hide"])
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "OK"
        assert calls["action"] == "remove"
        assert calls["mask_path"] is None

    def test_bridge_error_reports_json_error_and_exits_1(self, monkeypatch, capsys, tmp_path):
        monkeypatch.chdir(tmp_path)
        from bridge.sovereign_bridge import SovereignBridge, ScrapeEngineError

        def fake_toggle(self, action, mask_path=None, **kwargs):
            raise ScrapeEngineError("no active comp")

        monkeypatch.setattr(SovereignBridge, "toggle_safe_zone_mask", fake_toggle)

        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, ["hide"])
        assert exc_info.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "ERROR"
        assert "no active comp" in out["error"]


class TestArgvHandling:
    def test_missing_args_exits_nonzero(self, monkeypatch):
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, [])
        assert exc_info.value.code == 1

    def test_show_missing_preset_arg_exits_nonzero(self, monkeypatch):
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, ["show"])
        assert exc_info.value.code == 1

    def test_wrong_subcommand_exits_nonzero(self, monkeypatch):
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, ["bogus"])
        assert exc_info.value.code == 1
