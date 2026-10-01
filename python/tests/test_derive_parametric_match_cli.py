# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_derive_parametric_match_cli.py

Color Match "no LUT" quick match — CLI-spawn path for
BB.deriveParametricMatch() (backend_bridge.js). Proves the script's
argv handling and JSON envelope; core.horizon_color.py's own test
suite (test_horizon_color_toolkit.py) covers the actual per-channel
fit math.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))


def _run_cli(monkeypatch, argv):
    import derive_parametric_match
    monkeypatch.setattr(sys, "argv", ["derive_parametric_match.py"] + argv)
    return derive_parametric_match.main()


class TestDeriveOK:
    def test_valid_stills_report_ok_with_per_channel_fit(self, monkeypatch, capsys, tmp_path):
        from PIL import Image

        ref_path = str(tmp_path / "ref.png")
        grad_path = str(tmp_path / "grad.png")
        Image.new("RGB", (32, 32), color=(100, 150, 200)).save(ref_path)
        Image.new("RGB", (32, 32), color=(120, 130, 220)).save(grad_path)

        exit_code = _run_cli(monkeypatch, [ref_path, grad_path])
        out = json.loads(capsys.readouterr().out)

        assert exit_code == 0
        assert out["status"] == "OK"
        expected_keys = {"slope", "offset", "gamma", "input_black", "input_white", "output_black", "output_white"}
        for channel in ("red", "green", "blue"):
            assert set(out[channel].keys()) == expected_keys

    def test_never_writes_a_file(self, monkeypatch, capsys, tmp_path):
        from PIL import Image

        ref_path = str(tmp_path / "ref.png")
        grad_path = str(tmp_path / "grad.png")
        Image.new("RGB", (32, 32), color=(10, 20, 30)).save(ref_path)
        Image.new("RGB", (32, 32), color=(40, 50, 60)).save(grad_path)

        before = set(tmp_path.iterdir())
        _run_cli(monkeypatch, [ref_path, grad_path])
        after = set(tmp_path.iterdir())
        assert before == after


class TestDeriveErrors:
    def test_missing_file_reports_error_not_traceback(self, monkeypatch, capsys, tmp_path):
        exit_code = _run_cli(monkeypatch, [
            str(tmp_path / "does_not_exist_ref.png"),
            str(tmp_path / "does_not_exist_grad.png"),
        ])
        assert exit_code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "ERROR"
        assert "error" in err and err["error"]

    def test_missing_args_exits_nonzero(self, monkeypatch, capsys):
        try:
            _run_cli(monkeypatch, [])
            raised = False
        except SystemExit as exc:
            raised = True
            assert exc.code != 0
        assert raised, "argparse must reject missing required positional args"
