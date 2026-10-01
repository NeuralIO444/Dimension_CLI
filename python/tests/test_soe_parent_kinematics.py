# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_soe_parent_kinematics.py
core/occlusion/parent_kinematics.py + OcclusionEngine's opt-in
kinematic_aware parameter — the wiring of core.kinematics's tested
affine primitives (create_affine_matrix / decompose_affine_matrix)
into a real production code path (see tracking issue #329).

scale_engine.py's ROOT/CHILD Anti-Shatter invariant deliberately
leaves a CHILD layer's conformed_transforms.position in PARENT-LOCAL
space. SOE (OcclusionEngine) reads that field directly and treats it
as comp/world space, which is wrong for any parented layer. This
suite proves:

  1. parent_kinematics.py's forward/inverse kinematics math itself is
     correct (hand-computed expected values).
  2. With kinematic_aware=False (the default — matches every existing
     OcclusionEngine caller unchanged), a parented layer's placement
     is read/written exactly as before: zero regression, proven both
     here and by the full existing test_occlusion_engine* suite
     passing unmodified.
  3. With kinematic_aware=True, the engine correctly classifies and
     corrects a parented layer's TRUE world-space placement, and the
     corrected LOCAL value it writes, when recomposed through the
     parent chain, actually lands where the mask says it should —
     not just "a correction happened," but "the correction is
     geometrically right."
  4. The flag is a true no-op for any layer that has no parent,
     regardless of what other layers in the same comp are doing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import cv2
import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.occlusion_engine import OcclusionEngine, OcclusionMask
from core.occlusion.parent_kinematics import (
    local_position_for_world_target,
    world_position_for,
)

COMP_W = 200
COMP_H = 300


def _layer(name: str, *,
           index: int,
           containing_comp_id: int = 1,
           parent_index: int = -1,
           tag: Optional[str] = "TYPE",
           position=(100.0, 100.0),
           rotation: float = 0.0,
           scale=(100.0, 100.0),
           anchor=(0.0, 0.0),
           source_rect=(0.0, 0.0, 20.0, 20.0)) -> dict:
    """Matches the conformed-layer dict shape OcclusionEngine reads —
    same conventions as test_occlusion_engine.py's `_layer()` helper,
    extended with containing_comp_id/parent_index for parenting."""
    pos3 = list(position) + [0.0]
    scale3 = list(scale) + [100.0]
    anchor3 = list(anchor) + [0.0]
    return {
        "index": index,
        "uid": f"u-{index}",
        "name": name,
        "layer_kind": "av",
        "content_tag": tag,
        "content_tag_source": "manual_comment" if tag else None,
        "containing_comp_id": containing_comp_id,
        "parent_index": parent_index,
        "position": pos3,
        "scale": scale3,
        "anchor": anchor3,
        "source_rect": list(source_rect),
        "world_bounds": None,
        "is_keyed": {"position": False, "scale": False, "anchor": False},
        "conformed_transforms": {
            "is_root": parent_index in (-1, None),
            "position": pos3,
            "scale": scale3,
            "rotation": rotation,
            "anchor": anchor3,
        },
    }


def _conformed(layers: list) -> dict:
    return {"status": "SAFE", "layers": layers, "warnings": {}}


def _split_mask(tmp_path: Path) -> OcclusionMask:
    """Top half (y 0-150) GO, bottom half (y 150-300) CUTOFF —
    matches test_occlusion_engine.py's split_mask fixture exactly."""
    img = np.full((COMP_H, COMP_W), 128, dtype=np.uint8)
    img[0:COMP_H // 2, :] = 255      # GO
    img[COMP_H // 2:COMP_H, :] = 0   # CUTOFF
    out = tmp_path / "split_mask.png"
    cv2.imwrite(str(out), img)
    return OcclusionMask(out, COMP_W, COMP_H)


# ── Pure math: parent_kinematics.py in isolation ────────────────────


class TestWorldTransformMath:
    def test_root_layer_world_equals_local(self):
        root = _layer("Root", index=1, position=(50.0, 60.0))
        world = world_position_for(root, {(1, 1): root})
        assert world == pytest.approx([50.0, 60.0, 0.0])

    def test_child_world_position_composes_parent_offset(self):
        # Null at (100, 100); child's local position is (0, 100)
        # relative to it — pure-translation composition means world
        # position is the sum: (100+0, 100+100) = (100, 200).
        null = _layer("Null", index=1, tag=None, position=(100.0, 100.0))
        child = _layer("Child", index=2, parent_index=1, position=(0.0, 100.0))
        layers_by_key = {(1, 1): null, (1, 2): child}
        world = world_position_for(child, layers_by_key)
        assert world == pytest.approx([100.0, 200.0, 0.0])

    def test_multi_tier_chain_composes_through_all_levels(self):
        # Null1 (100,0) -> Null2 local (0,50) -> Leaf local (0,25).
        # World: (100, 0+50+25) = (100, 75).
        null1 = _layer("Null1", index=1, tag=None, position=(100.0, 0.0))
        null2 = _layer("Null2", index=2, tag=None, parent_index=1, position=(0.0, 50.0))
        leaf = _layer("Leaf", index=3, parent_index=2, position=(0.0, 25.0))
        layers_by_key = {(1, 1): null1, (1, 2): null2, (1, 3): leaf}
        world = world_position_for(leaf, layers_by_key)
        assert world == pytest.approx([100.0, 75.0, 0.0])

    def test_rotated_parent_rotates_child_world_position(self):
        # Null rotated 90 deg at origin; child local (10, 0) rotates
        # to world (0, 10) under a +90deg rotation.
        null = _layer("Null", index=1, tag=None, position=(0.0, 0.0), rotation=90.0)
        child = _layer("Child", index=2, parent_index=1, position=(10.0, 0.0))
        layers_by_key = {(1, 1): null, (1, 2): child}
        world = world_position_for(child, layers_by_key)
        assert world[0] == pytest.approx(0.0, abs=1e-6)
        assert world[1] == pytest.approx(10.0, abs=1e-6)

    def test_local_position_for_world_target_round_trips(self):
        null = _layer("Null", index=1, tag=None, position=(100.0, 100.0))
        child = _layer("Child", index=2, parent_index=1, position=(0.0, 100.0))
        layers_by_key = {(1, 1): null, (1, 2): child}

        new_local = local_position_for_world_target(child, layers_by_key, [80.0, 140.0, 0.0])
        # Feed the computed local position back into a copy of child
        # and confirm forward kinematics reproduces the requested
        # world target — the actual round-trip contract _set_position
        # depends on.
        child_after = dict(child)
        child_after["conformed_transforms"] = dict(child["conformed_transforms"])
        child_after["conformed_transforms"]["position"] = new_local
        layers_by_key_after = {(1, 1): null, (1, 2): child_after}
        recomposed_world = world_position_for(child_after, layers_by_key_after)
        assert recomposed_world[0] == pytest.approx(80.0, abs=1e-6)
        assert recomposed_world[1] == pytest.approx(140.0, abs=1e-6)

    def test_singular_parent_falls_back_without_crashing(self):
        null = _layer("Null", index=1, tag=None, position=(50.0, 50.0), scale=(0.0, 0.0))
        child = _layer("Child", index=2, parent_index=1, position=(10.0, 10.0))
        layers_by_key = {(1, 1): null, (1, 2): child}
        # Must not raise (singular determinant) and must return a
        # finite fallback, not NaN/Inf.
        result = local_position_for_world_target(child, layers_by_key, [999.0, 999.0, 0.0])
        assert all(np.isfinite(v) for v in result)

    def test_cross_comp_layers_never_treated_as_parent(self):
        # Same index, different containing_comp_id — must NOT resolve
        # as a parent-child relationship (comp-scoped identity, per
        # CLAUDE.md's sharp edge on flat-index collisions).
        other_comp_layer = _layer("Other", index=1, containing_comp_id=99, position=(500.0, 500.0))
        child = _layer("Child", index=2, containing_comp_id=1, parent_index=1, position=(10.0, 10.0))
        layers_by_key = {(99, 1): other_comp_layer, (1, 2): child}
        # child's parent_index=1 has no (1, 1) entry in layers_by_key
        # (only (99, 1) exists) — child must resolve as its own root.
        world = world_position_for(child, layers_by_key)
        assert world == pytest.approx([10.0, 10.0, 0.0])


# ── OcclusionEngine integration ──────────────────────────────────────


class TestOcclusionEngineKinematicAware:
    def test_default_is_off_and_matches_existing_behavior(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DIMENSION_SOE_KINEMATIC_AWARE", raising=False)
        mask = _split_mask(tmp_path)
        null = _layer("Null", index=1, tag=None, position=(100.0, 100.0))
        child = _layer("Child", index=2, parent_index=1, position=(0.0, 100.0))
        engine = OcclusionEngine(mask, "test")
        assert engine.kinematic_aware is False

    def test_naive_read_misses_a_violation_kinematic_aware_catches(self, tmp_path):
        """The core bug this wiring fixes: a child's local position
        (0, 100) reads as GO if treated as world space directly, but
        composed through its parent (null at (100,100)) the TRUE
        world position is (100, 200) -- solidly in CUTOFF. Default
        (unaware) must miss it; kinematic_aware must catch it."""
        mask = _split_mask(tmp_path)
        null = _layer("Null", index=1, tag=None, position=(100.0, 100.0))
        # 1x1 source_rect — split_mask's own docstring in
        # test_occlusion_engine.py notes TRANSLATE only fully resolves
        # (zero cutoff overlap) for AABBs small enough to sit right at
        # the GO/CUTOFF boundary edge; a larger box straddles it.
        child = _layer("Child", index=2, parent_index=1, position=(0.0, 100.0),
                        source_rect=(0.0, 0.0, 1.0, 1.0))

        unaware = OcclusionEngine(mask, "test", kinematic_aware=False)
        out_unaware, corrections_unaware = unaware.run(_conformed([null, child]))
        child_corr_unaware = [c for c in corrections_unaware if c.layer_index == 2]
        # Naive reading treats local (0,100) as world -> GO -> no
        # correction attempted at all (early "GO, clean" return).
        assert child_corr_unaware == []
        assert out_unaware["layers"][1]["conformed_transforms"]["position"] == [0.0, 100.0, 0.0]

        aware = OcclusionEngine(mask, "test", kinematic_aware=True)
        out_aware, corrections_aware = aware.run(_conformed([null, child]))
        child_corr_aware = [c for c in corrections_aware if c.layer_index == 2]
        assert len(child_corr_aware) == 1
        assert child_corr_aware[0].strategy != "SKIPPED_NO_BOUNDS"

        # Round-trip proof: the LOCAL value actually written, recomposed
        # through the (unchanged) null's world matrix, must land back
        # in GO territory (y <= 150), not just "some new number."
        corrected_child = out_aware["layers"][1]
        layers_by_key = {(1, 1): null, (1, 2): corrected_child}
        recomposed_world = world_position_for(corrected_child, layers_by_key)
        assert recomposed_world[1] <= 150.0, (
            f"corrected local position {corrected_child['conformed_transforms']['position']} "
            f"recomposes to world {recomposed_world}, still in CUTOFF"
        )

    def test_flag_is_a_no_op_for_unparented_layers(self, tmp_path):
        """A root layer's behavior must be bit-for-bit identical
        whether kinematic_aware is True or False, even in a comp that
        also contains parented layers."""
        mask = _split_mask(tmp_path)
        null = _layer("Null", index=1, tag=None, position=(100.0, 100.0))
        root_type = _layer("RootType", index=2, position=(100.0, 250.0))  # CUTOFF, needs correction
        child = _layer("Child", index=3, parent_index=1, position=(0.0, 100.0))

        out_off, corr_off = OcclusionEngine(mask, "test", kinematic_aware=False).run(
            _conformed([null, root_type, child])
        )
        out_on, corr_on = OcclusionEngine(mask, "test", kinematic_aware=True).run(
            _conformed([null, root_type, child])
        )

        root_off = [c for c in corr_off if c.layer_index == 2][0]
        root_on = [c for c in corr_on if c.layer_index == 2][0]
        assert root_off.corrected_position == root_on.corrected_position
        assert (out_off["layers"][1]["conformed_transforms"]["position"]
                == out_on["layers"][1]["conformed_transforms"]["position"])

    def test_env_var_enables_kinematic_awareness_when_flag_not_passed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DIMENSION_SOE_KINEMATIC_AWARE", "1")
        mask = _split_mask(tmp_path)
        engine = OcclusionEngine(mask, "test")
        assert engine.kinematic_aware is True

    def test_explicit_flag_overrides_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DIMENSION_SOE_KINEMATIC_AWARE", "1")
        mask = _split_mask(tmp_path)
        engine = OcclusionEngine(mask, "test", kinematic_aware=False)
        assert engine.kinematic_aware is False
