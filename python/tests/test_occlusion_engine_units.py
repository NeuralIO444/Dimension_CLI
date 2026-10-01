# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_occlusion_engine_units.py
U3 Phase A — unit-aware SOE coverage.

Covers the mission item 1 contract in .pipeline/plan.md:
  - sealed precomps are detection-only (never moved; a Run Warning
    fires instead of a per-layer correction)
  - a preserving camera scene is detection-only as ONE union AABB
  - multi-member TRANSLATABLE-tag clusters move as one rigid unit —
    the IDENTICAL (dx, dy) applied to every member (offset-preserving,
    never an absolute pin — the 2026-06-29 gravity-collapse
    anti-pattern)
  - any keyed member makes the whole unit detection-only
  - `resolution=None` (the default) reproduces the pre-U3 per-layer
    behavior byte-identically (existing test_occlusion_engine.py
    coverage already proves this since none of those call sites pass
    `resolution=`; this file adds one direct check for the omitted-
    vs-explicit-None equivalence and an explicit falsy-resolution
    gate-off check).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.occlusion_engine import OcclusionEngine, OcclusionMask
from core.placement_units import PlacementResolution


COMP_W = 200
COMP_H = 300


def _write_mask(tmp_path: Path,
                go_box: Optional[tuple] = None,
                cutoff_box: Optional[tuple] = None,
                w: int = COMP_W, h: int = COMP_H) -> Path:
    img = np.full((h, w), 128, dtype=np.uint8)
    if go_box is not None:
        l, t, r, b = go_box
        img[t:b, l:r] = 255
    if cutoff_box is not None:
        l, t, r, b = cutoff_box
        img[t:b, l:r] = 0
    out = tmp_path / "mask.png"
    cv2.imwrite(str(out), img)
    return out


def _layer(name: str, *,
           index: int = 1,
           uid: str = "u-1",
           kind: str = "av",
           tag: Optional[str] = "TOP",
           tag_source: str = "manual_comment",
           containing_comp_id: Optional[int] = 1,
           position=(100.0, 50.0),
           anchor=(0.0, 0.0),
           scale=(100.0, 100.0),
           source_rect=(0.0, 0.0, 80.0, 30.0),
           is_keyed: Optional[dict] = None) -> dict:
    """Construct a conformed-layer dict matching what SOE sees from
    `scale_engine.conform()` (mirrors test_occlusion_engine.py's
    `_layer()` helper, extended with `containing_comp_id` for the
    unit-aware comp-scoped lookups)."""
    if is_keyed is None:
        is_keyed = {"position": False, "scale": False, "anchor": False}
    pos3 = list(position) + [0.0]
    scale3 = list(scale) + [100.0]
    anchor3 = list(anchor) + [0.0]
    return {
        "index": index,
        "uid": uid,
        "name": name,
        "layer_kind": kind,
        "content_tag": tag,
        "content_tag_source": tag_source,
        "containing_comp_id": containing_comp_id,
        "position": pos3,
        "scale": scale3,
        "anchor": anchor3,
        "source_rect": list(source_rect),
        "world_bounds": None,
        "hero_time": 5.0,
        "is_keyed": dict(is_keyed),
        "conformed_transforms": {
            "is_root": True,
            "position": pos3,
            "scale":    scale3,
            "rotation": 0.0,
            "anchor":   anchor3,
        },
    }


def _conformed(layers: list) -> dict:
    return {
        "status": "SAFE",
        "layers": layers,
        "warnings": {"collapsed_layers": [], "child_layers": []},
    }


@pytest.fixture
def all_go_mask(tmp_path):
    img = np.full((COMP_H, COMP_W), 255, dtype=np.uint8)
    out = tmp_path / "all_go.png"
    cv2.imwrite(str(out), img)
    return OcclusionMask(out, COMP_W, COMP_H)


@pytest.fixture
def split_mask(tmp_path):
    """Top half GO, bottom half CUTOFF (boundary at y=COMP_H//2)."""
    p = _write_mask(
        tmp_path,
        go_box=(0, 0, COMP_W, COMP_H // 2),
        cutoff_box=(0, COMP_H // 2, COMP_W, COMP_H),
    )
    return OcclusionMask(p, COMP_W, COMP_H)


@pytest.fixture
def all_cutoff_mask(tmp_path):
    img = np.zeros((COMP_H, COMP_W), dtype=np.uint8)
    out = tmp_path / "all_cutoff.png"
    cv2.imwrite(str(out), img)
    return OcclusionMask(out, COMP_W, COMP_H)


# ── Sealed precomp: detection-only, warning on violation ───────────


class TestSealedPrecompDetectionOnly:
    def test_sealed_wrapper_overlap_warns_no_move(self, split_mask):
        # Root wrapper layer (index=1, comp 1) sits in CUTOFF. Nested
        # comp 2 has one interior layer, also tagged TOP (translatable
        # in isolation) and also sitting in CUTOFF.
        wrapper = _layer(
            "Nested Precomp", index=1, uid="w-1", containing_comp_id=1,
            tag="CENTER", position=(100, 250),
        )
        interior = _layer(
            "Interior Text", index=1, uid="i-1", containing_comp_id=2,
            tag="TOP", position=(100, 250),
        )
        resolution = PlacementResolution(
            root_cid=1,
            sealed_precomp_cids={2},
            wrapper_keys={2: (1, 1)},
        )
        engine = OcclusionEngine(split_mask, "test", resolution=resolution)
        out, corrections = engine.run(_conformed([wrapper, interior]))

        assert corrections == [], (
            "sealed precomp members must never get a per-layer SOECorrection")
        assert out["layers"][0]["position"] == wrapper["position"], (
            "sealed wrapper must not be moved")
        assert out["layers"][1]["position"] == interior["position"], (
            "sealed interior layer must not be moved")
        warnings = out["warnings"].get("soe_scene_preserve_warnings") or []
        assert len(warnings) == 1
        assert "Nested Precomp" in warnings[0]
        assert "sealed precomp" in warnings[0]
        assert "not moved" in warnings[0]

    def test_sealed_precomp_clear_no_warning(self, all_go_mask):
        wrapper = _layer(
            "Nested Precomp", index=1, uid="w-1", containing_comp_id=1,
            tag="CENTER", position=(100, 50),
        )
        interior = _layer(
            "Interior Text", index=1, uid="i-1", containing_comp_id=2,
            tag="TOP", position=(100, 50),
        )
        resolution = PlacementResolution(
            root_cid=1, sealed_precomp_cids={2}, wrapper_keys={2: (1, 1)},
        )
        engine = OcclusionEngine(all_go_mask, "test", resolution=resolution)
        out, corrections = engine.run(_conformed([wrapper, interior]))
        assert corrections == []
        assert not out["warnings"].get("soe_scene_preserve_warnings")


# ── Preserving root scene: union AABB, detection-only ───────────────


class TestPreservingRootSceneDetectionOnly:
    def test_root_union_overlap_warns_no_move(self, split_mask):
        a = _layer("Outline A", index=1, uid="a-1", containing_comp_id=1,
                   tag="TOP", position=(60, 250))
        b = _layer("Outline B", index=2, uid="b-1", containing_comp_id=1,
                   tag="TOP", position=(140, 250))
        camera = _layer("Camera 1", index=3, uid="c-1", containing_comp_id=1,
                        tag=None, kind="camera", position=(100, 250))
        resolution = PlacementResolution(
            root_cid=1, preserving_scene_cids={1}, any_preserving_scene_unit=True,
        )
        engine = OcclusionEngine(split_mask, "test", resolution=resolution)
        out, corrections = engine.run(_conformed([a, b, camera]))

        # The camera itself was never part of the union (excluded by
        # kind) and still gets its ordinary SKIPPED_STRUCTURAL record —
        # unchanged pre-U3 behavior, not a move. Neither TOP outline
        # (the union members) gets ANY per-layer SOECorrection at all.
        moved_uids = {c.layer_uid for c in corrections}
        assert moved_uids == {"c-1"}, (
            "only the camera's ordinary structural-skip record may appear; "
            "the union members (a-1, b-1) must get zero per-layer corrections")
        assert corrections[0].strategy == "SKIPPED_STRUCTURAL"
        assert out["layers"][0]["position"] == a["position"]
        assert out["layers"][1]["position"] == b["position"]
        warnings = out["warnings"].get("soe_scene_preserve_warnings") or []
        assert len(warnings) == 1
        assert "camera scene" in warnings[0]
        assert "not moved" in warnings[0]

    def test_root_union_clear_no_warning(self, all_go_mask):
        a = _layer("Outline A", index=1, uid="a-1", containing_comp_id=1,
                   tag="TOP", position=(60, 50))
        resolution = PlacementResolution(
            root_cid=1, preserving_scene_cids={1}, any_preserving_scene_unit=True)
        engine = OcclusionEngine(all_go_mask, "test", resolution=resolution)
        out, corrections = engine.run(_conformed([a]))
        assert corrections == []
        assert not out["warnings"].get("soe_scene_preserve_warnings")


# ── Multi-member TRANSLATABLE cluster: rigid shared-offset move ────


class TestWholeUnitGroupedTranslate:
    def test_group_moves_with_shared_delta(self, split_mask):
        # Three TOP-tagged 1x1 layers in CUTOFF, all part of the SAME
        # cluster (identical centroid tuple, cluster size 3). 1x1
        # source_rect keeps the post-translate AABB pinned to the GO
        # edge (same reasoning as test_occlusion_engine.py's split_mask
        # docstring).
        centroid = (100.0, 250.0)
        layers = [
            _layer(f"Outline {i}", index=i, uid=f"o-{i}", containing_comp_id=1,
                  tag="TOP", position=(80.0 + i * 20.0, 250.0),
                  source_rect=(0.0, 0.0, 1.0, 1.0), anchor=(0.0, 0.0))
            for i in range(1, 4)
        ]
        originals = [list(l["position"]) for l in layers]
        resolution = PlacementResolution(
            root_cid=1,
            layer_centroids={(1, i): centroid for i in range(1, 4)},
            layer_cluster_sizes={(1, i): 3 for i in range(1, 4)},
            sealed_precomp_cids={999},  # non-empty so the gate is ON
        )
        engine = OcclusionEngine(split_mask, "test", resolution=resolution)
        out, corrections = engine.run(_conformed(layers))

        assert len(corrections) == 3
        deltas = set()
        for c, orig in zip(corrections, originals):
            assert c.strategy == "TRANSLATE"
            dx = c.corrected_position[0] - orig[0]
            dy = c.corrected_position[1] - orig[1]
            deltas.add((round(dx, 6), round(dy, 6)))
        assert len(deltas) == 1, (
            f"every member must share the IDENTICAL (dx, dy) — got {deltas}")
        # Relative spacing preserved (offset-preserving, not an absolute pin).
        out_positions = [l["position"] for l in out["layers"]]
        for i in range(1, 3):
            orig_gap = originals[i][0] - originals[i - 1][0]
            new_gap = out_positions[i][0] - out_positions[i - 1][0]
            assert new_gap == pytest.approx(orig_gap, abs=1e-6)

    def test_singleton_bucket_falls_through_to_per_layer(self, split_mask):
        """Cluster size 1 must degenerate to the ordinary per-layer
        TRANSLATE path unchanged (same proof pattern as the 2026-06-29
        gravity fix's group-of-1 degeneration)."""
        layer = _layer(
            "Solo Outline", index=1, uid="s-1", containing_comp_id=1,
            tag="TOP", position=(100, 250),
            source_rect=(0.0, 0.0, 1.0, 1.0), anchor=(0.0, 0.0),
        )
        resolution = PlacementResolution(
            root_cid=1,
            layer_centroids={(1, 1): (100.0, 250.0)},
            layer_cluster_sizes={(1, 1): 1},
            sealed_precomp_cids={999},
        )
        engine = OcclusionEngine(split_mask, "test", resolution=resolution)
        out, corrections = engine.run(_conformed([layer]))
        assert len(corrections) == 1
        assert corrections[0].strategy == "TRANSLATE"


class TestWholeUnitKeyedSibling:
    def test_keyed_member_makes_whole_unit_detection_only(self, split_mask):
        centroid = (100.0, 250.0)
        keyed = _layer(
            "Outline Keyed", index=1, uid="k-1", containing_comp_id=1,
            tag="TOP", position=(90.0, 250.0),
            is_keyed={"position": True, "scale": False, "anchor": False},
        )
        plain = _layer(
            "Outline Plain", index=2, uid="p-1", containing_comp_id=1,
            tag="TOP", position=(110.0, 250.0),
        )
        resolution = PlacementResolution(
            root_cid=1,
            layer_centroids={(1, 1): centroid, (1, 2): centroid},
            layer_cluster_sizes={(1, 1): 2, (1, 2): 2},
            sealed_precomp_cids={999},
        )
        engine = OcclusionEngine(split_mask, "test", resolution=resolution)
        out, corrections = engine.run(_conformed([keyed, plain]))

        assert len(corrections) == 2
        strategies = {c.layer_uid: c.strategy for c in corrections}
        assert strategies["k-1"] == "SKIPPED_KEYED"
        assert strategies["p-1"] == "SKIPPED_UNIT_KEYED"
        assert out["layers"][0]["position"] == keyed["position"]
        assert out["layers"][1]["position"] == plain["position"], (
            "non-keyed sibling must not move either — the group moves "
            "rigidly or not at all")


# ── Gate-off byte-identity ──────────────────────────────────────────


class TestGateOffByteIdentity:
    def test_omitted_resolution_matches_explicit_none(self, split_mask):
        layer = _layer(
            "title", position=(100, 250),
            source_rect=(0.0, 0.0, 1.0, 1.0), anchor=(0.0, 0.0),
        )
        out_a, corr_a = OcclusionEngine(split_mask, "test").run(
            _conformed([dict(layer)]))
        out_b, corr_b = OcclusionEngine(split_mask, "test", resolution=None).run(
            _conformed([dict(layer)]))
        assert out_a == out_b
        assert [c.__dict__ for c in corr_a] == [c.__dict__ for c in corr_b]

    def test_falsy_resolution_gate_stays_off(self, split_mask):
        """A resolution with no preserving unit and no sealed precomp
        (the tags-mode / camera-less-auto truth table) must leave SOE
        byte-identical to the per-layer path — the U3 Scope Decision."""
        layer = _layer(
            "title", position=(100, 250),
            source_rect=(0.0, 0.0, 1.0, 1.0), anchor=(0.0, 0.0),
        )
        empty_resolution = PlacementResolution(root_cid=1)
        assert not empty_resolution.any_preserving_scene_unit
        assert not empty_resolution.sealed_precomp_cids
        out_default, corr_default = OcclusionEngine(split_mask, "test").run(
            _conformed([dict(layer)]))
        out_gated, corr_gated = OcclusionEngine(
            split_mask, "test", resolution=empty_resolution).run(
            _conformed([dict(layer)]))
        assert out_default == out_gated
        assert [c.__dict__ for c in corr_default] == [c.__dict__ for c in corr_gated]
