# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_gh_release.py
Unit tests for the autonomous GitHub Release tool (python/tools/gh_release.py).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch, MagicMock

from tools.gh_release import (
    extract_version,
    extract_release_notes,
    compute_sha256,
    assemble_release_assets,
    dispatch_gh_release,
    run_preflight_quality_gates,
)


def test_extract_version():
    ver = extract_version()
    assert isinstance(ver, str)
    assert len(ver.split(".")) >= 2


def test_extract_release_notes(tmp_path: Path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Feat: New Feature (2026-08-31)\n- Item 1\n- Item 2\n\n### Feat: Old Feature\n- Old Item\n",
        encoding="utf-8"
    )
    notes = extract_release_notes(changelog)
    assert "### Feat: New Feature (2026-08-31)" in notes
    assert "- Item 1" in notes
    assert "- Item 2" in notes
    assert "Old Item" not in notes


def test_compute_sha256(tmp_path: Path):
    sample = tmp_path / "sample.txt"
    sample.write_text("Hello Dimension Release", encoding="utf-8")
    expected_hash = hashlib.sha256(b"Hello Dimension Release").hexdigest()
    assert compute_sha256(sample) == expected_hash


def test_assemble_release_assets(tmp_path: Path):
    dist_dir = tmp_path / "dist"
    # Create fake artifact
    fake_zxp = tmp_path / "Dimension_v6.0.0.zxp"
    fake_zxp.write_text("fake zxp binary", encoding="utf-8")

    with patch("tools.gh_release._REPO_ROOT", tmp_path):
        assets = assemble_release_assets("6.0.0", dist_dir)
        assert len(assets) >= 1
        asset_names = [a.name for a in assets]
        assert "checksums.sha256" in asset_names


@patch("subprocess.run")
def test_dispatch_gh_release_dry_run(mock_subproc, tmp_path: Path):
    asset1 = tmp_path / "asset.zxp"
    asset1.write_text("dummy", encoding="utf-8")

    success = dispatch_gh_release(
        version="6.0.0",
        tag="v6.0.0",
        notes="Release notes",
        assets=[asset1],
        dry_run=True,
    )
    assert success is True
    mock_subproc.assert_not_called()


@patch("subprocess.run")
def test_dispatch_gh_release_execution(mock_subproc, tmp_path: Path):
    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stdout = "https://github.com/NeuralIO444/Dimension/releases/tag/v6.0.0"
    mock_subproc.return_value = mock_res

    asset1 = tmp_path / "asset.zxp"
    asset1.write_text("dummy", encoding="utf-8")

    success = dispatch_gh_release(
        version="6.0.0",
        tag="v6.0.0",
        notes="Release notes",
        assets=[asset1],
        dry_run=False,
    )
    assert success is True
    mock_subproc.assert_called_once()
    args = mock_subproc.call_args[0][0]
    assert "gh" in args
    assert "release" in args
    assert "create" in args
    assert "v6.0.0" in args


@patch("subprocess.run")
def test_run_preflight_quality_gates_success(mock_subproc):
    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_subproc.return_value = mock_res

    res = run_preflight_quality_gates(skip_tests=True)
    assert res is True


@patch("subprocess.run")
def test_run_preflight_quality_gates_stops_at_first_failing_gate(mock_subproc):
    """Unlike auto_pr.py's run_pre_push_review (which collects every
    failure), run_preflight_quality_gates short-circuits with `return
    False` at the first failing gate. Gate 1 (reachability) failing
    must return False after exactly one subprocess call -- gates 2-4
    must never run."""
    mock_res = MagicMock()
    mock_res.returncode = 1
    mock_subproc.return_value = mock_res

    result = run_preflight_quality_gates(skip_tests=True)

    assert result is False
    mock_subproc.assert_called_once()


@patch("subprocess.run")
def test_run_preflight_quality_gates_reaches_later_gates_when_earlier_pass(mock_subproc):
    """Gate 3 (node tests) fails after gates 1-2 pass, proving the
    function actually reaches and checks later gates rather than only
    ever evaluating the first one."""
    def _side_effect(cmd, *args, **kwargs):
        res = MagicMock()
        res.returncode = 1 if cmd[0] == "node" else 0
        return res
    mock_subproc.side_effect = _side_effect

    result = run_preflight_quality_gates(skip_tests=True)

    assert result is False
    # reachability (pass), 86-pillar (pass), node (fails here) -- gate 4
    # is skipped by skip_tests=True regardless, so 3 calls total.
    assert mock_subproc.call_count == 3
