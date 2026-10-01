# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_schema_5_1_slot_12_5.py — Slot 12.5 Stage A / item 3 (schema 5.1).

Locks the additive Slot 12.5 schema fields:

    LayerModel:
        containing_comp_id, containing_comp_uid,
        wrapper_layer_uid, nesting_depth   (cross-comp parent chain)

    LayerFlags:
        continuously_rasterize             (composition quality switch)

    CompNode:
        preserve_nested_frame_rate,
        preserve_nested_resolution         (composition advanced flags)

    ScrapeMeta.schema_version default "5.0" → "5.1"

Every addition is additive and backwards-compatible. Schema 5.0 manifests
parse unchanged against the 5.1 model. These tests guard that contract.
"""

from __future__ import annotations

import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from models.project_structure import CompNode, ProjectStructure  # noqa: E402
from models.scrape_manifest import LayerModel, ScrapeManifest  # noqa: E402
from models.scraper_v5 import LayerFlags, LayerReference, ScrapeMeta  # noqa: E402


# ---------------------------------------------------------------------------
# LayerModel — cross-comp parent-chain fields
# ---------------------------------------------------------------------------

class TestLayerModelCrossCompFields:
    def test_all_four_fields_default_to_none(self):
        layer = LayerModel(index=1, name="Test")
        assert layer.containing_comp_id is None
        assert layer.containing_comp_uid is None
        assert layer.wrapper_layer_uid is None
        assert layer.nesting_depth is None

    def test_containing_comp_id_accepts_int(self):
        layer = LayerModel(index=1, name="Test", containing_comp_id=42)
        assert layer.containing_comp_id == 42

    def test_containing_comp_uid_accepts_string(self):
        layer = LayerModel(index=1, name="Test", containing_comp_uid="comp_abc123")
        assert layer.containing_comp_uid == "comp_abc123"

    def test_wrapper_layer_uid_accepts_string(self):
        layer = LayerModel(index=1, name="Test", wrapper_layer_uid="layer_def456")
        assert layer.wrapper_layer_uid == "layer_def456"

    def test_nesting_depth_accepts_zero(self):
        layer = LayerModel(index=1, name="Test", nesting_depth=0)
        assert layer.nesting_depth == 0

    def test_nesting_depth_accepts_positive_int(self):
        layer = LayerModel(index=1, name="Test", nesting_depth=4)
        assert layer.nesting_depth == 4

    def test_deeply_nested_layer_carries_all_four_fields(self):
        layer = LayerModel(
            index=3,
            name="Deep Layer",
            containing_comp_id=99,
            containing_comp_uid="comp_root",
            wrapper_layer_uid="wrapper_uid_xyz",
            nesting_depth=2,
        )
        assert layer.containing_comp_id == 99
        assert layer.containing_comp_uid == "comp_root"
        assert layer.wrapper_layer_uid == "wrapper_uid_xyz"
        assert layer.nesting_depth == 2

    def test_round_trip_preserves_cross_comp_fields(self):
        original = LayerModel(
            index=1,
            name="RT",
            containing_comp_id=7,
            containing_comp_uid="comp_uid",
            wrapper_layer_uid="wrap_uid",
            nesting_depth=1,
        )
        as_json = original.model_dump_json()
        reparsed = LayerModel.model_validate_json(as_json)
        assert reparsed.containing_comp_id == 7
        assert reparsed.containing_comp_uid == "comp_uid"
        assert reparsed.wrapper_layer_uid == "wrap_uid"
        assert reparsed.nesting_depth == 1


# ---------------------------------------------------------------------------
# LayerFlags.continuously_rasterize
# ---------------------------------------------------------------------------

class TestLayerFlagsContinuouslyRasterize:
    def test_defaults_to_false(self):
        flags = LayerFlags()
        assert flags.continuously_rasterize is False

    def test_accepts_true(self):
        flags = LayerFlags(continuously_rasterize=True)
        assert flags.continuously_rasterize is True

    def test_round_trip_preserves_value(self):
        flags = LayerFlags(continuously_rasterize=True)
        as_json = flags.model_dump_json()
        reparsed = LayerFlags.model_validate_json(as_json)
        assert reparsed.continuously_rasterize is True

    def test_legacy_flags_without_field_parses_unchanged(self):
        # Simulate a schema-5.0 flags blob with no continuously_rasterize key.
        legacy_blob = {
            "enabled": True,
            "collapse_transformations": True,
            "three_d": True,
        }
        flags = LayerFlags.model_validate(legacy_blob)
        # Default applied because the field was absent.
        assert flags.continuously_rasterize is False
        # Existing fields parse as before.
        assert flags.collapse_transformations is True
        assert flags.three_d is True


# ---------------------------------------------------------------------------
# CompNode — composition advanced flags
# ---------------------------------------------------------------------------

class TestCompNodeCompositionFlags:
    def _base_comp(self):
        return {"id": 1, "name": "Test Comp", "width": 1920, "height": 1080}

    def test_both_flags_default_to_false(self):
        comp = CompNode.model_validate(self._base_comp())
        assert comp.preserve_nested_frame_rate is False
        assert comp.preserve_nested_resolution is False

    def test_preserve_frame_rate_accepts_true(self):
        data = self._base_comp()
        data["preserve_nested_frame_rate"] = True
        comp = CompNode.model_validate(data)
        assert comp.preserve_nested_frame_rate is True

    def test_preserve_resolution_accepts_true(self):
        data = self._base_comp()
        data["preserve_nested_resolution"] = True
        comp = CompNode.model_validate(data)
        assert comp.preserve_nested_resolution is True

    def test_legacy_comp_without_flags_parses_unchanged(self):
        # Pre-5.1 comp_node JSON — no preserve_* keys at all.
        legacy_blob = {
            "id": 5,
            "name": "Legacy Comp",
            "width": 1920,
            "height": 1080,
            "layer_count": 19,
        }
        comp = CompNode.model_validate(legacy_blob)
        assert comp.preserve_nested_frame_rate is False
        assert comp.preserve_nested_resolution is False
        assert comp.layer_count == 19


# ---------------------------------------------------------------------------
# ScrapeMeta.schema_version bump
# ---------------------------------------------------------------------------

class TestScrapeMetaVersionBump:
    def test_default_is_5_1(self):
        meta = ScrapeMeta()
        assert meta.schema_version == "5.1"

    def test_legacy_5_0_manifest_parses_unchanged(self):
        legacy = ScrapeMeta(schema_version="5.0")
        assert legacy.schema_version == "5.0"

    def test_5_1_manifest_explicit_value_preserved(self):
        meta = ScrapeMeta(schema_version="5.1")
        assert meta.schema_version == "5.1"

    def test_future_version_strings_pass_through(self):
        # The field is a free-form string; only the writer side commits
        # to a specific format. Read-side is permissive.
        meta = ScrapeMeta(schema_version="6.0")
        assert meta.schema_version == "6.0"


# ---------------------------------------------------------------------------
# Backwards-compat: full legacy 5.0 manifest still parses against 5.1 model
# ---------------------------------------------------------------------------

class TestLegacyManifestParsesUnchanged:
    """The single most load-bearing test in this file. A schema-5.0
    manifest written before Slot 12.5 MUST parse unchanged against the
    schema-5.1 model. If this fails, the additive contract is broken."""

    def test_minimal_legacy_5_0_manifest_parses(self):
        legacy_manifest = {
            "status": "OK",
            "project_info": {"name": "Legacy", "width": 1920, "height": 1080, "fps": 30.0},
            "scrape_meta": {"schema_version": "5.0"},
            "layers": [
                {
                    "id": 1,
                    "index": 1,
                    "name": "Layer 1",
                    "uid": "abc",
                    "parent_index": -1,
                    "position": [960, 540, 0],
                    "scale": [100, 100, 100],
                    "rotation_z": 0,
                    "anchor": [0, 0, 0],
                },
            ],
        }
        manifest = ScrapeManifest.model_validate(legacy_manifest)
        # Existing fields parse unchanged.
        assert manifest.status == "OK"
        assert manifest.schema_version == "5.0"
        layer = manifest.layers[0]
        assert layer.name == "Layer 1"
        assert layer.parent_index == -1
        # New 5.1 fields default to None on a 5.0 manifest.
        assert layer.containing_comp_id is None
        assert layer.containing_comp_uid is None
        assert layer.wrapper_layer_uid is None
        assert layer.nesting_depth is None

    def test_5_1_manifest_with_cross_comp_fields_parses(self):
        v51_manifest = {
            "status": "OK",
            "project_info": {"name": "Nested", "width": 1920, "height": 1080, "fps": 30.0},
            "scrape_meta": {"schema_version": "5.1"},
            "layers": [
                {
                    "id": 1,
                    "index": 1,
                    "name": "Top Layer",
                    "uid": "uid_top",
                    "parent_index": -1,
                    "containing_comp_id": 100,
                    "wrapper_layer_uid": None,
                    "nesting_depth": 0,
                },
                {
                    "id": 2,
                    "index": 1,
                    "name": "Deep Layer",
                    "uid": "uid_deep",
                    "parent_index": -1,
                    "containing_comp_id": 200,
                    "wrapper_layer_uid": "uid_top",
                    "nesting_depth": 1,
                },
            ],
        }
        manifest = ScrapeManifest.model_validate(v51_manifest)
        assert manifest.schema_version == "5.1"
        top, deep = manifest.layers
        assert top.nesting_depth == 0 and top.wrapper_layer_uid is None
        assert deep.nesting_depth == 1 and deep.wrapper_layer_uid == "uid_top"

    def test_legacy_project_structure_parses_unchanged(self):
        legacy_ps = {
            "status": "OK",
            "schema_version": "1.0",
            "scan_meta": {"scanned_at": "2026-05-15T00:00:00Z"},
            "comps": [
                {"id": 1, "name": "Final", "width": 1920, "height": 1080, "layer_count": 19},
            ],
            "references": [],
        }
        ps = ProjectStructure.model_validate(legacy_ps)
        comp = ps.comps[0]
        assert comp.preserve_nested_frame_rate is False
        assert comp.preserve_nested_resolution is False


# ---------------------------------------------------------------------------
# Stage B — cross-comp parent-ref validator (comp-scoped index lookup)
# ---------------------------------------------------------------------------

class TestCrossCompParentRefValidator:
    """The cross_validate_parent_refs validator builds uid↔index maps to
    catch parent_index/parent_uid mismatches. Schema 5.1's recursive
    scrape produces multiple comps' layers in one flat list, so the
    maps MUST be scoped by `containing_comp_id`. Otherwise layer index
    1 in comp A and layer index 1 in comp B collide and produce
    spurious mismatch warnings (or worse — auto-heal to the wrong uid)."""

    def _manifest_with(self, layers):
        return {
            "status": "OK",
            "project_info": {"name": "T", "width": 1920, "height": 1080, "fps": 30.0},
            "scrape_meta": {"schema_version": "5.1"},
            "layers": layers,
        }

    def test_index_collision_across_comps_does_not_false_flag(self):
        # Two comps, each with a layer at index 1 + a child at index 2.
        # The children correctly point at their own-comp parent (index 1)
        # by both uid and index. Pre-Stage-B validator would build a flat
        # index_to_uid map where index 1 = whichever layer was processed
        # last, then flag the other comp's child as a mismatch.
        layers = [
            {"index": 1, "name": "A-root", "uid": "uid_a_root",
             "parent_index": -1, "containing_comp_id": 100, "nesting_depth": 0},
            {"index": 2, "name": "A-child", "uid": "uid_a_child",
             "parent_index": 1, "parent_uid": "uid_a_root",
             "containing_comp_id": 100, "nesting_depth": 0},
            {"index": 1, "name": "B-root", "uid": "uid_b_root",
             "parent_index": -1, "containing_comp_id": 200, "nesting_depth": 1,
             "wrapper_layer_uid": "uid_a_root"},
            {"index": 2, "name": "B-child", "uid": "uid_b_child",
             "parent_index": 1, "parent_uid": "uid_b_root",
             "containing_comp_id": 200, "nesting_depth": 1,
             "wrapper_layer_uid": "uid_a_root"},
        ]
        manifest = ScrapeManifest.model_validate(self._manifest_with(layers))
        # Neither child gets auto-healed; both retain their correct parent_index.
        a_child = next(L for L in manifest.layers if L.name == "A-child")
        b_child = next(L for L in manifest.layers if L.name == "B-child")
        assert a_child.parent_index == 1
        assert b_child.parent_index == 1

    def test_real_mismatch_within_one_comp_still_caught(self):
        # Validator still auto-heals genuine mismatches inside a single
        # comp — Stage B's comp-scoping doesn't disable the check, just
        # buckets it correctly.
        layers = [
            {"index": 1, "name": "root", "uid": "uid_root",
             "parent_index": -1, "containing_comp_id": 100},
            {"index": 2, "name": "alias", "uid": "uid_alias",
             "parent_index": -1, "containing_comp_id": 100},
            # `bad` claims its parent is the layer at index 1, but its
            # parent_uid points at uid_alias (which is index 2). The
            # validator should heal parent_index to 2.
            {"index": 3, "name": "bad", "uid": "uid_bad",
             "parent_index": 1, "parent_uid": "uid_alias",
             "containing_comp_id": 100},
        ]
        manifest = ScrapeManifest.model_validate(self._manifest_with(layers))
        bad = next(L for L in manifest.layers if L.name == "bad")
        assert bad.parent_index == 2  # healed by uid

    def test_legacy_5_0_layers_share_one_bucket(self):
        # Layers without `containing_comp_id` (legacy 5.0) all share the
        # None-keyed bucket — pre-5.1 behavior preserved exactly.
        layers = [
            {"index": 1, "name": "root", "uid": "uid_root", "parent_index": -1},
            {"index": 2, "name": "child", "uid": "uid_child",
             "parent_index": 1, "parent_uid": "uid_root"},
        ]
        manifest = ScrapeManifest.model_validate(self._manifest_with(layers))
        # No new fields set; validator runs on the None bucket; pass.
        child = next(L for L in manifest.layers if L.name == "child")
        assert child.parent_index == 1
        assert child.containing_comp_id is None


# ---------------------------------------------------------------------------
# Stage B item 2 — LayerReference model + LayerModel.layer_references field
# ---------------------------------------------------------------------------

class TestLayerReferenceModel:
    def test_all_fields_optional_defaults_none(self):
        ref = LayerReference()
        assert ref.from_layer_uid is None
        assert ref.to_layer_uid is None
        assert ref.to_layer_index is None
        assert ref.effect_match_name is None
        assert ref.effect_display_name is None
        assert ref.property_match_name is None
        assert ref.property_display_name is None

    def test_round_trip_preserves_all_fields(self):
        ref = LayerReference(
            from_layer_uid="uid_a",
            to_layer_uid="uid_b",
            to_layer_index=3,
            effect_match_name="ADBE Set Matte3",
            effect_display_name="Set Matte",
            property_match_name="ADBE Set Matte3-0001",
            property_display_name="Take Matte From Layer",
        )
        reparsed = LayerReference.model_validate_json(ref.model_dump_json())
        assert reparsed.from_layer_uid == "uid_a"
        assert reparsed.to_layer_uid == "uid_b"
        assert reparsed.to_layer_index == 3
        assert reparsed.effect_match_name == "ADBE Set Matte3"

    def test_to_uid_optional_for_unresolved_index(self):
        # When the scraped index resolves to no layer (corrupt manifest,
        # missing layer, etc.), to_layer_uid stays None but to_layer_index
        # is preserved so diagnostics can show what AE returned.
        ref = LayerReference(
            from_layer_uid="uid_a",
            to_layer_uid=None,
            to_layer_index=99,
            effect_match_name="ADBE Set Matte3",
            property_match_name="ADBE Set Matte3-0001",
        )
        assert ref.to_layer_uid is None
        assert ref.to_layer_index == 99


class TestLayerModelLayerReferencesField:
    def test_field_defaults_to_none(self):
        layer = LayerModel(index=1, name="Test")
        assert layer.layer_references is None

    def test_accepts_list_of_references(self):
        refs = [
            LayerReference(from_layer_uid="uid_a", to_layer_uid="uid_b",
                           to_layer_index=2,
                           effect_match_name="ADBE Set Matte3",
                           property_match_name="ADBE Set Matte3-0001"),
        ]
        layer = LayerModel(index=1, name="Consumer", layer_references=refs)
        assert layer.layer_references is not None
        assert len(layer.layer_references) == 1
        assert layer.layer_references[0].to_layer_uid == "uid_b"

    def test_legacy_5_0_layer_without_references_parses(self):
        # No layer_references key on the layer dict at all — schema-5.0
        # backward-compat. Default applied → None.
        legacy_layer_blob = {
            "index": 1,
            "name": "Legacy",
            "uid": "uid_legacy",
            "parent_index": -1,
        }
        layer = LayerModel.model_validate(legacy_layer_blob)
        assert layer.layer_references is None

    def test_round_trip_through_scrape_manifest(self):
        # A 5.1 manifest with a layer that carries an effect-input ref
        # should parse + serialize + reparse cleanly.
        manifest_dict = {
            "status": "OK",
            "project_info": {"name": "T", "width": 1920, "height": 1080, "fps": 30.0},
            "scrape_meta": {"schema_version": "5.1"},
            "layers": [
                {
                    "index": 1, "name": "matte", "uid": "uid_matte",
                    "parent_index": -1,
                    "containing_comp_id": 100,
                },
                {
                    "index": 2, "name": "consumer", "uid": "uid_consumer",
                    "parent_index": -1,
                    "containing_comp_id": 100,
                    "layer_references": [
                        {
                            "from_layer_uid": "uid_consumer",
                            "to_layer_uid":   "uid_matte",
                            "to_layer_index": 1,
                            "effect_match_name":     "ADBE Set Matte3",
                            "effect_display_name":   "Set Matte",
                            "property_match_name":   "ADBE Set Matte3-0001",
                            "property_display_name": "Take Matte From Layer",
                        }
                    ],
                },
            ],
        }
        manifest = ScrapeManifest.model_validate(manifest_dict)
        consumer = next(L for L in manifest.layers if L.name == "consumer")
        assert consumer.layer_references is not None
        assert len(consumer.layer_references) == 1
        assert consumer.layer_references[0].to_layer_uid == "uid_matte"
        # Round-trip via JSON.
        as_json = manifest.model_dump_json()
        reparsed = ScrapeManifest.model_validate_json(as_json)
        rc = next(L for L in reparsed.layers if L.name == "consumer")
        assert rc.layer_references[0].effect_match_name == "ADBE Set Matte3"
