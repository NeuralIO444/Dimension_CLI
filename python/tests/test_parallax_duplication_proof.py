# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_parallax_duplication_proof.py — Track A / Slice P4 (Step 4a proof).

Offline Layer-2 contract test using the real 195-layer, 46-comp Parallax production fixture
(`parallax_manifest.json` + `parallax_project_structure.json`).

Proves without needing live After Effects:
1. `ProjectStructureAnalyzer.shared_precomps()` identifies >= 1 shared precomps (12 in Parallax).
2. `build_mirror_rewires_spec` generates valid MirrorRewire entries for all 126 precomp wrappers.
3. `build_mirror_tree_spec` constructs all 46 mirror comp entries with preserved nested dims.
4. Full `run_conform` produces a `chunk_manifest.json` containing mirror_tree and mirror_rewires
   with zero missing-structure warnings.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.output_naming import build_mirror_rewires_spec, build_mirror_tree_spec
from core.project_structure_analyzer import ProjectStructureAnalyzer
from logic.preset_manager import PresetManager
from models.conformed_manifest import MirrorRewire, MirrorTreeEntry
from models.project_structure import ProjectStructure
from models.scrape_manifest import ScrapeManifest
from stages.conform import ConformConfig, run_conform


_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "session_2026_07_02"
_PARALLAX_MANIFEST = _FIXTURES_DIR / "parallax_manifest.json"
_PARALLAX_STRUCTURE = _FIXTURES_DIR / "parallax_project_structure.json"


@pytest.fixture
def parallax_data():
    if not _PARALLAX_MANIFEST.is_file() or not _PARALLAX_STRUCTURE.is_file():
        pytest.skip("Parallax fixture files missing in session_2026_07_02")
    manifest = ScrapeManifest.model_validate_json(_PARALLAX_MANIFEST.read_text(encoding="utf-8"))
    structure = ProjectStructure.model_validate_json(_PARALLAX_STRUCTURE.read_text(encoding="utf-8"))
    return manifest, structure


def test_parallax_structure_has_shared_precomps(parallax_data):
    """Step 4a requirement 1: shared_precomps() >= 1 on real project structure."""
    manifest, structure = parallax_data
    analyzer = ProjectStructureAnalyzer(structure)
    shared = analyzer.shared_precomps()
    assert len(shared) >= 1, "Parallax fixture must have >= 1 shared precomps"
    # Ground truth for Parallax: 12 shared precomps
    assert len(shared) == 12
    assert 1 in shared  # Comp ID 1 is the main shared asset


def test_parallax_mirror_rewires_spec_shape(parallax_data):
    """Step 4a requirement 2: build_mirror_rewires_spec produces valid MirrorRewire entries."""
    manifest, structure = parallax_data
    rewires = build_mirror_rewires_spec(manifest, structure)

    # Parallax has 126 wrapper layers across its comp tree
    assert len(rewires) == 126

    for rewire in rewires:
        assert isinstance(rewire, MirrorRewire)
        assert rewire.wrapper_layer_uid, "wrapper_layer_uid must not be empty"
        assert rewire.wrapper_layer_name, "wrapper_layer_name must not be empty"
        assert rewire.wrapper_in_source_comp_name, "wrapper_in_source_comp_name must not be empty"
        assert rewire.target_mirror_source_name, "target_mirror_source_name must not be empty"


def test_parallax_mirror_tree_spec_entries(parallax_data):
    """Step 4a requirement 3: build_mirror_tree_spec builds all 46 mirror comps."""
    manifest, structure = parallax_data
    preset = PresetManager().presets.get("tiktok_video") or PresetManager().presets.get("builtin:tiktok_video")

    entries = build_mirror_tree_spec(
        manifest=manifest,
        project_structure=structure,
        preset=preset,
        preserve_nested_dims=True,
    )

    # Parallax comp hierarchy has 46 comps total (1 root + 45 nested)
    assert len(entries) == 46
    root_entries = [e for e in entries if e.is_root]
    assert len(root_entries) == 1
    assert root_entries[0].source_comp_name == "Final Comp"

    for entry in entries:
        assert isinstance(entry, MirrorTreeEntry)
        assert entry.source_comp_name
        assert entry.output_name
        assert entry.source_comp_id > 0


def test_parallax_end_to_end_conform_with_structure(tmp_path, parallax_data):
    """Step 4a requirement 4: run_conform end-to-end generates populated mirror_tree and mirror_rewires."""
    manifest_src = tmp_path / "scrape_manifest.json"
    manifest_src.write_text(_PARALLAX_MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")
    structure_src = tmp_path / "project_structure.json"
    structure_src.write_text(_PARALLAX_STRUCTURE.read_text(encoding="utf-8"), encoding="utf-8")

    config = ConformConfig(
        source=str(manifest_src),
        preset="tiktok_video",
        allow_state_hash_bypass=True,
    )

    result = run_conform(config)
    assert result.chunk_manifest_path, "chunk_manifest_path must be populated"

    # Verify no missing-structure warnings occurred
    run_warnings = result.run_warnings
    assert not any("project_structure.json missing" in w for w in run_warnings)

    # Verify chunk_manifest.json has mirror_tree and mirror_rewires populated
    chunk_manifest_path = Path(result.chunk_manifest_path)
    assert chunk_manifest_path.is_file()
    chunk_manifest = json.loads(chunk_manifest_path.read_text(encoding="utf-8"))

    assert "mirror_tree" in chunk_manifest and chunk_manifest["mirror_tree"] is not None
    assert len(chunk_manifest["mirror_tree"]) == 46
    assert "mirror_rewires" in chunk_manifest and chunk_manifest["mirror_rewires"] is not None
    assert len(chunk_manifest["mirror_rewires"]) == 126
