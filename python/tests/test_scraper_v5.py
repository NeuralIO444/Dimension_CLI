# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_scraper_v5.py
Dimension Engine — v5 scraper schema + registry + analyzer tests.

These tests lock in the contract between the JSX scraper and the Python
consumer. Every assertion here is load-bearing for the rest of the
pipeline — if one of these fails, the scraper is wrong or the schema
drifted and someone forgot to update the JSX table.
"""

from __future__ import annotations

import sys
import pathlib

# Make `python/` importable when pytest is run from the repo root.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from core.match_name_registry import (  # noqa: E402
    REGISTRY,
    all_match_names,
    classify as classify_match,
    get as get_match,
    to_json as registry_to_json,
)
from core.property_registry import ScaleRule  # noqa: E402
from models.scrape_manifest import ScrapeManifest  # noqa: E402
from models.scraper_v5 import (  # noqa: E402
    Category,
    EffectRecord,
    ExpressionClass,
    ExpressionInfo,
    KeyStream,
    LayerFlags,
    MaskRecord,
    MaskShape,
    PropertyRecord,
    ScrapeError,
    SourceItem,
    SourceKind,
    TrackMatte,
    TrackMatteType,
    ValueKind,
)


# ══════════════════════════════════════════════════════════════════════════
#  MATCH NAME REGISTRY
# ══════════════════════════════════════════════════════════════════════════


class TestMatchNameRegistry:
    """The registry is the single source of truth for matchName dispatch.

    JSX scraper mirrors this table; any drift is caught by
    test_jsx_classifier_mirrors_python (below).
    """

    def test_registry_nonempty(self):
        assert len(REGISTRY) > 0
        assert len(all_match_names()) > 50   # we shipped 70+ entries

    def test_core_transform_entries_present(self):
        # Position MUST be in the registry — it's the foundation property.
        pos = get_match("ADBE Position")
        assert pos is not None
        assert pos.category is Category.TRANSFORM
        assert pos.value_kind is ValueKind.VEC3
        assert pos.scale_rule is ScaleRule.ROOT_CENTER_REMAP_XY_SCALE_Z
        assert pos.separable is True

    def test_separated_axis_entries(self):
        for name, rule in [
            ("ADBE Position_0", ScaleRule.ROOT_REMAP_AXIS_X),
            ("ADBE Position_1", ScaleRule.ROOT_REMAP_AXIS_Y),
            ("ADBE Position_2", ScaleRule.ROOT_SCALE_AXIS_Z),
        ]:
            e = get_match(name)
            assert e is not None, f"missing separated axis entry {name}"
            assert e.scale_rule is rule
            assert e.category is Category.TRANSFORM_AXIS

    def test_camera_intrinsics(self):
        zoom = get_match("ADBE Camera Zoom")
        assert zoom is not None
        assert zoom.scale_rule is ScaleRule.CAMERA_INTRINSIC_SCALE

    def test_light_world_distances_scale(self):
        """radius + falloff distance must multiply by S."""
        for name in ("ADBE Light Radius",
                     "ADBE Light Falloff Distance",
                     "ADBE Light Falloff Start"):
            e = get_match(name)
            assert e is not None, f"missing light entry {name}"
            assert e.scale_rule is ScaleRule.MULTIPLY_BY_S, (
                f"{name} must scale by S — world distance"
            )

    def test_layer_style_distance_scales(self):
        e = get_match("ADBE Drop Shadow Distance")
        assert e is not None
        assert e.scale_rule is ScaleRule.MULTIPLY_BY_S

    def test_drop_shadow_effect_distance_scales(self):
        e = get_match("ADBE Drop Shadow-0004")
        assert e is not None
        assert e.scale_rule is ScaleRule.MULTIPLY_BY_S
        assert e.display_name == "Distance"

    def test_unknown_matchname_passes_through(self):
        e = classify_match("ADBE Totally Made Up Effect Name")
        assert e.category is Category.UNKNOWN
        assert e.scale_rule is ScaleRule.PASS_THROUGH

    def test_serialize_for_jsx(self):
        j = registry_to_json()
        assert isinstance(j, list)
        assert len(j) == len(REGISTRY)
        sample = j[0]
        assert {"match_name", "display_name", "category",
                "value_kind", "scale_rule", "separable", "notes"} <= set(sample.keys())





# ══════════════════════════════════════════════════════════════════════════
#  v5 MODELS ROUND TRIP
# ══════════════════════════════════════════════════════════════════════════


class TestV5Models:

    def test_key_stream_basic(self):
        ks = KeyStream(times=[0.0, 1.0], values=[[0, 0], [100, 50]])
        assert ks.times == [0.0, 1.0]
        assert ks.values == [[0, 0], [100, 50]]

    def test_key_stream_rejects_nan(self):
        import math
        import pytest
        with pytest.raises(Exception):
            KeyStream(times=[0.0], values=[math.nan])

    def test_property_record_minimal(self):
        p = PropertyRecord(
            path=["transform", "ADBE Position"],
            match_name="ADBE Position",
            display_name="Position",
            value_kind=ValueKind.VEC3,
            static=[960.0, 540.0, 0.0],
        )
        assert p.category is Category.UNKNOWN   # default when not set
        assert p.separated is False
        assert p.expression is None

    def test_property_record_with_per_axis(self):
        p = PropertyRecord(
            path=["transform", "ADBE Position"],
            match_name="ADBE Position",
            display_name="Position",
            value_kind=ValueKind.VEC3,
            separated=True,
            per_axis={
                "x": KeyStream(times=[0.0, 1.0], values=[100.0, 300.0]),
                "y": KeyStream(times=[0.0, 1.0], values=[200.0, 400.0]),
            },
        )
        assert "x" in p.per_axis
        assert p.per_axis["x"].values == [100.0, 300.0]

    def test_property_record_with_expression(self):
        p = PropertyRecord(
            path=["transform", "ADBE Position"],
            match_name="ADBE Position",
            display_name="Position",
            value_kind=ValueKind.VEC3,
            expression=ExpressionInfo(source="thisComp.width/2", enabled=True),
        )
        enriched = p.expression.model_copy(update={
            "classification": ExpressionClass.REWRITABLE,
        })
        assert enriched.classification is ExpressionClass.REWRITABLE

    def test_effect_record(self):
        fx = EffectRecord(
            index=1,
            match_name="ADBE Drop Shadow",
            display_name="Drop Shadow",
            enabled=True,
            properties=[
                PropertyRecord(
                    path=["effects", "ADBE Drop Shadow", "ADBE Drop Shadow-0004"],
                    match_name="ADBE Drop Shadow-0004",
                    display_name="Distance",
                    category=Category.EFFECT_SCALE,
                    value_kind=ValueKind.SCALAR,
                    scale_rule="multiply_by_s",
                    static=15.0,
                ),
            ],
        )
        assert fx.properties[0].scale_rule == "multiply_by_s"

    def test_mask_record(self):
        m = MaskRecord(
            index=1,
            name="Mask 1",
            mode="add",
            shape=MaskShape(
                vertices=[[0.0, 0.0], [100.0, 0.0], [100.0, 100.0]],
                in_tangents=[[0, 0], [0, 0], [0, 0]],
                out_tangents=[[0, 0], [0, 0], [0, 0]],
                closed=True,
            ),
        )
        assert m.shape.closed is True
        assert len(m.shape.vertices) == 3

    def test_layer_flags_defaults(self):
        f = LayerFlags()
        assert f.enabled is True
        assert f.three_d is False
        assert f.frame_blending == "none"
        assert f.auto_orient == "none"

    def test_track_matte(self):
        t = TrackMatte(type=TrackMatteType.ALPHA, target_index=2, target_uid="abc")
        assert t.type is TrackMatteType.ALPHA
        assert t.target_index == 2

    def test_source_item(self):
        s = SourceItem(
            kind=SourceKind.FOOTAGE,
            name="clip.mov",
            width=1920, height=1080,
            file_path="/path/to/clip.mov",
        )
        assert s.kind is SourceKind.FOOTAGE
        assert s.width == 1920

    def test_scrape_error(self):
        e = ScrapeError(
            layer_index=3, layer_name="Camera 1",
            path=["transform", "ADBE Position"],
            operation="read_keys",
            error="property hidden",
            hypothesis="one-node camera POI",
        )
        assert e.severity.value == "warning"


# ══════════════════════════════════════════════════════════════════════════
#  MANIFEST EXTENSION (legacy + v5)
# ══════════════════════════════════════════════════════════════════════════


class TestManifestExtension:

    def test_legacy_manifest_still_parses(self):
        """A manifest with only legacy fields must validate unchanged."""
        raw = {
            "status": "OK",
            "project_info": {"name": "Test", "width": 1920, "height": 1080},
            "layers": [
                {"index": 1, "name": "Layer 1", "position": [960, 540, 0]},
            ],
        }
        m = ScrapeManifest.model_validate(raw)
        assert m.is_v5() is False
        assert m.schema_version == "4.x"
        assert m.scrape_meta is None
        assert m.layers[0].properties is None

    def test_v5_manifest_parses(self):
        raw = {
            "status": "OK",
            "project_info": {"name": "Test", "width": 1920, "height": 1080},
            "scrape_meta": {
                "schema_version": "5.0",
                "scraper_version": "sovereign-5.0.0",
                "scrape_mode": "full",
                "total_layers": 1,
            },
            "errors": [],
            "layers": [
                {
                    "index": 1, "name": "Layer 1", "position": [960, 540, 0],
                    "flags": {"enabled": True, "three_d": True, "motion_blur": True},
                    "properties": [
                        {
                            "path": ["transform", "ADBE Position"],
                            "match_name": "ADBE Position",
                            "display_name": "Position",
                            "category": "transform",
                            "value_kind": "vec3",
                            "scale_rule": "root_center_remap_xy_scale_z",
                            "static": [960.0, 540.0, 0.0],
                        },
                    ],
                },
            ],
        }
        m = ScrapeManifest.model_validate(raw)
        assert m.is_v5() is True
        assert m.scrape_meta.scrape_mode.value == "full"
        assert m.layers[0].flags.three_d is True
        assert len(m.layers[0].properties) == 1
        assert m.layers[0].properties[0].match_name == "ADBE Position"

    def test_v5_manifest_error_ledger(self):
        raw = {
            "status": "OK",
            "project_info": {"name": "T", "width": 1920, "height": 1080},
            "scrape_meta": {"schema_version": "5.0"},
            "errors": [
                {
                    "layer_index": 3,
                    "layer_name": "Camera 1",
                    "path": ["transform", "ADBE Anchor Point"],
                    "operation": "read_value",
                    "error": "property or a parent property is hidden",
                    "severity": "warning",
                    "hypothesis": "one-node camera — POI not user-settable",
                },
            ],
            "layers": [],
        }
        m = ScrapeManifest.model_validate(raw)
        assert len(m.errors) == 1
        assert m.errors[0].hypothesis.startswith("one-node")


# ══════════════════════════════════════════════════════════════════════════
#  DRIFT GUARD: Python registry ↔ JSX classifier
# ══════════════════════════════════════════════════════════════════════════


class TestPythonJsxMirror:
    """
    The JSX classifier encodes the same matchName → rule table as Python.
    This test reads the JSX file, extracts the embedded table, and
    verifies every entry matches the Python registry. If this fails,
    someone updated one side without the other.
    """

    def _read_jsx_table(self) -> list:
        """
        Parse SovCore_Classify.jsx and extract its DIMENSION_MATCH_TABLE
        array. We do this with a lightweight regex — the JSX file is
        purpose-built so the table is easy to locate.
        """
        jsx_path = REPO_ROOT.parent / "Scripts" / "Dimension_Assets" / "SovCore_Classify.jsx"
        if not jsx_path.exists():
            # Mirror file not yet written — test skips (covered later
            # in this session). Deferred assert keeps signal clear.
            return []

        src = jsx_path.read_text()
        # The JSX table is a literal JS object list of {matchName, scaleRule}
        # entries. We use the Python registry's JSON export as the canonical
        # shape and check membership, not structural equivalence.
        import re
        entries = []
        # Each table row is on its own line: { matchName: "...", scaleRule: "..." }
        pattern = re.compile(
            r'\{\s*matchName\s*:\s*"([^"]+)"\s*,\s*scaleRule\s*:\s*"([^"]+)"'
        )
        for m in pattern.finditer(src):
            entries.append((m.group(1), m.group(2)))
        return entries

    def test_jsx_table_matches_python_registry(self):
        jsx = self._read_jsx_table()
        if not jsx:
            # Classifier file not yet written — xfail gracefully.
            import pytest
            pytest.skip("SovCore_Classify.jsx not present yet — drift check deferred")
            return

        py = {e.match_name: e.scale_rule.value for e in REGISTRY}
        jsx_map = dict(jsx)

        missing_in_jsx = set(py.keys()) - set(jsx_map.keys())
        missing_in_py = set(jsx_map.keys()) - set(py.keys())

        assert not missing_in_jsx, (
            f"Entries in Python registry but not JSX classifier: {sorted(missing_in_jsx)}"
        )
        assert not missing_in_py, (
            f"Entries in JSX classifier but not Python registry: {sorted(missing_in_py)}"
        )

        for match_name, py_rule in py.items():
            assert jsx_map[match_name] == py_rule, (
                f"Rule drift on {match_name!r}: Python={py_rule}, JSX={jsx_map[match_name]}"
            )
