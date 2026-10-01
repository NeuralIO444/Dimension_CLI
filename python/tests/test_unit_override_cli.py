# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_unit_override_cli.py

CLI-spawn path for the CEP "Merge into Unit" / "Dissolve" buttons
(BUGS.md "Merge into Unit button is dead"). `unit_override_cli.py`
writes to the same `unit_overrides.json` sidecar shape as
`sovereign_bridge.handle_create_unit_job` / `handle_dissolve_unit_job`
(the socket-mode path), via `stages.tag`'s load/save helpers.

The CLI never reads the manifest's contents — only its path, to derive
the sidecar directory (`stages.tag.session_dir_for_manifest`) — so
these tests use an empty placeholder manifest file.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest


def _run_cli(monkeypatch, argv):
    import unit_override_cli
    monkeypatch.setattr(sys, "argv", ["unit-override"] + argv)
    unit_override_cli.main()


@pytest.fixture
def manifest_path(tmp_path):
    p = tmp_path / "scrape_manifest.json"
    p.write_text("{}", encoding="utf-8")
    return str(p)


def _sidecar_path(manifest_path):
    from pathlib import Path
    return Path(manifest_path).parent / "unit_overrides.json"


class TestCreate:
    def test_create_writes_sidecar(self, manifest_path, monkeypatch, capsys):
        _run_cli(monkeypatch, ["--manifest", manifest_path, "--create", "uidA,uidB"])
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "OK"
        unit_id = out["unit_id"]
        assert unit_id.startswith("manual_")

        sidecar = json.loads(_sidecar_path(manifest_path).read_text())
        entry = sidecar["unit_overrides"][unit_id]
        assert entry["action"] == "create"
        assert entry["members"] == ["uidA", "uidB"]

    def test_create_preserves_existing_overrides(self, manifest_path, monkeypatch, capsys):
        _run_cli(monkeypatch, ["--manifest", manifest_path, "--create", "uidA,uidB"])
        capsys.readouterr()
        _run_cli(monkeypatch, ["--manifest", manifest_path, "--create", "uidC,uidD"])
        out2 = json.loads(capsys.readouterr().out)

        sidecar = json.loads(_sidecar_path(manifest_path).read_text())
        assert len(sidecar["unit_overrides"]) == 2
        assert out2["unit_id"] in sidecar["unit_overrides"]

    def test_create_rejects_single_uid(self, manifest_path, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, ["--manifest", manifest_path, "--create", "uidA"])
        assert exc_info.value.code == 1
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "ERROR"
        assert not _sidecar_path(manifest_path).exists()

    def test_create_rejects_empty(self, manifest_path, monkeypatch, capsys):
        with pytest.raises(SystemExit):
            _run_cli(monkeypatch, ["--manifest", manifest_path, "--create", ""])
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "ERROR"


class TestDissolve:
    def test_dissolve_writes_sidecar(self, manifest_path, monkeypatch, capsys):
        _run_cli(monkeypatch, ["--manifest", manifest_path, "--dissolve", "group:0:HERO:0:5_0:10"])
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "OK"
        assert out["unit_id"] == "group:0:HERO:0:5_0:10"

        sidecar = json.loads(_sidecar_path(manifest_path).read_text())
        entry = sidecar["unit_overrides"]["group:0:HERO:0:5_0:10"]
        assert entry["action"] == "dissolve"

    def test_dissolve_after_create_round_trip(self, manifest_path, monkeypatch, capsys):
        _run_cli(monkeypatch, ["--manifest", manifest_path, "--create", "uidA,uidB"])
        created = json.loads(capsys.readouterr().out)
        unit_id = created["unit_id"]

        _run_cli(monkeypatch, ["--manifest", manifest_path, "--dissolve", unit_id])
        capsys.readouterr()

        sidecar = json.loads(_sidecar_path(manifest_path).read_text())
        assert sidecar["unit_overrides"][unit_id]["action"] == "dissolve"


class TestFlagValidation:
    def test_rejects_neither_flag(self, manifest_path, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, ["--manifest", manifest_path])
        assert exc_info.value.code == 2  # argparse usage error

    def test_rejects_both_flags(self, manifest_path, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, [
                "--manifest", manifest_path,
                "--create", "uidA,uidB",
                "--dissolve", "group:0:HERO:0:5",
            ])
        assert exc_info.value.code == 2  # argparse mutually-exclusive error

    def test_rejects_missing_manifest_arg(self, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc_info:
            _run_cli(monkeypatch, ["--create", "uidA,uidB"])
        assert exc_info.value.code == 2
