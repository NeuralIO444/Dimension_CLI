# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_missing_structure_warning.py — Slice P3 / Edge E10.

Verifies that when `project_structure.json` is missing for a composition with
precomp wrapper layers, conform emits a loud run warning (Python / report / UI)
instead of silently skipping mirror tree and rewire construction.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.scale_engine import ScaleEngine
from models.scrape_manifest import ScrapeManifest
from stages.conform_passes import build_mirror_tree_spec


_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "session_2026_07_02"
_PARALLAX_MANIFEST_PATH = _FIXTURES / "parallax_manifest.json"


def _make_dummy_engine(manifest: ScrapeManifest) -> ScaleEngine:
    return ScaleEngine(
        manifest,
        1080,
        1920,
        "fit",
        0.0,
    )


def test_missing_structure_flat_comp_no_warning(tmp_path):
    """A flat composition with 0 wrapper layers produces no warning when structure is missing."""
    manifest_data = {
        "status": "OK",
        "schema_version": 5,
        "project_info": {"name": "Flat Comp", "width": 1920, "height": 1080},
        "layers": [
            {
                "uid": "solid_1",
                "name": "Background",
                "index": 1,
                "layer_type": "AVLayer",
                "source_item": {"kind": "solid", "name": "Dark Grey Solid 1"},
                "transform": {},
            },
            {
                "uid": "text_1",
                "name": "Title",
                "index": 2,
                "layer_type": "TextLayer",
                "source_item": None,
                "transform": {},
            },
        ],
    }
    manifest_file = tmp_path / "scrape_manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    manifest = ScrapeManifest.model_validate(manifest_data)
    engine = _make_dummy_engine(manifest)
    run_warnings: list[str] = []

    def _warn(msg: str, lst: list[str]) -> None:
        lst.append(msg)

    spec = build_mirror_tree_spec(
        str(manifest_file),
        manifest,
        engine,
        preset="tiktok_video",
        preset_label="TikTok",
        target_w=1080,
        target_h=1920,
        scale_mode="fit",
        bleed_pct=0.0,
        run_warnings=run_warnings,
        emit_warning=_warn,
    )

    assert spec.mirror_tree == []
    assert spec.mirror_rewires == []
    assert len(run_warnings) == 0


def test_missing_structure_with_precomp_wrappers_emits_loud_warning(tmp_path):
    """When precomp wrappers exist and project_structure.json is missing, a loud warning is emitted."""
    manifest_data = {
        "status": "OK",
        "schema_version": 5,
        "project_info": {"name": "Main Comp", "width": 1920, "height": 1080},
        "layers": [
            {
                "uid": "precomp_1",
                "name": "Nested Lower Third",
                "index": 1,
                "layer_type": "AVLayer",
                "content_tag": "TYPE",
                "source_item": {
                    "kind": "comp",
                    "id": 101,
                    "name": "Lower Third Comp",
                    "nested_comp_id": 101,
                },
                "transform": {},
            },
            {
                "uid": "precomp_2",
                "name": "Bug Precomp",
                "index": 2,
                "layer_type": "AVLayer",
                "content_tag": "BUG",
                "source_item": {
                    "kind": "comp",
                    "id": 102,
                    "name": "Network Bug",
                    "nested_comp_id": 102,
                },
                "transform": {},
            },
        ],
    }
    manifest_file = tmp_path / "scrape_manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    manifest = ScrapeManifest.model_validate(manifest_data)
    engine = _make_dummy_engine(manifest)
    run_warnings: list[str] = []

    def _warn(msg: str, lst: list[str]) -> None:
        lst.append(msg)

    spec = build_mirror_tree_spec(
        str(manifest_file),
        manifest,
        engine,
        preset="tiktok_video",
        preset_label="TikTok",
        target_w=1080,
        target_h=1920,
        scale_mode="fit",
        bleed_pct=0.0,
        run_warnings=run_warnings,
        emit_warning=_warn,
    )

    assert spec.mirror_tree == []
    assert len(run_warnings) == 1
    assert "Mirror tree skipped — project_structure.json missing" in run_warnings[0]
    assert "2 precomp wrapper(s)" in run_warnings[0]


def test_missing_structure_excluded_tags_not_counted(tmp_path):
    """GUIDE and PROTECT wrapper layers do not trigger the missing structure warning."""
    manifest_data = {
        "status": "OK",
        "schema_version": 5,
        "project_info": {"name": "Overlay Comp", "width": 1920, "height": 1080},
        "layers": [
            {
                "uid": "guide_layer",
                "name": "Action Safe Guide",
                "index": 1,
                "layer_type": "AVLayer",
                "content_tag": "GUIDE",
                "source_item": {
                    "kind": "comp",
                    "id": 201,
                    "name": "Safe Zone Overlay",
                },
                "transform": {},
            },
            {
                "uid": "protect_layer",
                "name": "Protected Logo",
                "index": 2,
                "layer_type": "AVLayer",
                "content_tag": "PROTECT",
                "source_item": {
                    "kind": "comp",
                    "id": 202,
                    "name": "Logo Lockup",
                },
                "transform": {},
            },
        ],
    }
    manifest_file = tmp_path / "scrape_manifest.json"
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")

    manifest = ScrapeManifest.model_validate(manifest_data)
    engine = _make_dummy_engine(manifest)
    run_warnings: list[str] = []

    def _warn(msg: str, lst: list[str]) -> None:
        lst.append(msg)

    spec = build_mirror_tree_spec(
        str(manifest_file),
        manifest,
        engine,
        preset="tiktok_video",
        preset_label="TikTok",
        target_w=1080,
        target_h=1920,
        scale_mode="fit",
        bleed_pct=0.0,
        run_warnings=run_warnings,
        emit_warning=_warn,
    )

    assert spec.mirror_tree == []
    assert len(run_warnings) == 0


def test_missing_structure_on_real_parallax_fixture(tmp_path):
    """Parallax fixture without project_structure.json triggers the warning with 126 wrappers."""
    if not _PARALLAX_MANIFEST_PATH.is_file():
        pytest.skip("parallax_manifest.json fixture not found")

    target_manifest = tmp_path / "scrape_manifest.json"
    target_manifest.write_text(_PARALLAX_MANIFEST_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    manifest = ScrapeManifest.model_validate_json(target_manifest.read_text(encoding="utf-8"))
    engine = _make_dummy_engine(manifest)
    run_warnings: list[str] = []

    def _warn(msg: str, lst: list[str]) -> None:
        lst.append(msg)

    spec = build_mirror_tree_spec(
        str(target_manifest),
        manifest,
        engine,
        preset="tiktok_video",
        preset_label="TikTok",
        target_w=1080,
        target_h=1920,
        scale_mode="fit",
        bleed_pct=0.0,
        run_warnings=run_warnings,
        emit_warning=_warn,
    )

    assert spec.mirror_tree == []
    assert len(run_warnings) == 1
    assert "126 precomp wrapper(s)" in run_warnings[0]
