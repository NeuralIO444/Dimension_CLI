# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial. See LICENSE.

"""Ship-queue checks: config resolution, machine-face holes, provenance."""

from __future__ import annotations

import json


from dimension import cli as dimension_cli
from dimension.common import EXIT_ERROR
from logic.config_paths import bundled_config_dir, config_path
from stages.conform import ConformConfig, _creation_names, _record_conform_provenance


def test_checkout_config_dir_resolves_channels():
    root = bundled_config_dir()
    assert (root / "channels.yaml").is_file()
    assert config_path("profiles").is_dir()
    assert config_path("safe_zones").is_dir()


def test_preview_invalid_manifest_is_structured_error(tmp_path, capsys):
    bad = tmp_path / "scrape.json"
    bad.write_text("{not json", encoding="utf-8")
    code, out, _ = _run(
        ["--json", "duplication", "preview", "--manifest", str(bad),
         "--width", "1080", "--height", "1920"],
        capsys,
    )
    assert code == EXIT_ERROR
    payload = json.loads(out)
    assert payload["code"] == "MANIFEST_INVALID"


def test_creation_names_reads_output_comp(tmp_path):
    path = tmp_path / "chunk_manifest.json"
    path.write_text(json.dumps({
        "output_comp_name": "Hero_tiktok",
        "mirror_tree": [{"name": "Hero_tiktok_pre"}],
    }), encoding="utf-8")
    assert _creation_names(str(path)) == ["Hero_tiktok", "Hero_tiktok_pre"]


def test_record_conform_provenance_roundtrip(tmp_path, capsys):
    source = tmp_path / "scrape_manifest.json"
    source.write_text("{}", encoding="utf-8")
    manifest = tmp_path / "chunk_manifest.json"
    manifest.write_text(json.dumps({"output_comp_name": "Hero_tiktok"}), encoding="utf-8")
    config = ConformConfig(source=str(source), preset="tiktok_video")
    _record_conform_provenance(
        config,
        status="ok",
        session="sess",
        chunk_manifest_path=str(manifest),
        names=["Hero_tiktok"],
        detail={"preset": "tiktok_video"},
    )
    db = tmp_path / ".dimension" / "dimension.db"
    assert db.is_file()
    code, out, _ = _run(
        ["--json", "provenance", "duplicates", "--project-dir", str(tmp_path)],
        capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert "Hero_tiktok" in payload["duplicates"]
    assert not (tmp_path / ".dimension" / "duplication_log.json").exists()


def _run(argv, capsys):
    code = dimension_cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err
