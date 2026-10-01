# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_mirror_rewires_spec.py — Slot 12.5 Stage D / Item 3.

Unit coverage for:
  - `MirrorRewire` Pydantic model
  - `core.output_naming.build_mirror_rewires_spec` — recursive-scrape→rewire-spec builder
  - `exporter.PayloadSlicer.slice_and_export` integration — mirror_rewires
    field flows into chunk_manifest.json

Scope: spec-build correctness, Q3A default, 87N flat regression
(N=0 rewires — no precomp wrappers), legacy-5.0 fallback,
missing-source-comp handling. JSX-side rewire execution lives in
Babysitter and is verified via static contract tests in
test_babysitter_mirror_rewires.py.
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

from core.output_naming import build_mirror_rewires_spec  # noqa: E402
from logic.exporter import PayloadSlicer  # noqa: E402
from models.conformed_manifest import MirrorRewire  # noqa: E402
from models.project_structure import (  # noqa: E402
    CompNode,
    ProjectStructure,
    ScanMeta,
)
from models.scrape_manifest import ScrapeManifest  # noqa: E402


def _structure(comps):
    return ProjectStructure(
        status="OK",
        schema_version="1.0",
        scan_meta=ScanMeta(scanned_at="2026-05-17T13:00:00Z"),
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
    """layers: list of dicts with optional source_item (precomp wrapper)
    and containing_comp_id (Stage B v5.1)."""
    layer_dicts = []
    for L in layers:
        d = {
            "index": L["index"], "name": L["name"], "uid": L["uid"],
            "parent_index": L.get("parent_index", -1),
            "layer_kind": "av",
        }
        if "containing_comp_id" in L:
            d["containing_comp_id"] = L["containing_comp_id"]
        if "source_comp_id" in L:
            # Build a comp-kind source_item so the rewire builder sees
            # this as a precomp wrapper.
            d["source_item"] = {
                "kind": "comp",
                "name": f"src_{L['source_comp_id']}",
                "id": L["source_comp_id"],
                "nested_comp_id": L["source_comp_id"],
            }
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
# 1. MirrorRewire schema
# ---------------------------------------------------------------------------

class TestMirrorRewireSchema:
    def test_minimum_fields(self):
        r = MirrorRewire(
            wrapper_layer_uid="u_wrap",
            wrapper_in_source_comp_name="Root",
            target_mirror_source_name="Precomp_A",
        )
        assert r.wrapper_layer_uid == "u_wrap"
        assert r.wrapper_in_source_comp_name == "Root"
        assert r.target_mirror_source_name == "Precomp_A"
        # Q3A default — no fork.
        assert r.fork_consumer_layer_uid is None
        assert r.wrapper_layer_name is None

    def test_q3b_fork_field_carries_consumer_uid(self):
        r = MirrorRewire(
            wrapper_layer_uid="u_wrap",
            wrapper_in_source_comp_name="Root",
            target_mirror_source_name="Element_01",
            fork_consumer_layer_uid="u_consumer_xyz",
        )
        assert r.fork_consumer_layer_uid == "u_consumer_xyz"

    def test_round_trip_through_json(self):
        r = MirrorRewire(
            wrapper_layer_uid="u",
            wrapper_layer_name="wrap",
            wrapper_in_source_comp_name="Root",
            target_mirror_source_name="Precomp_A",
        )
        rr = MirrorRewire.model_validate_json(r.model_dump_json())
        assert rr == r

    def test_empty_strings_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            MirrorRewire(
                wrapper_layer_uid="",
                wrapper_in_source_comp_name="Root",
                target_mirror_source_name="A",
            )
        with pytest.raises(ValidationError):
            MirrorRewire(
                wrapper_layer_uid="u",
                wrapper_in_source_comp_name="",
                target_mirror_source_name="A",
            )
        with pytest.raises(ValidationError):
            MirrorRewire(
                wrapper_layer_uid="u",
                wrapper_in_source_comp_name="Root",
                target_mirror_source_name="",
            )


# ---------------------------------------------------------------------------
# 2. build_mirror_rewires_spec — happy path
# ---------------------------------------------------------------------------

class TestBuildMirrorRewiresSpec:
    def test_corpus_01_chain_emits_three_rewires(self):
        """The Corpus_01 chain Root_Omnibus → Precomp_A → Precomp_B →
        Precomp_C has three precomp wrappers (Root→A, A→B, B→C).
        Each emits one rewire."""
        structure = _structure([
            (100, "Root_Omnibus", 1920, 1080),
            (200, "Precomp_A",    1920, 1080),
            (300, "Precomp_B",    1920, 1080),
            (400, "Precomp_C",    1920, 1080),
        ])
        manifest = _manifest_recursive(
            "Root_Omnibus", 1920, 1080,
            [
                {"index": 1, "name": "Wrapper_A",   "uid": "u_wA",
                 "containing_comp_id": 100, "source_comp_id": 200},
                {"index": 1, "name": "Wrapper_B",   "uid": "u_wB",
                 "containing_comp_id": 200, "source_comp_id": 300},
                {"index": 1, "name": "Wrapper_C",   "uid": "u_wC",
                 "containing_comp_id": 300, "source_comp_id": 400},
                # Leaf layers in Precomp_C — no source, no rewire.
                {"index": 2, "name": "HERO_Logo",   "uid": "u_hero",
                 "containing_comp_id": 400},
            ],
        )
        rewires = build_mirror_rewires_spec(manifest, structure)
        assert len(rewires) == 3
        # Each rewire connects (containing comp → target source comp).
        triples = [
            (r.wrapper_layer_uid, r.wrapper_in_source_comp_name,
             r.target_mirror_source_name)
            for r in rewires
        ]
        assert triples == [
            ("u_wA", "Root_Omnibus", "Precomp_A"),
            ("u_wB", "Precomp_A",    "Precomp_B"),
            ("u_wC", "Precomp_B",    "Precomp_C"),
        ]
        # All Q3A default — no forks.
        for r in rewires:
            assert r.fork_consumer_layer_uid is None
        # Wrapper names carried for fallback lookup.
        assert rewires[0].wrapper_layer_name == "Wrapper_A"

    def test_shared_precomp_emits_one_rewire_per_reference(self):
        """A precomp referenced N times produces N rewires — every
        consumer wrapper rewires independently. Item 3 is Q3A default,
        all pointing at the SHARED mirror."""
        structure = _structure([
            (100, "Root", 1920, 1080),
            (200, "Element_01", 1920, 1080),
        ])
        manifest = _manifest_recursive(
            "Root", 1920, 1080,
            [
                {"index": 1, "name": "ref_a", "uid": "u_a",
                 "containing_comp_id": 100, "source_comp_id": 200},
                {"index": 2, "name": "ref_b", "uid": "u_b",
                 "containing_comp_id": 100, "source_comp_id": 200},
                {"index": 3, "name": "ref_c", "uid": "u_c",
                 "containing_comp_id": 100, "source_comp_id": 200},
            ],
        )
        rewires = build_mirror_rewires_spec(manifest, structure)
        assert len(rewires) == 3
        # All three rewire to the same Q3A shared mirror name.
        assert all(r.target_mirror_source_name == "Element_01" for r in rewires)
        # Each wrapper UID surfaces — Babysitter will find them by uid
        # in the same containing mirror.
        uids = sorted(r.wrapper_layer_uid for r in rewires)
        assert uids == ["u_a", "u_b", "u_c"]

    def test_legacy_5_0_manifest_uses_active_comp_as_containing(self):
        """A schema-5.0 manifest has no containing_comp_id. The builder
        falls back to the active comp name (single mirror in the
        legacy single-comp setup)."""
        structure = _structure([
            (100, "ART_v14", 1920, 1080),
            (200, "Hero", 1920, 1080),
        ])
        manifest = ScrapeManifest.model_validate({
            "status": "OK",
            "project_info": {"name": "ART_v14", "width": 1920,
                              "height": 1080, "fps": 30.0},
            "scrape_meta": {"schema_version": "5.0"},
            "layers": [
                {
                    "index": 1, "name": "ref_hero", "uid": "u_h",
                    "parent_index": -1, "layer_kind": "av",
                    "source_item": {
                        "kind": "comp", "name": "src_200",
                        "id": 200, "nested_comp_id": 200,
                    },
                },
            ],
        })
        rewires = build_mirror_rewires_spec(manifest, structure)
        assert len(rewires) == 1
        assert rewires[0].wrapper_in_source_comp_name == "ART_v14"
        assert rewires[0].target_mirror_source_name == "Hero"

    def test_no_precomp_wrappers_returns_empty_list(self):
        """The 87N flat case: a manifest with only footage/solid
        layers and no precomp wrappers produces zero rewires. The
        rewire phase will no-op and flat conform stays unchanged."""
        structure = _structure([(100, "87N_Flat", 1920, 1080)])
        manifest = _manifest_recursive(
            "87N_Flat", 1920, 1080,
            [
                {"index": 1, "name": "Solid_1", "uid": "u_s1",
                 "containing_comp_id": 100},   # no source_comp_id
                {"index": 2, "name": "Solid_2", "uid": "u_s2",
                 "containing_comp_id": 100},
            ],
        )
        rewires = build_mirror_rewires_spec(manifest, structure)
        assert rewires == []


# ---------------------------------------------------------------------------
# 3. build_mirror_rewires_spec — edge cases
# ---------------------------------------------------------------------------

class TestBuildMirrorRewiresEdgeCases:
    def test_wrapper_without_uid_skipped(self):
        """Babysitter's rewire phase anchors on findLayerByUID. A
        wrapper without a UID can't be anchored — skipped with a
        warning log, the rest of the manifest still produces rewires."""
        structure = _structure([
            (100, "Root", 1920, 1080),
            (200, "Precomp_A", 1920, 1080),
        ])
        manifest = ScrapeManifest.model_validate({
            "status": "OK",
            "project_info": {"name": "Root", "width": 1920,
                              "height": 1080, "fps": 30.0},
            "scrape_meta": {"schema_version": "5.1"},
            "layers": [
                {
                    "index": 1, "name": "no_uid_wrapper",
                    "uid": None,  # no UID
                    "parent_index": -1, "layer_kind": "av",
                    "containing_comp_id": 100,
                    "source_item": {"kind": "comp", "name": "src",
                                      "id": 200, "nested_comp_id": 200},
                },
                {
                    "index": 2, "name": "has_uid_wrapper",
                    "uid": "u_has",
                    "parent_index": -1, "layer_kind": "av",
                    "containing_comp_id": 100,
                    "source_item": {"kind": "comp", "name": "src",
                                      "id": 200, "nested_comp_id": 200},
                },
            ],
        })
        rewires = build_mirror_rewires_spec(manifest, structure)
        # Only the uid-bearing wrapper produced a rewire.
        assert len(rewires) == 1
        assert rewires[0].wrapper_layer_uid == "u_has"

    def test_missing_target_comp_in_structure_skipped(self):
        """If a wrapper's source comp isn't in project_structure
        (deleted, external, or scrape/structure drift), the rewire is
        skipped with a warning — the rest of the manifest still ships."""
        structure = _structure([
            (100, "Root", 1920, 1080),
            (200, "Present_Comp", 1920, 1080),
            # 999 deliberately absent
        ])
        manifest = _manifest_recursive(
            "Root", 1920, 1080,
            [
                {"index": 1, "name": "ok",      "uid": "u_ok",
                 "containing_comp_id": 100, "source_comp_id": 200},
                {"index": 2, "name": "missing", "uid": "u_miss",
                 "containing_comp_id": 100, "source_comp_id": 999},
            ],
        )
        rewires = build_mirror_rewires_spec(manifest, structure)
        assert len(rewires) == 1
        assert rewires[0].wrapper_layer_uid == "u_ok"

    def test_missing_containing_comp_in_structure_skipped(self):
        """Symmetric edge case: wrapper's CONTAINING comp not in
        project_structure (corrupt sidecar). Skipped, others ship."""
        structure = _structure([
            (100, "Root", 1920, 1080),
            (200, "Precomp_A", 1920, 1080),
            # 999 deliberately absent
        ])
        manifest = _manifest_recursive(
            "Root", 1920, 1080,
            [
                {"index": 1, "name": "ok",      "uid": "u_ok",
                 "containing_comp_id": 100, "source_comp_id": 200},
                {"index": 2, "name": "orphan",  "uid": "u_orph",
                 "containing_comp_id": 999, "source_comp_id": 200},
            ],
        )
        rewires = build_mirror_rewires_spec(manifest, structure)
        assert len(rewires) == 1
        assert rewires[0].wrapper_layer_uid == "u_ok"

    def test_guide_wrapper_excluded_from_rewires(self):
        """A wrapper layer tagged GUIDE must not appear in the rewires
        spec. Non-GUIDE wrappers in the same manifest still emit rewires."""
        structure = _structure([
            (100, "Root", 1920, 1080),
            (200, "Precomp_A", 1920, 1080),
            (300, "CheckersAndMattes", 1920, 1080),  # GUIDE — excluded
        ])
        manifest = ScrapeManifest.model_validate({
            "status": "OK",
            "project_info": {"name": "Root", "width": 1920,
                              "height": 1080, "fps": 30.0},
            "scrape_meta": {"schema_version": "5.1"},
            "layers": [
                # Normal precomp wrapper → should produce a rewire
                {
                    "index": 1, "name": "Wrapper_A",
                    "uid": "u_wA", "parent_index": -1,
                    "layer_kind": "av", "containing_comp_id": 100,
                    "source_item": {
                        "kind": "comp", "name": "Precomp_A",
                        "id": 200, "nested_comp_id": 200,
                    },
                },
                # GUIDE-tagged wrapper → must NOT produce a rewire
                {
                    "index": 2, "name": "GUIDE_Overlay",
                    "uid": "u_guide", "parent_index": -1,
                    "layer_kind": "av", "containing_comp_id": 100,
                    "content_tag": "GUIDE",
                    "source_item": {
                        "kind": "comp", "name": "CheckersAndMattes",
                        "id": 300, "nested_comp_id": 300,
                    },
                },
            ],
        })
        rewires = build_mirror_rewires_spec(manifest, structure)
        assert len(rewires) == 1
        assert rewires[0].wrapper_layer_uid == "u_wA"
        assert rewires[0].target_mirror_source_name == "Precomp_A"
        # Confirm the GUIDE wrapper is absent
        uids = [r.wrapper_layer_uid for r in rewires]
        assert "u_guide" not in uids

    def test_footage_and_solid_layers_dont_emit_rewires(self):
        """Only `source_item.kind == 'comp'` produces a rewire. Footage
        / solid / null sources don't get one — they don't have a
        target-dimensioned mirror to rewire to."""
        structure = _structure([(100, "Root", 1920, 1080)])
        manifest = ScrapeManifest.model_validate({
            "status": "OK",
            "project_info": {"name": "Root", "width": 1920,
                              "height": 1080, "fps": 30.0},
            "scrape_meta": {"schema_version": "5.1"},
            "layers": [
                {
                    "index": 1, "name": "footage_layer", "uid": "u_f",
                    "parent_index": -1, "layer_kind": "av",
                    "containing_comp_id": 100,
                    "source_item": {
                        "kind": "footage", "name": "footage.mov",
                        "id": 500, "file_path": "/path/to/footage.mov",
                    },
                },
                {
                    "index": 2, "name": "solid_layer", "uid": "u_s",
                    "parent_index": -1, "layer_kind": "av",
                    "containing_comp_id": 100,
                    "source_item": {
                        "kind": "solid", "name": "Red Solid", "id": 600,
                    },
                },
            ],
        })
        rewires = build_mirror_rewires_spec(manifest, structure)
        assert rewires == []


# ---------------------------------------------------------------------------
# 4. PayloadSlicer integration — mirror_rewires flows into chunk_manifest
# ---------------------------------------------------------------------------

class TestExporterMirrorRewiresIntegration:
    def _minimal_layer(self):
        return {
            "index": 1, "name": "L", "uid": "u_1",
            "parent_index": -1, "layer_kind": "av",
            "position": [0, 0, 0], "scale": [100, 100, 100],
            "rotation_z": 0, "anchor": [0, 0, 0],
            "conformed_transforms": {
                "position": [0, 0, 0], "scale": [100, 100, 100],
                "rotation": 0, "anchor": [0, 0, 0], "is_root": True,
            },
        }

    def _run(self, *, mirror_rewires=None):
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                slicer = PayloadSlicer(output_dir=os.path.join(tmpdir, "Chunks"))
                path = slicer.slice_and_export(
                    [self._minimal_layer()],
                    expected_comp_name="Root",
                    target_width=1080, target_height=1920,
                    preset_label="TikTok",
                    mirror_rewires=mirror_rewires,
                )
                with open(path) as f:
                    return json.load(f)
            finally:
                os.chdir(cwd)

    def test_omits_mirror_rewires_when_absent(self):
        data = self._run(mirror_rewires=None)
        assert "mirror_rewires" not in data

    def test_omits_mirror_rewires_when_empty(self):
        data = self._run(mirror_rewires=[])
        assert "mirror_rewires" not in data

    def test_carries_mirror_rewires_when_present(self):
        rewires = [
            MirrorRewire(
                wrapper_layer_uid="u_wA",
                wrapper_layer_name="Wrapper_A",
                wrapper_in_source_comp_name="Root",
                target_mirror_source_name="Precomp_A",
            ),
            MirrorRewire(
                wrapper_layer_uid="u_wB",
                wrapper_in_source_comp_name="Precomp_A",
                target_mirror_source_name="Precomp_B",
            ),
        ]
        data = self._run(mirror_rewires=rewires)
        assert "mirror_rewires" in data
        assert len(data["mirror_rewires"]) == 2
        assert data["mirror_rewires"][0]["wrapper_layer_uid"] == "u_wA"
        assert data["mirror_rewires"][0]["target_mirror_source_name"] == "Precomp_A"
        # fork_consumer_layer_uid serializes (None for Q3A default).
        assert data["mirror_rewires"][0]["fork_consumer_layer_uid"] is None
