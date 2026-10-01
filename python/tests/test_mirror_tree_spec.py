# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_mirror_tree_spec.py — Slot 12.5 Stage D / Item 2.

Unit coverage for:
  - `MirrorTreeEntry` Pydantic model
  - `core.output_naming.build_mirror_tree_spec` — recursive-scrape→mirror-spec builder
  - `core.output_naming.session_bin_path` — target bin path convention
  - `exporter.PayloadSlicer.slice_and_export` integration — mirror_tree + target_bin_path
    flow into chunk_manifest.json

Scope: spec-build correctness, de-dup per Q3A, 87N flat regression
(N=1), legacy-5.0-manifest fallback, exporter wiring. JSX-side
mirror-comp creation lives in Babysitter and is verified via static
contract tests in test_babysitter_mirror_tree.py.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
))

from core.output_naming import (  # noqa: E402
    build_mirror_tree_spec,
    session_bin_path,
)
from logic.exporter import PayloadSlicer  # noqa: E402
from models.conformed_manifest import MirrorTreeEntry  # noqa: E402
from models.project_structure import (  # noqa: E402
    CompNode,
    ProjectStructure,
    ScanMeta,
)
from models.scrape_manifest import ScrapeManifest  # noqa: E402
from models.target import Target  # noqa: E402


def _target(label="TikTok Vertical", template="{source}_D", width=1080, height=1920):
    return Target(
        id="test:tiktok",
        label=label,
        category="social",
        subcategory="tiktok",
        width=width,
        height=height,
        aspect_ratio=width / height,
        aspect_label="9:16",
        source="builtin",
        output_name_template=template,
    )


def _structure(comps):
    """comps: list of (id, name, width, height)."""
    return ProjectStructure(
        status="OK",
        schema_version="1.0",
        scan_meta=ScanMeta(scanned_at="2026-05-17T12:00:00Z"),
        comps=[
            CompNode(
                id=cid, name=name, width=w, height=h,
                fps=30.0, duration=4.0, pixel_aspect=1.0,
                bg_color=[0, 0, 0], layer_count=1,
                is_render_target=False, folder_path="",
            )
            for (cid, name, w, h) in comps
        ],
        references=[],
    )


def _manifest_recursive(active_name, active_w, active_h, layers):
    """layers: list of dicts {index, name, uid, containing_comp_id, ...}.
    Builds a schema-5.1 manifest with Stage B's recursive-scrape
    breadcrumb fields populated."""
    layer_dicts = []
    for L in layers:
        d = {
            "index": L["index"], "name": L["name"], "uid": L["uid"],
            "parent_index": L.get("parent_index", -1),
            "layer_kind": "av",
        }
        if "containing_comp_id" in L:
            d["containing_comp_id"] = L["containing_comp_id"]
        layer_dicts.append(d)
    return ScrapeManifest.model_validate({
        "status": "OK",
        "project_info": {
            "name": active_name, "width": active_w,
            "height": active_h, "fps": 30.0,
        },
        "scrape_meta": {"schema_version": "5.1"},
        "layers": layer_dicts,
    })


# ---------------------------------------------------------------------------
# 1. MirrorTreeEntry schema
# ---------------------------------------------------------------------------

class TestMirrorTreeEntrySchema:
    def test_minimum_fields(self):
        e = MirrorTreeEntry(source_comp_name="Hero", output_name="Hero_D")
        assert e.source_comp_name == "Hero"
        assert e.output_name == "Hero_D"
        assert e.name_version == 1
        assert e.is_root is False
        assert e.source_comp_id is None

    def test_round_trip_through_json(self):
        e = MirrorTreeEntry(
            source_comp_name="Precomp_A",
            source_comp_id=42,
            output_name="Precomp_A_D",
            name_version=2,
            is_root=False,
        )
        re = MirrorTreeEntry.model_validate_json(e.model_dump_json())
        assert re == e

    def test_empty_names_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            MirrorTreeEntry(source_comp_name="", output_name="X_D")
        with pytest.raises(ValidationError):
            MirrorTreeEntry(source_comp_name="X", output_name="")

    def test_name_version_must_be_at_least_one(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            MirrorTreeEntry(
                source_comp_name="X", output_name="X_D", name_version=0
            )


# ---------------------------------------------------------------------------
# 2. session_bin_path convention
# ---------------------------------------------------------------------------

class TestSessionBinPath:
    def test_canonical_form(self):
        path = session_bin_path("TIKTOK", "2026-05-17_120000")
        assert path == "From Dimensions/TikTok (2026-05-17 12:00)"

    def test_matches_duplication_planner_convention(self):
        """Mirror-tree bin path must agree with
        DuplicationPlanner.session_folder_name's convention so the v5.8
        duplication folder and the Stage D mirror tree land in the same
        named bin."""
        from datetime import datetime
        from core.duplication_planner import DuplicationPlanner
        from models.scrape_manifest import ScrapeManifest

        m = ScrapeManifest.model_validate({
            "status": "OK",
            "project_info": {"name": "Root", "width": 1920,
                              "height": 1080, "fps": 30.0},
            "layers": [],
        })
        ts = datetime(2026, 5, 17, 12, 0, 0)
        planner = DuplicationPlanner(
            structure=_structure([(1, "Root", 1920, 1080)]),
            manifest=m,
            target_dimensions=(1080, 1920),
            preset_id="TIKTOK",
            timestamp=ts,
        )
        # Both producers ship the same path shape.
        from_planner = planner.session_folder_name()
        from_resolver = session_bin_path("TIKTOK", ts.strftime("%Y-%m-%d_%H%M%S"))
        assert from_planner == from_resolver


# ---------------------------------------------------------------------------
# 3. build_mirror_tree_spec — recursive scrape → mirror entries
# ---------------------------------------------------------------------------

class TestBuildMirrorTreeSpec:
    def test_corpus_01_chain_produces_four_entries(self):
        """The Corpus_01 chain: Root_Omnibus → Precomp_A → Precomp_B →
        Precomp_C. Four unique comps; four mirror entries. Root marked
        is_root; nested entries not. BFS order preserved."""
        structure = _structure([
            (100, "Root_Omnibus", 1920, 1080),
            (200, "Precomp_A",    1920, 1080),
            (300, "Precomp_B",    1920, 1080),
            (400, "Precomp_C",    1920, 1080),
        ])
        manifest = _manifest_recursive(
            "Root_Omnibus", 1920, 1080,
            [
                {"index": 1, "name": "Null",    "uid": "u_null",
                 "containing_comp_id": 100},
                {"index": 2, "name": "Wrapper", "uid": "u_wrap",
                 "containing_comp_id": 100},
                {"index": 1, "name": "A_FX",    "uid": "u_fx",
                 "containing_comp_id": 200},
                {"index": 2, "name": "B_link",  "uid": "u_blink",
                 "containing_comp_id": 200},
                {"index": 1, "name": "Matte",   "uid": "u_matte",
                 "containing_comp_id": 300},
                {"index": 1, "name": "HERO",    "uid": "u_hero",
                 "containing_comp_id": 400},
            ],
        )
        entries = build_mirror_tree_spec(manifest, structure, _target())
        assert len(entries) == 4
        # BFS order: root first, then nested per scrape walk.
        assert [e.source_comp_name for e in entries] == [
            "Root_Omnibus", "Precomp_A", "Precomp_B", "Precomp_C",
        ]
        # Default template → `_D` suffix on every entry.
        assert [e.output_name for e in entries] == [
            "Root_Omnibus_D", "Precomp_A_D", "Precomp_B_D", "Precomp_C_D",
        ]
        # Exactly one is_root entry — the active comp.
        assert sum(1 for e in entries if e.is_root) == 1
        assert entries[0].is_root is True
        for e in entries[1:]:
            assert e.is_root is False

    def test_dedup_per_q3a_shared_precomp_one_entry(self):
        """A precomp referenced N times in the manifest produces ONE
        mirror entry, not N. Q3A locked decision: shared precomps
        share one conformed copy."""
        structure = _structure([
            (100, "Root", 1920, 1080),
            (200, "Element_01", 1920, 1080),
        ])
        manifest = _manifest_recursive(
            "Root", 1920, 1080,
            [
                {"index": 1, "name": "ref_a", "uid": "u_a",
                 "containing_comp_id": 100},
                {"index": 2, "name": "ref_b", "uid": "u_b",
                 "containing_comp_id": 100},
                {"index": 3, "name": "ref_c", "uid": "u_c",
                 "containing_comp_id": 100},
                # Element_01's inner layers — appears 5x in scrape
                # because shared. The walker emits them once per visit
                # but our manifest models a single visit (de-duped by
                # Stage B's recursive walker per Q3A).
                {"index": 1, "name": "inner", "uid": "u_inner",
                 "containing_comp_id": 200},
            ],
        )
        entries = build_mirror_tree_spec(manifest, structure, _target())
        assert len(entries) == 2
        assert [e.source_comp_name for e in entries] == ["Root", "Element_01"]

    def test_collision_bumps_version(self):
        """When a mirror's resolved name already exists in the project
        (e.g. a stale `Root_D` from a previous conform), the resolver
        bumps `_v2`."""
        structure = _structure([
            (100, "Root", 1920, 1080),
            (999, "Root_D", 1080, 1920),  # collision
        ])
        manifest = _manifest_recursive(
            "Root", 1920, 1080,
            [{"index": 1, "name": "L", "uid": "u", "containing_comp_id": 100}],
        )
        entries = build_mirror_tree_spec(manifest, structure, _target())
        assert len(entries) == 1
        assert entries[0].output_name == "Root_D_v2"
        assert entries[0].name_version == 2

    def test_legacy_5_0_manifest_falls_back_to_active_comp(self):
        """A schema-5.0 manifest has no containing_comp_id on any
        layer. The builder falls back to a single root entry for the
        active comp — preserves 87N flat regression (N=1 mirror)."""
        structure = _structure([(100, "ART_v14", 1920, 1080)])
        manifest = ScrapeManifest.model_validate({
            "status": "OK",
            "project_info": {"name": "ART_v14", "width": 1920,
                              "height": 1080, "fps": 30.0},
            "scrape_meta": {"schema_version": "5.0"},
            "layers": [
                {"index": 1, "name": "L1", "uid": "u1",
                 "parent_index": -1, "layer_kind": "av"},
                {"index": 2, "name": "L2", "uid": "u2",
                 "parent_index": -1, "layer_kind": "av"},
            ],
        })
        entries = build_mirror_tree_spec(manifest, structure, _target())
        assert len(entries) == 1
        assert entries[0].source_comp_name == "ART_v14"
        assert entries[0].is_root is True
        assert entries[0].output_name == "ART_v14_D"

    def test_missing_active_comp_returns_empty(self):
        """If the active comp's name doesn't appear in
        project_structure (corrupt sidecar), the builder returns an
        empty list. Babysitter falls back to legacy single-comp path."""
        structure = _structure([(100, "OtherComp", 1920, 1080)])
        manifest = ScrapeManifest.model_validate({
            "status": "OK",
            "project_info": {"name": "ART_v14", "width": 1920,
                              "height": 1080, "fps": 30.0},
            "layers": [{"index": 1, "name": "L1", "uid": "u1",
                        "parent_index": -1, "layer_kind": "av"}],
        })
        entries = build_mirror_tree_spec(manifest, structure, _target())
        assert entries == []

    def test_unknown_containing_comp_id_skipped_with_warning(self):
        """A layer carrying a containing_comp_id that's not in
        project_structure (sidecar/scrape drift) is skipped — the rest
        of the manifest still produces entries."""
        structure = _structure([
            (100, "Root", 1920, 1080),
            (200, "Precomp_A", 1920, 1080),
        ])
        manifest = _manifest_recursive(
            "Root", 1920, 1080,
            [
                {"index": 1, "name": "L1", "uid": "u1",
                 "containing_comp_id": 100},
                {"index": 1, "name": "L2", "uid": "u2",
                 "containing_comp_id": 999},   # not in structure
                {"index": 1, "name": "L3", "uid": "u3",
                 "containing_comp_id": 200},
            ],
        )
        entries = build_mirror_tree_spec(manifest, structure, _target())
        # Root + Precomp_A → 2 entries; comp 999 skipped.
        names = [e.source_comp_name for e in entries]
        assert names == ["Root", "Precomp_A"]

    def test_custom_template_applies_to_every_entry(self):
        structure = _structure([
            (100, "Root", 1920, 1080),
            (200, "Precomp_A", 1920, 1080),
        ])
        manifest = _manifest_recursive(
            "Root", 1920, 1080,
            [
                {"index": 1, "name": "L", "uid": "uR",
                 "containing_comp_id": 100},
                {"index": 1, "name": "L", "uid": "uA",
                 "containing_comp_id": 200},
            ],
        )
        preset = _target(template="{source}_TT_{width}x{height}")
        entries = build_mirror_tree_spec(manifest, structure, preset)
        assert entries[0].output_name == "Root_TT_1080x1920"
        assert entries[1].output_name == "Precomp_A_TT_1080x1920"

    def test_guide_precomp_excluded_from_mirror_tree(self):
        """A GUIDE-tagged wrapper layer whose source is nested_comp_id=300
        must not appear in the mirror tree output. Layers scraped from
        inside comp 300 are also excluded because that comp is filtered."""
        structure = _structure([
            (100, "Root", 1920, 1080),
            (200, "Precomp_A", 1920, 1080),
            (300, "CheckersAndMattes", 1920, 1080),  # GUIDE overlay
        ])
        manifest = ScrapeManifest.model_validate({
            "status": "OK",
            "project_info": {"name": "Root", "width": 1920,
                              "height": 1080, "fps": 30.0},
            "scrape_meta": {"schema_version": "5.1"},
            "layers": [
                # Regular content layer in Root
                {
                    "index": 1, "name": "Content",
                    "uid": "u_content", "parent_index": -1,
                    "layer_kind": "av", "containing_comp_id": 100,
                },
                # Normal precomp wrapper in Root → comp 200
                {
                    "index": 2, "name": "Wrapper_A",
                    "uid": "u_wA", "parent_index": -1,
                    "layer_kind": "av", "containing_comp_id": 100,
                    "source_item": {
                        "kind": "comp", "name": "Precomp_A",
                        "id": 200, "nested_comp_id": 200,
                    },
                },
                # GUIDE-tagged wrapper in Root → comp 300
                {
                    "index": 3, "name": "GUIDE_Overlay",
                    "uid": "u_guide", "parent_index": -1,
                    "layer_kind": "av", "containing_comp_id": 100,
                    "content_tag": "GUIDE",
                    "source_item": {
                        "kind": "comp", "name": "CheckersAndMattes",
                        "id": 300, "nested_comp_id": 300,
                    },
                },
                # Inner layer from comp 200 (should appear)
                {
                    "index": 1, "name": "A_inner",
                    "uid": "u_ai", "parent_index": -1,
                    "layer_kind": "av", "containing_comp_id": 200,
                },
                # Inner layer from comp 300 (should be excluded)
                {
                    "index": 1, "name": "Guide_inner",
                    "uid": "u_gi", "parent_index": -1,
                    "layer_kind": "av", "containing_comp_id": 300,
                },
            ],
        })
        entries = build_mirror_tree_spec(manifest, structure, _target())
        comp_names = [e.source_comp_name for e in entries]
        assert "CheckersAndMattes" not in comp_names
        # Root and Precomp_A must still be present
        assert "Root" in comp_names
        assert "Precomp_A" in comp_names
        assert len(entries) == 2

    def test_protect_precomp_excluded_from_mirror_tree(self):
        """Same exclusion applies when the wrapper tag is PROTECT instead
        of GUIDE."""
        structure = _structure([
            (100, "Root", 1920, 1080),
            (200, "ProtectedOverlay", 1920, 1080),
        ])
        manifest = ScrapeManifest.model_validate({
            "status": "OK",
            "project_info": {"name": "Root", "width": 1920,
                              "height": 1080, "fps": 30.0},
            "scrape_meta": {"schema_version": "5.1"},
            "layers": [
                {
                    "index": 1, "name": "Content",
                    "uid": "u_c", "parent_index": -1,
                    "layer_kind": "av", "containing_comp_id": 100,
                },
                {
                    "index": 2, "name": "Protect_Wrapper",
                    "uid": "u_p", "parent_index": -1,
                    "layer_kind": "av", "containing_comp_id": 100,
                    "content_tag": "PROTECT",
                    "source_item": {
                        "kind": "comp", "name": "ProtectedOverlay",
                        "id": 200, "nested_comp_id": 200,
                    },
                },
                {
                    "index": 1, "name": "Protect_inner",
                    "uid": "u_pi", "parent_index": -1,
                    "layer_kind": "av", "containing_comp_id": 200,
                },
            ],
        })
        entries = build_mirror_tree_spec(manifest, structure, _target())
        comp_names = [e.source_comp_name for e in entries]
        assert "ProtectedOverlay" not in comp_names
        assert "Root" in comp_names
        assert len(entries) == 1


# ---------------------------------------------------------------------------
# 4. PayloadSlicer integration — mirror_tree flows into chunk_manifest.json
# ---------------------------------------------------------------------------

class TestExporterMirrorTreeIntegration:
    def _minimal_conformed_layer(self, idx, name="L"):
        # Slot 7.5 Phase 3 — `skip_inject` is part of ConformedTransforms.
        return {
            "index": idx, "name": name, "uid": f"u_{idx}",
            "parent_index": -1, "layer_kind": "av",
            "position": [0, 0, 0], "scale": [100, 100, 100],
            "rotation_z": 0, "anchor": [0, 0, 0],
            "conformed_transforms": {
                "position": [0, 0, 0], "scale": [100, 100, 100],
                "rotation": 0, "anchor": [0, 0, 0],
                "is_root": True,
            },
        }

    def _run_slicer(self, *, mirror_tree=None, target_bin_path=""):
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                slicer = PayloadSlicer(output_dir=os.path.join(tmpdir, "Chunks"))
                path = slicer.slice_and_export(
                    [self._minimal_conformed_layer(1)],
                    expected_comp_name="Root",
                    target_width=1080,
                    target_height=1920,
                    preset_label="TikTok",
                    mirror_tree=mirror_tree,
                    target_bin_path=target_bin_path,
                )
                with open(path) as f:
                    return json.load(f)
            finally:
                os.chdir(cwd)

    def test_chunk_manifest_omits_mirror_tree_when_absent(self):
        """Back-compat: a pre-Stage-D call (no mirror_tree, no bin path)
        produces a chunk_manifest without those keys. Babysitter falls
        back to the legacy single-comp setupWorkspace path."""
        data = self._run_slicer(mirror_tree=None, target_bin_path="")
        assert "mirror_tree" not in data
        assert "target_bin_path" not in data

    def test_chunk_manifest_carries_mirror_tree_when_present(self):
        entries = [
            MirrorTreeEntry(source_comp_name="Root", output_name="Root_D",
                            is_root=True),
            MirrorTreeEntry(source_comp_name="Precomp_A",
                            output_name="Precomp_A_D"),
        ]
        data = self._run_slicer(
            mirror_tree=entries,
            target_bin_path="From Dimensions/TIKTOK_2026-05-17_120000",
        )
        assert "mirror_tree" in data
        assert "target_bin_path" in data
        assert data["target_bin_path"] == "From Dimensions/TIKTOK_2026-05-17_120000"
        # Two entries, in order, with the expected names + is_root flags.
        assert len(data["mirror_tree"]) == 2
        assert data["mirror_tree"][0]["source_comp_name"] == "Root"
        assert data["mirror_tree"][0]["output_name"] == "Root_D"
        assert data["mirror_tree"][0]["is_root"] is True
        assert data["mirror_tree"][1]["source_comp_name"] == "Precomp_A"
        assert data["mirror_tree"][1]["is_root"] is False

    def test_chunk_manifest_omits_mirror_tree_when_empty_list(self):
        """Empty list → omit. Same outcome as None for the on-disk
        contract; Babysitter sees no `mirror_tree` key and falls back."""
        data = self._run_slicer(mirror_tree=[], target_bin_path="")
        assert "mirror_tree" not in data
