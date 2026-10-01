# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_lut_cli.py

Color Match (Track D, CM4) — CLI-spawn path for the confirmation
modal's LUT file picker. `lut_cli.py validate <path>` is
`core.lut_parser.load_lut()`'s first production caller: the CEP panel
spawns this via `BB.validateLut()` (backend_bridge.js) before letting
the artist proceed to inject, so a malformed .cube/.3dl surfaces as a
readable error in the modal instead of a confusing AE-side failure.

Reuses the fixture files under python/tests/fixtures/luts/ built for
test_lut_parser.py rather than duplicating LUT content here — this
suite only proves the CLI's argv handling and JSON envelope, not the
parser's format-branch coverage.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "luts")


def _run_cli(monkeypatch, argv):
    import lut_cli
    monkeypatch.setattr(sys, "argv", ["lut"] + argv)
    lut_cli.main()


class TestValidateOK:
    def test_valid_cube_reports_ok(self, monkeypatch, capsys):
        path = os.path.join(FIXTURES, "identity_2x2x2.cube")
        _run_cli(monkeypatch, ["validate", path])
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "OK"
        assert out["source_format"] == "cube"
        assert out["grid_size"] == 2

    def test_valid_3dl_reports_ok(self, monkeypatch, capsys):
        path = os.path.join(FIXTURES, "flame_2023_10bit.3dl")
        _run_cli(monkeypatch, ["validate", path])
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "OK"
        assert out["source_format"] == "3dl"
        assert out["bit_depth"] == 10


class TestValidateErrors:
    def test_nan_corrupted_reports_error_not_traceback(self, monkeypatch, capsys):
        path = os.path.join(FIXTURES, "nan_corrupted.cube")
        _run_cli(monkeypatch, ["validate", path])
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "ERROR"
        assert "error" in out and out["error"]

    def test_non_cubic_mesh_reports_error(self, monkeypatch, capsys):
        path = os.path.join(FIXTURES, "non_cubic_mesh.3dl")
        _run_cli(monkeypatch, ["validate", path])
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "ERROR"

    def test_missing_file_reports_error_not_crash(self, monkeypatch, capsys):
        path = os.path.join(FIXTURES, "does_not_exist.cube")
        _run_cli(monkeypatch, ["validate", path])
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "ERROR"
        assert "error" in out


class TestDeriveDispatch:
    """`derive`/`derive-parametric` used to be unrecognized subcommands
    — `main()` only ever matched `sys.argv[1] != "validate"`, so any
    invocation via the packaged `dimension_engine` binary (the only
    path BB.deriveLutFromStills/BB.convertImageToPng/
    BB.deriveParametricMatch use once bundled — see BB.resolveCli's
    dev-venv-vs-bundled-binary branch in backend_bridge.js) failed with
    a "Usage: lut_cli.py validate <path>" error, meaning AUTO-MATCH and
    Quick Match's derive step never worked outside a dev venv. This
    class proves the dispatch itself, not just the underlying scripts
    (already covered by test_horizon_color_toolkit.py and
    test_derive_parametric_match_cli.py)."""

    def test_derive_forwards_to_derive_lut_and_writes_a_cube(self, monkeypatch, capsys, tmp_path):
        from PIL import Image

        ref = str(tmp_path / "ref.png")
        grad = str(tmp_path / "grad.png")
        out_cube = str(tmp_path / "out.cube")
        Image.new("RGB", (16, 16), color=(50, 100, 150)).save(ref)
        Image.new("RGB", (16, 16), color=(70, 90, 170)).save(grad)

        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, ["derive", ref, grad, out_cube, "--size", "3"])
        assert exc_info.value.code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "OK"
        assert out["lut_path"] == out_cube
        assert os.path.exists(out_cube)

    def test_derive_convert_png_flag_reaches_the_underlying_script(self, monkeypatch, capsys, tmp_path):
        from PIL import Image

        ref = str(tmp_path / "ref.png")
        grad = str(tmp_path / "grad.png")
        out_cube = str(tmp_path / "out.cube")
        preview = str(tmp_path / "preview.png")
        Image.new("RGB", (16, 16), color=(50, 100, 150)).save(ref)
        Image.new("RGB", (16, 16), color=(70, 90, 170)).save(grad)

        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, ["derive", ref, grad, out_cube, "--convert-png", preview])
        assert exc_info.value.code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["preview_png"] == preview
        assert os.path.exists(preview)

    def test_derive_missing_file_reports_error_and_exits_nonzero(self, monkeypatch, capsys, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, [
                "derive",
                str(tmp_path / "missing_ref.png"),
                str(tmp_path / "missing_grad.png"),
                str(tmp_path / "out.cube"),
            ])
        assert exc_info.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "ERROR"

    def test_derive_parametric_forwards_and_reports_ok(self, monkeypatch, capsys, tmp_path):
        from PIL import Image

        ref = str(tmp_path / "ref.png")
        grad = str(tmp_path / "grad.png")
        Image.new("RGB", (16, 16), color=(50, 100, 150)).save(ref)
        Image.new("RGB", (16, 16), color=(70, 90, 170)).save(grad)

        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, ["derive-parametric", ref, grad])
        assert exc_info.value.code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "OK"
        expected_keys = {"slope", "offset", "gamma", "input_black", "input_white", "output_black", "output_white"}
        for channel in ("red", "green", "blue"):
            assert set(out[channel].keys()) == expected_keys

    def test_derive_parametric_missing_file_reports_error_and_exits_nonzero(self, monkeypatch, capsys, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, [
                "derive-parametric",
                str(tmp_path / "missing_ref.png"),
                str(tmp_path / "missing_grad.png"),
            ])
        assert exc_info.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "ERROR"


class TestDeriveSmartDispatch:
    """`derive-smart` (issue #479, Horizon Color Planner "Smart Match")
    forwards to scripts.derive_smart_match.main(), same dispatch shape
    as `derive`/`derive-parametric` above, and for the same reason: a
    subcommand exercised only via a dev venv is a subcommand that has
    never actually been proven to work for a customer running the
    packaged `dimension_engine` binary."""

    def _make_images(self, tmp_path, cross_channel=False):
        import numpy as np
        from PIL import Image

        grid = np.linspace(0, 1, 32, dtype=np.float64)
        src = np.zeros((32, 32, 3), dtype=np.float64)
        for c in range(3):
            src[:, :, c] = grid
        ref_path = str(tmp_path / "ref.png")
        grad_path = str(tmp_path / "grad.png")

        if cross_channel:
            graded = np.zeros_like(src)
            graded[:, :, 0] = src[:, :, 1] * 0.9 + 0.1
            graded[:, :, 1] = src[:, :, 2] * 0.8 + 0.05
            graded[:, :, 2] = src[:, :, 0] * 1.1
            graded = np.clip(graded, 0.0, 1.0)
        else:
            graded = np.clip(src * 0.9 + 0.05, 0.0, 1.0)

        Image.fromarray((src * 255).astype(np.uint8)).save(ref_path)
        Image.fromarray((graded * 255).astype(np.uint8)).save(grad_path)
        return ref_path, grad_path

    def test_derive_smart_simple_tone_shift_picks_levels_and_writes_no_cube(self, monkeypatch, capsys, tmp_path):
        ref, grad = self._make_images(tmp_path, cross_channel=False)
        out_cube = str(tmp_path / "out.cube")

        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, ["derive-smart", ref, grad, out_cube])
        assert exc_info.value.code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "OK"
        assert out["backend"] in ("levels", "lut_elided")
        assert out["levels"] is not None
        assert out["lut_path"] is None
        assert not os.path.exists(out_cube)

    def test_derive_smart_hard_chroma_pair_picks_lut_mesh_and_writes_a_real_cube(self, monkeypatch, capsys, tmp_path):
        ref, grad = self._make_images(tmp_path, cross_channel=True)
        out_cube = str(tmp_path / "out.cube")

        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, ["derive-smart", ref, grad, out_cube, "--size", "5"])
        assert exc_info.value.code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "OK"
        assert out["backend"] == "lut_mesh"
        assert out["levels"] is None
        assert out["lut_path"] == out_cube
        assert os.path.exists(out_cube)
        content = open(out_cube).read()
        assert "LUT_3D_SIZE 5" in content

    def test_derive_smart_missing_file_reports_error_and_exits_nonzero(self, monkeypatch, capsys, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, [
                "derive-smart",
                str(tmp_path / "missing_ref.png"),
                str(tmp_path / "missing_grad.png"),
                str(tmp_path / "out.cube"),
            ])
        assert exc_info.value.code == 1
        err = json.loads(capsys.readouterr().err)
        assert err["status"] == "ERROR"


class TestArgvHandling:
    def test_missing_args_exits_nonzero_with_json(self, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, [])
        assert exc_info.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "ERROR"

    def test_wrong_subcommand_exits_nonzero(self, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, ["bogus", "path"])
        assert exc_info.value.code == 1
