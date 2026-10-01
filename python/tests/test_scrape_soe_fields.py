# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_scrape_soe_fields.py
v5.2.5 SOE-Scrape prep — schema-only coverage.

Validates the four new LayerModel fields (`source_rect`,
`world_bounds`, `hero_time`, `is_keyed`) and their None-safety on
structural layer kinds. JSX emission is verified manually per the
PR's manual QA checklist; this file does NOT execute ExtendScript.

Fixture: python/tests/fixtures/soe_fields_minimal.json — 6 layers
(Camera / Light / Null / Adjustment / static text / keyed text)
designed to exercise every field permutation.
"""

from __future__ import annotations

import os
import sys
import json
import math
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from models.scrape_manifest import ScrapeManifest


FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "soe_fields_minimal.json"
)


@pytest.fixture
def manifest():
    with FIXTURE_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return ScrapeManifest.model_validate(raw)


def _by_uid(manifest, uid: str):
    for L in manifest.layers:
        if L.uid == uid:
            return L
    raise KeyError(uid)


# ── Structural layers — null bounds, all-False is_keyed ───────────


class TestStructuralLayersHaveNullBounds:
    def test_camera_null_bounds(self, manifest):
        L = _by_uid(manifest, "uid-camera")
        assert L.world_bounds is None
        assert L.source_rect is None
        assert L.is_keyed == {"position": False, "scale": False, "anchor": False}

    def test_light_null_bounds(self, manifest):
        L = _by_uid(manifest, "uid-light")
        assert L.world_bounds is None
        assert L.source_rect is None
        assert L.is_keyed == {"position": False, "scale": False, "anchor": False}

    def test_null_layer_null_bounds(self, manifest):
        L = _by_uid(manifest, "uid-null")
        assert L.world_bounds is None
        assert L.source_rect is None

    def test_adjustment_null_bounds(self, manifest):
        L = _by_uid(manifest, "uid-adj")
        assert L.world_bounds is None
        assert L.source_rect is None


# ── AV layer — populated bounds + finite floats ──────────────────


class TestAvLayerHasWorldBounds:
    def test_static_text_has_finite_world_bounds(self, manifest):
        L = _by_uid(manifest, "uid-title-static")
        assert L.world_bounds is not None
        for edge in ("l", "t", "r", "b"):
            assert edge in L.world_bounds
            assert math.isfinite(L.world_bounds[edge])
        assert L.world_bounds["l"] < L.world_bounds["r"]
        assert L.world_bounds["t"] < L.world_bounds["b"]

    def test_static_text_source_rect_shape(self, manifest):
        L = _by_uid(manifest, "uid-title-static")
        assert isinstance(L.source_rect, list)
        assert len(L.source_rect) == 4
        for v in L.source_rect:
            assert isinstance(v, (int, float))
            assert math.isfinite(float(v))

    def test_static_text_hero_time_positive(self, manifest):
        L = _by_uid(manifest, "uid-title-static")
        assert L.hero_time is not None
        assert L.hero_time > 0


# ── is_keyed — animated vs static detection ──────────────────────


class TestIsKeyedDetectsAnimatedPosition:
    def test_keyed_layer_position_flag(self, manifest):
        L = _by_uid(manifest, "uid-title-keyed")
        assert L.is_keyed == {
            "position": True,
            "scale":    False,
            "anchor":   False,
        }


class TestIsKeyedDefaultsAllFalse:
    def test_static_layer_all_false(self, manifest):
        L = _by_uid(manifest, "uid-title-static")
        assert L.is_keyed == {
            "position": False,
            "scale":    False,
            "anchor":   False,
        }


# ── Backwards compat — older manifests parse cleanly ─────────────


class TestBackwardsCompat:
    def test_legacy_manifest_without_soe_fields(self):
        """A v5-era manifest that predates SOE-Scrape parses cleanly,
        all four new fields default to None."""
        legacy = {
            "status": "OK",
            "scrape_meta": {
                "schema_version": "5.0.0",
                "exported_at":    "2026-04-01T00:00:00Z",
                "scraper_revision": "legacy",
            },
            "project_info": {
                "name": "Legacy", "width": 1920, "height": 1080,
                "fps": 24.0, "duration": 10.0,
            },
            "layers": [{
                "index": 1, "name": "Title", "uid": "u1",
                "parent_index": -1, "layer_kind": "av",
                "position": [960, 540, 0],
                "scale": [100, 100, 100],
                "anchor": [0, 0, 0],
            }],
        }
        m = ScrapeManifest.model_validate(legacy)
        L = m.layers[0]
        assert L.source_rect is None
        assert L.world_bounds is None
        assert L.hero_time is None
        assert L.is_keyed is None

    def test_existing_v5_fixture_still_parses(self):
        """The shipped fixture at scrape_manifest_v5.json must parse
        unchanged — schema_version stays at v5, the new SOE fields
        are Optional, no break."""
        legacy_fixture = (
            Path(__file__).resolve().parent / "fixtures" / "scrape_manifest_v5.json"
        )
        with legacy_fixture.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        m = ScrapeManifest.model_validate(raw)
        # Sanity check — the existing fixture has 3 layers.
        assert len(m.layers) == 3
        # No layer should accidentally have populated SOE fields.
        for L in m.layers:
            assert L.source_rect is None
            assert L.world_bounds is None
            assert L.hero_time is None
            assert L.is_keyed is None


# ── world_bounds validator — rejects NaN / Inf ───────────────────


class TestWorldBoundsValidatorRejectsNan:
    def _make_layer(self, world_bounds):
        return {
            "status": "OK",
            "project_info": {
                "name": "X", "width": 1920, "height": 1080,
                "fps": 24.0, "duration": 10.0,
            },
            "layers": [{
                "index": 1, "name": "L", "uid": "u",
                "parent_index": -1, "layer_kind": "av",
                "position": [0, 0, 0], "scale": [100, 100, 100],
                "anchor": [0, 0, 0],
                "world_bounds": world_bounds,
            }],
        }

    def test_nan_rejected(self):
        bad = self._make_layer({"l": float("nan"), "t": 0, "r": 100, "b": 100})
        with pytest.raises(Exception):
            ScrapeManifest.model_validate(bad)

    def test_inf_rejected(self):
        bad = self._make_layer({"l": 0, "t": 0, "r": float("inf"), "b": 100})
        with pytest.raises(Exception):
            ScrapeManifest.model_validate(bad)

    def test_missing_edge_rejected(self):
        bad = self._make_layer({"l": 0, "t": 0, "r": 100})  # no "b"
        with pytest.raises(Exception):
            ScrapeManifest.model_validate(bad)

    def test_non_numeric_rejected(self):
        bad = self._make_layer({"l": "zero", "t": 0, "r": 100, "b": 100})
        with pytest.raises(Exception):
            ScrapeManifest.model_validate(bad)

    def test_finite_floats_accepted(self):
        good = self._make_layer({"l": 0.5, "t": -5.0, "r": 100.5, "b": 100.0})
        m = ScrapeManifest.model_validate(good)
        assert m.layers[0].world_bounds == {
            "l": 0.5, "t": -5.0, "r": 100.5, "b": 100.0,
        }
