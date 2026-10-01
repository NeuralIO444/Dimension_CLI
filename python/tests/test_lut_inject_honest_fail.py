# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_lut_inject_honest_fail.py

Issue #9 — LUT injection honest-fail. AE exposes Apply Color LUT2's
file-path property as PropertyValueType.NO_VALUE: no scripting mechanism
can set it, so LUT injection is a permanent platform limitation
(Dimension #494), not a bug. Every inject path must fail loudly with the
named code LUT_UNSCRIPTABLE and a plain-English message — never a bare
traceback, never a silent no-op. No workaround chase.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bridge.color_match_bridge import (
    ColorMatchBridge,
    ColorMatchError,
    LutUnscriptableError,
)


def _run_lut_cli(monkeypatch, argv):
    import lut_cli

    monkeypatch.setattr(sys, "argv", ["lut"] + argv)
    lut_cli.main()


class TestInjectLutHonestFail:
    def test_raises_named_error(self, tmp_path, monkeypatch):
        cm = ColorMatchBridge(project_root=str(tmp_path))
        monkeypatch.setattr(cm._bridge, "_socket_is_listening", lambda: False)
        with pytest.raises(LutUnscriptableError) as exc_info:
            cm.inject_lut("100", "42", tmp_path / "grade.cube")
        assert exc_info.value.code == "LUT_UNSCRIPTABLE"

    def test_message_is_plain_english_not_traceback(self, tmp_path):
        cm = ColorMatchBridge(project_root=str(tmp_path))
        with pytest.raises(LutUnscriptableError) as exc_info:
            cm.inject_lut("100", "42", tmp_path / "grade.cube")
        msg = str(exc_info.value)
        assert "not possible" in msg
        assert "#494" in msg
        assert "Traceback" not in msg

    def test_never_touches_the_bridge(self, tmp_path, monkeypatch):
        """Honest-fail happens before dispatch: no bridge traffic at all."""
        cm = ColorMatchBridge(project_root=str(tmp_path))
        calls = []
        monkeypatch.setattr(
            cm._bridge,
            "execute_bridge_job",
            lambda job, timeout: calls.append(job),
        )
        with pytest.raises(LutUnscriptableError):
            cm.inject_lut("100", "42", tmp_path / "grade.cube", timeout_s=0.5)
        assert calls == []

    def test_fails_regardless_of_arguments(self, tmp_path):
        """Even invalid args get LUT_UNSCRIPTABLE: the platform cannot do
        it, so argument validation is moot."""
        cm = ColorMatchBridge(project_root=str(tmp_path))
        with pytest.raises(LutUnscriptableError):
            cm.inject_lut("", "", "")

    def test_still_a_color_match_error(self):
        """Backward-compat: existing `except ColorMatchError` handlers keep
        catching the inject failure."""
        assert issubclass(LutUnscriptableError, ColorMatchError)


class TestLutCliInject:
    def test_inject_exits_65_with_named_code(self, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _run_lut_cli(monkeypatch, ["inject", "/tmp/grade.cube"])
        assert exc_info.value.code == 65
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "ERROR"
        assert out["code"] == "LUT_UNSCRIPTABLE"
        assert "Traceback" not in out["error"]

    def test_help_states_limitation_plainly(self, monkeypatch, capsys):
        _run_lut_cli(monkeypatch, ["--help"])
        out = capsys.readouterr().out
        assert "LUT_UNSCRIPTABLE" in out
        assert "impossible" in out
        assert "#494" in out
        # ...and the local math that DOES work is still advertised.
        assert "derive" in out

    def test_local_math_paths_unaffected(self, monkeypatch, capsys):
        """validate still works headless — only injection honest-fails."""
        fixtures = os.path.join(os.path.dirname(__file__), "fixtures", "luts")
        path = os.path.join(fixtures, "identity_2x2x2.cube")
        _run_lut_cli(monkeypatch, ["validate", path])
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "OK"
