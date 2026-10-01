# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Tests for the unified `dimension` CLI (issue #5).

Two contracts under test:

1. The machine face: with --json, stdout is exactly one parseable
   JSON document, logs stay on stderr, exit codes are meaningful.
2. Ops purity: `dimension.ops` functions return dicts and never print
   (the future TUI calls them directly).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from dimension import cli as dimension_cli
from dimension.common import (
    EXIT_AE_UNAVAILABLE,
    EXIT_ERROR,
    EXIT_OK,
    EXIT_UNSCRIPTABLE,
    EXIT_USAGE,
)
from dimension.ops import (
    naming as naming_ops,
    provenance as provenance_ops,
    safe_zone as safe_zone_ops,
)

FIXTURE_MANIFEST = os.path.join(
    os.path.dirname(__file__), "fixtures", "scrape_manifest_v5.json"
)
FIXTURE_LUT = os.path.join(
    os.path.dirname(__file__), "fixtures", "luts", "flame_scene_linear.cube"
)


def run_cli(argv, capsys):
    """Run the CLI in-process; returns (exit_code, stdout, stderr)."""
    code = dimension_cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def run_cli_json(argv, capsys):
    """Run with --json; stdout must be exactly one JSON document."""
    code, out, err = run_cli(["--json"] + argv, capsys)
    payload = json.loads(out)  # raises if stdout isn't pure JSON
    assert isinstance(payload, dict)
    return code, payload, err


# ── machine-face contract ─────────────────────────────────────────

def test_json_catalog_presets_is_pure_json(capsys):
    code, payload, _ = run_cli_json(["catalog", "presets"], capsys)
    assert code == EXIT_OK
    assert payload["status"] == "OK"
    assert payload["preset_count"] > 0
    # stdout purity is proven by json.loads(out) inside run_cli_json;
    # (pytest's log capture intercepts the engine logger in-process,
    # so stderr emptiness here proves nothing — the subprocess test
    # below covers real stderr routing.)


def test_json_error_shape_and_exit_code(capsys):
    code, payload, _ = run_cli_json(["catalog", "show", "no-such-preset"], capsys)
    assert code == EXIT_ERROR
    assert payload["status"] == "ERROR"
    assert payload["code"] == "PRESET_NOT_FOUND"


def test_lut_inject_honest_fail_exit_65(capsys):
    code, payload, _ = run_cli_json(["lut", "inject", "x.cube"], capsys)
    assert code == EXIT_UNSCRIPTABLE
    assert payload["code"] == "LUT_UNSCRIPTABLE"


def test_ae_mask_show_without_ae_exit_69(capsys):
    code, payload, _ = run_cli_json(
        ["ae", "mask", "show", "--preset", "tiktok_video"], capsys
    )
    assert code == EXIT_AE_UNAVAILABLE
    assert payload["code"] == "AE_UNAVAILABLE"


def test_ae_probe_exits_zero_when_unreachable(capsys):
    code, payload, _ = run_cli_json(["ae", "probe"], capsys)
    assert code == EXIT_OK
    assert payload["ready"] is False
    assert payload["hint"]


def test_usage_error_exit_2(capsys):
    with pytest.raises(SystemExit) as exc:
        dimension_cli.main(["bogus-command"])
    assert exc.value.code == EXIT_USAGE


def test_missing_source_file_exit_1(capsys):
    code, payload, _ = run_cli_json(
        ["conform", "--source", "/nope/missing.json"], capsys
    )
    assert code == EXIT_ERROR
    assert payload["code"] == "SOURCE_NOT_FOUND"


def test_provenance_readonly_without_db(capsys):
    code, payload, _ = run_cli_json(
        ["provenance", "duplicates", "--project-dir", "/tmp"], capsys
    )
    assert code == EXIT_OK
    assert payload["db_exists"] is False
    assert payload["duplicates"] == []
    # Read-only: the op must not create the database file.
    assert not os.path.exists("/tmp/.dimension/dimension.db")


def test_module_entry_propagates_exit_code():
    proc = subprocess.run(
        [sys.executable, "-m", "dimension", "--json", "lut", "inject", "x.cube"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == EXIT_UNSCRIPTABLE
    assert json.loads(proc.stdout)["code"] == "LUT_UNSCRIPTABLE"


def test_subprocess_stdout_stderr_separation():
    # Real process: engine log lines ("TargetStore loaded") must land on
    # stderr; stdout must be exactly the JSON document.
    proc = subprocess.run(
        [sys.executable, "-m", "dimension", "--json", "catalog", "presets"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == EXIT_OK
    payload = json.loads(proc.stdout)
    assert payload["status"] == "OK"
    assert "TargetStore loaded" not in proc.stdout
    assert "preset_count" not in proc.stderr


# ── human face ────────────────────────────────────────────────────

def test_human_catalog_presets(capsys):
    code, out, _ = run_cli(["catalog", "presets"], capsys)
    assert code == EXIT_OK
    assert "preset(s):" in out
    assert "{" not in out  # no JSON in human mode


def test_human_naming_resolve(capsys):
    code, out, _ = run_cli(
        ["naming", "resolve", "--source", "Hero", "--existing", "Hero_D",
         "--template", "{source}_D"], capsys
    )
    assert code == EXIT_OK
    assert "Hero_D_v2" in out


# ── ops purity (TUI calls these directly) ─────────────────────────

def test_ops_do_not_print(capsys):
    naming_ops.resolve_name_op(source="A", existing="A_D")
    provenance_ops.duplicates_op(project_dir="/tmp")
    out, err = capsys.readouterr()
    assert out == ""


def test_naming_collision_bump():
    r = naming_ops.resolve_name_op(
        source="Hero", existing="Hero_D", template="{source}_D"
    )
    assert (r["name"], r["version"]) == ("Hero_D_v2", 2)
    r2 = naming_ops.resolve_name_op(
        source="Hero", existing="", template="{source}_D"
    )
    assert (r2["name"], r2["version"]) == ("Hero_D", 1)


def test_naming_engine_default_template():
    # No --template: the engine's own {source}_{preset} wins; an empty
    # preset slugs to "conform".
    r = naming_ops.resolve_name_op(source="Hero", preset_id="builtin:tiktok_video")
    assert r["name"] == "Hero_tiktok_video"
    r2 = naming_ops.resolve_name_op(source="Hero")
    assert r2["name"] == "Hero_conform"


def test_safe_zone_plan_zones_sum_to_one():
    r = safe_zone_ops.mask_plan_op(preset="tiktok_video")
    assert r["status"] == "OK"
    z = r["zones"]
    assert abs(z["go"] + z["nudge"] + z["cutoff"] - 1.0) < 1e-6


def test_provenance_roundtrip(tmp_path):
    from core import dimension_db

    project_dir = str(tmp_path)
    conn = dimension_db.open_project_db(project_dir)
    dimension_db.record_creation(
        conn, name="BG_D", source="BG", session="s1", operation="duplicate"
    )
    conn.close()

    dups = provenance_ops.duplicates_op(project_dir=project_dir)
    assert dups["duplicates"] == ["BG_D"]
    assert dups["db_exists"] is True


# ── end-to-end through the real engine ────────────────────────────

@pytest.mark.skipif(
    not os.path.isfile(FIXTURE_MANIFEST), reason="fixture manifest missing"
)
def test_conform_json_contract(tmp_path, capsys, monkeypatch):
    # The engine writes chunk_manifest.json to the CWD (pre-existing
    # behavior) — contain it.
    monkeypatch.chdir(tmp_path)
    out_dir = tmp_path / "chunks"
    code, payload, _ = run_cli_json(
        [
            "conform",
            "--source", FIXTURE_MANIFEST,
            "--preset", "tiktok_video",
            "--output", str(out_dir),
            "--no-report",
        ],
        capsys,
    )
    assert code == EXIT_OK
    assert payload["status"] == "OK"
    assert payload["chunk_count"] >= 1
    assert os.path.isfile(payload["chunk_manifest_path"])


@pytest.mark.skipif(not os.path.isfile(FIXTURE_LUT), reason="fixture LUT missing")
def test_lut_validate_roundtrip(capsys):
    code, payload, _ = run_cli_json(["lut", "validate", FIXTURE_LUT], capsys)
    assert code == EXIT_OK
    assert payload["status"] == "OK"
    assert payload["source_format"] == "cube"
