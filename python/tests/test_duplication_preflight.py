# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_duplication_preflight.py
v5.8 — coverage for the qt_controller ↔ planner glue.

Tests the pure-Python helper that loads scrape_manifest.json +
project_structure.json, instantiates the planner, builds the default
plan, and returns either a session for the modal or None for the
legacy path.

The qt_controller's `_duplication_preflight()` UI method is exercised
indirectly: the helper is what determines fall-through, and the
modal-exec path is straight Qt event-loop code that's covered by
test_duplication_modal.py.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


# ── Helpers ──────────────────────────────────────────────────────────


def _write_manifest(path: Path, comp_name="ART_v14",
                     w=1920, h=1080, layers=()):
    """Atomic write of a minimal v5 scrape manifest."""
    payload = {
        "status": "OK",
        "project_info": {"name": comp_name, "width": w, "height": h,
                          "fps": 23.976},
        "layers": list(layers),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_structure(path: Path, comps, references):
    """Atomic write of a project_structure.json scan."""
    payload = {
        "status":         "OK",
        "schema_version": "1.0",
        "scan_meta": {
            "scanned_at":   "2026-04-25T15:00:00Z",
            "ae_version":   "24.0",
            "project_name": "test.aep",
        },
        "comps": [
            {"id": cid, "name": name, "width": w, "height": h,
             "fps": 23.976, "duration": 10.0, "pixel_aspect": 1.0,
             "bg_color": [0, 0, 0], "layer_count": 3,
             "is_render_target": False, "folder_path": ""}
            for (cid, name, w, h) in comps
        ],
        "references": [
            {"from_comp_id": f, "to_comp_id": t,
             "layer_index":  li, "layer_name": ln}
            for (f, t, li, ln) in references
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


# ── build_duplication_session ────────────────────────────────────────


class TestBuildDuplicationSession:
    def test_returns_none_when_structure_missing(self, tmp_path):
        """v5.7 not yet integrated → silent fall-through, legacy
        conform path runs unchanged."""
        from logic.duplication_preflight import build_duplication_session
        manifest = tmp_path / "scrape_manifest.json"
        _write_manifest(manifest)
        # No project_structure.json written.
        session = build_duplication_session(
            project_root=str(tmp_path),
            scrape_manifest_path=str(manifest),
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        )
        assert session is None

    def test_returns_none_when_no_shared_precomps(self, tmp_path):
        """Project structure scanned but no shared precomps → empty
        plan → fall-through."""
        from logic.duplication_preflight import build_duplication_session
        manifest = tmp_path / "scrape_manifest.json"
        structure = tmp_path / "project_structure.json"
        _write_manifest(manifest, comp_name="ART_v14")
        _write_structure(
            structure,
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "BG", 1920, 1080)],
            references=[(1, 2, 1, "bg")],
        )
        session = build_duplication_session(
            project_root=str(tmp_path),
            scrape_manifest_path=str(manifest),
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        )
        assert session is None

    def test_returns_session_when_shared_detected(self, tmp_path):
        from logic.duplication_preflight import build_duplication_session
        manifest = tmp_path / "scrape_manifest.json"
        structure = tmp_path / "project_structure.json"
        # Active comp ART_v14 has a layer pointing at Hero(3); Hero
        # is also used by ART_v15 → shared.
        _write_manifest(manifest, comp_name="ART_v14",
                        layers=[{
                            "index": 1, "name": "hero", "uid": "L1",
                            "parent_index": -1, "layer_kind": "av",
                            "source_item": {"kind": "comp",
                                            "name": "Hero", "id": 3,
                                            "nested_comp_id": 3},
                        }])
        _write_structure(
            structure,
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero", 1920, 1080)],
            references=[(1, 3, 1, "hero"), (2, 3, 1, "hero")],
        )
        session = build_duplication_session(
            project_root=str(tmp_path),
            scrape_manifest_path=str(manifest),
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        )
        assert session is not None
        assert session.active_comp_name == "ART_v14"
        assert len(session.default_plan.duplicates) == 1
        assert session.default_plan.duplicates[0].original_uid == "3"
        # Analyzer is wired with the parent-of map for tooltips.
        assert session.analyzer.parents_of(3) == {1, 2}

    def test_returns_none_when_manifest_corrupt(self, tmp_path):
        from logic.duplication_preflight import build_duplication_session
        manifest = tmp_path / "scrape_manifest.json"
        structure = tmp_path / "project_structure.json"
        manifest.write_text("not json {", encoding="utf-8")
        _write_structure(structure, comps=[], references=[])
        assert build_duplication_session(
            project_root=str(tmp_path),
            scrape_manifest_path=str(manifest),
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ) is None

    def test_returns_none_when_structure_corrupt(self, tmp_path):
        from logic.duplication_preflight import build_duplication_session
        manifest = tmp_path / "scrape_manifest.json"
        structure = tmp_path / "project_structure.json"
        _write_manifest(manifest)
        structure.write_text("not json {", encoding="utf-8")
        assert build_duplication_session(
            project_root=str(tmp_path),
            scrape_manifest_path=str(manifest),
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        ) is None

    def test_falls_back_to_project_root_structure_path(self, tmp_path):
        """Some sessions land project_structure.json at project root
        instead of the .dimension sibling — helper checks both."""
        from logic.duplication_preflight import build_duplication_session
        # Manifest in subdir, structure in project root.
        sub = tmp_path / ".dimension"
        sub.mkdir()
        manifest = sub / "scrape_manifest.json"
        structure = tmp_path / "project_structure.json"
        _write_manifest(manifest, comp_name="ART_v14",
                        layers=[{
                            "index": 1, "name": "hero", "uid": "L1",
                            "parent_index": -1, "layer_kind": "av",
                            "source_item": {"kind": "comp",
                                            "name": "Hero", "id": 3,
                                            "nested_comp_id": 3},
                        }])
        _write_structure(
            structure,
            comps=[(1, "ART_v14", 1920, 1080),
                   (2, "ART_v15", 1920, 1080),
                   (3, "Hero", 1920, 1080)],
            references=[(1, 3, 1, "h"), (2, 3, 1, "h")],
        )
        session = build_duplication_session(
            project_root=str(tmp_path),
            scrape_manifest_path=str(manifest),
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
        )
        assert session is not None


# ── Naming conventions ──────────────────────────────────────────────


class TestNamingConventions:
    def test_log_path_under_dotdimension(self, tmp_path):
        from logic.duplication_preflight import duplication_log_path_for
        path = duplication_log_path_for(str(tmp_path))
        assert path.endswith(".dimension/duplication_log.json")
        assert str(tmp_path) in path
