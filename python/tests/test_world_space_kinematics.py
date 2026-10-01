# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_world_space_kinematics.py
Unit tests for World-Space Forward/Inverse Kinematics Engine (PR 2).
"""

import numpy as np
import pytest

from core.kinematics import (
    KinematicSolver,
    create_affine_matrix,
    decompose_affine_matrix,
)
from models.scrape_manifest import LayerModel


class TestAffineMatrixMath:
    """1. Tests 3x3 matrix construction, inversion, and decomposition."""

    def test_create_affine_matrix_identity(self):
        m = create_affine_matrix(position=[0, 0], scale=[100, 100], rotation_deg=0, anchor=[0, 0])
        np.testing.assert_allclose(m, np.eye(3), atol=1e-6)

    def test_create_affine_matrix_pure_translation(self):
        m = create_affine_matrix(position=[960, 540], scale=[100, 100], rotation_deg=0, anchor=[0, 0])
        expected = np.array([
            [1.0, 0.0, 960.0],
            [0.0, 1.0, 540.0],
            [0.0, 0.0, 1.0],
        ])
        np.testing.assert_allclose(m, expected, atol=1e-6)

    def test_create_affine_matrix_pure_scale(self):
        m = create_affine_matrix(position=[0, 0], scale=[200, 50], rotation_deg=0, anchor=[0, 0])
        expected = np.array([
            [2.0, 0.0, 0.0],
            [0.0, 0.5, 0.0],
            [0.0, 0.0, 1.0],
        ])
        np.testing.assert_allclose(m, expected, atol=1e-6)

    def test_create_affine_matrix_pure_rotation(self):
        m = create_affine_matrix(position=[0, 0], scale=[100, 100], rotation_deg=90, anchor=[0, 0])
        expected = np.array([
            [0.0, -1.0, 0.0],
            [1.0,  0.0, 0.0],
            [0.0,  0.0, 1.0],
        ])
        np.testing.assert_allclose(m, expected, atol=1e-6)

    def test_decompose_affine_matrix_roundtrip_identity(self):
        m = np.eye(3)
        res = decompose_affine_matrix(m, anchor=[0, 0])
        assert res["position"] == [0.0, 0.0, 0.0]
        assert res["scale"] == [100.0, 100.0, 100.0]
        assert res["rotation"] == 0.0

    def test_decompose_affine_matrix_roundtrip_translated_rotated_scaled(self):
        orig_pos = [500.0, 300.0]
        orig_scale = [150.0, 80.0]
        orig_rot = 45.0
        orig_anch = [50.0, 50.0]

        m = create_affine_matrix(orig_pos, orig_scale, orig_rot, orig_anch)
        res = decompose_affine_matrix(m, anchor=orig_anch)

        assert res["position"][0] == pytest.approx(orig_pos[0], abs=1e-2)
        assert res["position"][1] == pytest.approx(orig_pos[1], abs=1e-2)
        assert res["scale"][0] == pytest.approx(orig_scale[0], abs=1e-2)
        assert res["scale"][1] == pytest.approx(orig_scale[1], abs=1e-2)
        assert res["rotation"] == pytest.approx(orig_rot, abs=1e-2)
        assert res["anchor"] == [50.0, 50.0, 0.0]

    def test_decompose_affine_matrix_preserves_anchor_point(self):
        m = create_affine_matrix(position=[100, 100], scale=[100, 100], rotation_deg=0, anchor=[200, 200])
        res = decompose_affine_matrix(m, anchor=[200, 200])
        assert res["anchor"] == [200.0, 200.0, 0.0]
        assert res["position"] == [100.0, 100.0, 0.0]

    def test_negative_scale_flip_preserves_sign(self):
        m = create_affine_matrix(position=[0, 0], scale=[-100, 100], rotation_deg=0, anchor=[0, 0])
        res = decompose_affine_matrix(m, anchor=[0, 0])
        assert res["scale"][0] == pytest.approx(-100.0, abs=1e-2) or res["scale"][1] == pytest.approx(-100.0, abs=1e-2)


class TestKinematicSolverHierarchies:
    """2. Tests forward kinematics, double-scaling elimination, and null hierarchies."""

    def test_singleton_root_layer_scaling(self):
        """Single unparented layer scales uniformly with S=2.0."""
        layer = LayerModel(
            index=1,
            name="Title",
            uid="uid-title",
            position=[960.0, 540.0, 0.0],
            scale=[100.0, 100.0, 100.0],
            anchor=[960.0, 540.0, 0.0],
            parent_index=-1,
        )
        solver = KinematicSolver([layer], 1920, 1080, 3840, 2160, uniform_scale=2.0, center_offset=(0.0, 0.0))
        results = solver.solve()

        res = results["uid-title"]
        assert res["relayout_status"] == "OK"
        assert res["scale"][0] == pytest.approx(200.0, abs=1e-2)
        assert res["position"][0] == pytest.approx(1920.0, abs=1e-2)
        assert res["position"][1] == pytest.approx(1080.0, abs=1e-2)

    def test_parent_null_2x_scale_child_visual_scale_invariance(self):
        """Parent Null (Index 2) and Child (Index 1). Child visual scale must not double-scale."""
        parent_null = LayerModel(
            index=2,
            name="Master_Null",
            uid="uid-parent",
            position=[960.0, 540.0, 0.0],
            scale=[100.0, 100.0, 100.0],
            anchor=[0.0, 0.0, 0.0],
            parent_index=-1,
        )
        child_layer = LayerModel(
            index=1,
            name="Text_Child",
            uid="uid-child",
            position=[100.0, 50.0, 0.0],
            scale=[100.0, 100.0, 100.0],
            anchor=[0.0, 0.0, 0.0],
            parent_index=2,
        )

        solver = KinematicSolver(
            [child_layer, parent_null],
            1920, 1080, 3840, 2160,
            uniform_scale=2.0,
            center_offset=(0.0, 0.0),
        )
        results = solver.solve()

        p_res = results["uid-parent"]
        c_res = results["uid-child"]

        assert p_res["scale"][0] == pytest.approx(200.0, abs=1e-2)
        # In local space under parent scaling by 2x, child local scale remains 100%
        # so world effective scale = 2.0 * 1.0 = 2.0 (NO DOUBLE SCALING to 400%)
        assert c_res["scale"][0] == pytest.approx(100.0, abs=1e-2)
        assert c_res["position"] == [100.0, 50.0, 0.0]

    def test_3_tier_null_hierarchy_kinematics(self):
        """Child (1) -> Null B (2) -> Null A (3)."""
        null_a = LayerModel(index=3, name="Null_A", uid="u-a", position=[960.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=-1)
        null_b = LayerModel(index=2, name="Null_B", uid="u-b", position=[50.0, 50.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=3)
        child = LayerModel(index=1, name="Child", uid="u-c", position=[20.0, 10.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=2)

        solver = KinematicSolver([child, null_b, null_a], 1920, 1080, 3840, 2160, uniform_scale=2.0)
        results = solver.solve()

        assert results["u-a"]["scale"][0] == pytest.approx(200.0, abs=1e-2)
        assert results["u-b"]["scale"][0] == pytest.approx(100.0, abs=1e-2)
        assert results["u-c"]["scale"][0] == pytest.approx(100.0, abs=1e-2)

    def test_4_tier_null_hierarchy_kinematics(self):
        """Child (1) -> N1 (2) -> N2 (3) -> N3 (4)."""
        n3 = LayerModel(index=4, name="N3", uid="u4", position=[960.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=-1)
        n2 = LayerModel(index=3, name="N2", uid="u3", position=[10.0, 10.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=4)
        n1 = LayerModel(index=2, name="N1", uid="u2", position=[10.0, 10.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=3)
        c = LayerModel(index=1, name="C", uid="u1", position=[5.0, 5.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=2)

        solver = KinematicSolver([c, n1, n2, n3], 1920, 1080, 1080, 1920, uniform_scale=0.5625)
        results = solver.solve()

        assert results["u4"]["scale"][0] == pytest.approx(56.25, abs=1e-2)
        assert results["u3"]["scale"][0] == pytest.approx(100.0, abs=1e-2)
        assert results["u2"]["scale"][0] == pytest.approx(100.0, abs=1e-2)
        assert results["u1"]["scale"][0] == pytest.approx(100.0, abs=1e-2)

    def test_rotated_parent_null_zero_anchor_drift(self):
        """Parent Null rotated 45 degrees. Child local transforms match without drift."""
        parent = LayerModel(index=2, name="Rot_Parent", uid="u-p", position=[500.0, 500.0, 0.0], scale=[100.0, 100.0, 100.0], rotation=45.0, anchor=[0.0, 0.0, 0.0], parent_index=-1)
        child = LayerModel(index=1, name="Child", uid="u-c", position=[100.0, 0.0, 0.0], scale=[100.0, 100.0, 100.0], rotation=0.0, anchor=[0.0, 0.0, 0.0], parent_index=2)

        solver = KinematicSolver([child, parent], 1920, 1080, 3840, 2160, uniform_scale=2.0)
        results = solver.solve()

        p_res = results["u-p"]
        c_res = results["u-c"]

        assert p_res["rotation"] == pytest.approx(45.0, abs=1e-2)
        assert c_res["position"] == [100.0, 0.0, 0.0]
        assert c_res["rotation"] == 0.0

    def test_offset_anchor_point_parent_null_kinematics(self):
        """Parent Null with non-zero anchor point [960, 540]."""
        parent = LayerModel(index=2, name="Anchor_Parent", uid="u-p", position=[960.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0], anchor=[960.0, 540.0, 0.0], parent_index=-1)
        child = LayerModel(index=1, name="Child", uid="u-c", position=[960.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0], anchor=[0.0, 0.0, 0.0], parent_index=2)

        solver = KinematicSolver([child, parent], 1920, 1080, 3840, 2160, uniform_scale=2.0)
        results = solver.solve()

        assert results["u-p"]["anchor"] == [960.0, 540.0, 0.0]
        assert results["u-c"]["anchor"] == [0.0, 0.0, 0.0]

    def test_gravity_offset_applied_in_world_space(self):
        """Applies gravity offset to child layer."""
        layer = LayerModel(index=1, name="Title", uid="u-t", position=[960.0, 300.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=-1)
        solver = KinematicSolver([layer], 1920, 1080, 1080, 1920, uniform_scale=0.5625)
        results = solver.solve(gravity_offsets={"u-t": (0.0, -50.0)})

        res = results["u-t"]
        assert res["position"][1] == pytest.approx(300.0 * 0.5625 - 50.0, abs=1e-2)

    def test_singular_zero_scale_parent_null_triggers_center_world_guardrail(self):
        """Parent null with zero scale S=[0, 0] triggers Center-World guardrail for child."""
        parent = LayerModel(index=2, name="BlackHole_Null", uid="u-bh", position=[500.0, 500.0, 0.0], scale=[0.0, 0.0, 100.0], parent_index=-1)
        child = LayerModel(index=1, name="Trapped_Child", uid="u-trap", position=[100.0, 100.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=2)

        solver = KinematicSolver([child, parent], 1920, 1080, 1080, 1920, uniform_scale=0.5625)
        results = solver.solve()

        child_res = results["u-trap"]
        assert child_res["relayout_status"] == "FALLBACK_CENTER_WORLD"
        assert child_res["guardrail_reason"] == "SINGULAR_PARENT_MATRIX"
        assert child_res["position"] == [540.0, 960.0, 0.0]

    def test_non_parented_sibling_layers_independent(self):
        """Two independent layers transform without cross-talk."""
        l1 = LayerModel(index=1, name="L1", uid="u1", position=[100.0, 100.0, 0.0], scale=[100.0, 100.0, 100.0])
        l2 = LayerModel(index=2, name="L2", uid="u2", position=[800.0, 800.0, 0.0], scale=[100.0, 100.0, 100.0])

        solver = KinematicSolver([l1, l2], 1920, 1080, 3840, 2160, uniform_scale=2.0)
        res = solver.solve()

        assert res["u1"]["position"] == [200.0, 200.0, 0.0]
        assert res["u2"]["position"] == [1600.0, 1600.0, 0.0]

    def test_multiple_children_under_same_parent_null(self):
        """Parent null with 3 distinct children."""
        parent = LayerModel(index=4, name="Parent", uid="u-p", position=[960.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0])
        c1 = LayerModel(index=1, name="C1", uid="u1", position=[10.0, 0.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=4)
        c2 = LayerModel(index=2, name="C2", uid="u2", position=[20.0, 0.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=4)
        c3 = LayerModel(index=3, name="C3", uid="u3", position=[30.0, 0.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=4)

        solver = KinematicSolver([c1, c2, c3, parent], 1920, 1080, 3840, 2160, uniform_scale=2.0)
        res = solver.solve()

        assert res["u1"]["position"] == [10.0, 0.0, 0.0]
        assert res["u2"]["position"] == [20.0, 0.0, 0.0]
        assert res["u3"]["position"] == [30.0, 0.0, 0.0]

    def test_forward_kinematics_world_matrix_memoization(self):
        """Memo dictionary correctly caches computed world matrices."""
        parent = LayerModel(index=2, name="P", uid="up", position=[100.0, 100.0, 0.0], scale=[100.0, 100.0, 100.0])
        child = LayerModel(index=1, name="C", uid="uc", position=[10.0, 10.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=2)

        solver = KinematicSolver([child, parent], 1920, 1080, 3840, 2160, uniform_scale=2.0)
        memo = {}
        w_mat = solver.compute_world_matrix(1, memo)
        assert 1 in memo
        assert 2 in memo
        assert w_mat[0, 2] == pytest.approx(110.0, abs=1e-2)

    def test_high_res_4k_scaling_kinematic_precision(self):
        """HD to 4K kinematic precision."""
        layer = LayerModel(index=1, name="L", uid="u", position=[960.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0])
        solver = KinematicSolver([layer], 1920, 1080, 3840, 2160, uniform_scale=2.0)
        res = solver.solve()
        assert res["u"]["position"] == [1920.0, 1080.0, 0.0]

    def test_narrow_9x16_vertical_conform_kinematics(self):
        """HD to 9:16 vertical kinematic precision."""
        layer = LayerModel(index=1, name="L", uid="u", position=[960.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0])
        solver = KinematicSolver([layer], 1920, 1080, 1080, 1920, uniform_scale=0.5625)
        res = solver.solve()
        assert res["u"]["position"][0] == pytest.approx(540.0, abs=1e-2)
        assert res["u"]["position"][1] == pytest.approx(303.75, abs=1e-2)

    def test_widen_32x9_horizontal_conform_kinematics(self):
        """HD to 32:9 extreme width."""
        layer = LayerModel(index=1, name="L", uid="u", position=[960.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0])
        solver = KinematicSolver([layer], 1920, 1080, 3840, 1080, uniform_scale=1.0)
        res = solver.solve()
        assert res["u"]["scale"][0] == pytest.approx(100.0, abs=1e-2)

    def test_zero_rotation_and_identity_anchor_defaults(self):
        """Handles layers with missing rotation or anchor."""
        layer = LayerModel(index=1, name="L", uid="u", position=[100.0, 200.0, 0.0], scale=[100.0, 100.0, 100.0], anchor=None, rotation=None)
        solver = KinematicSolver([layer], 1920, 1080, 3840, 2160, uniform_scale=2.0)
        res = solver.solve()
        assert res["u"]["rotation"] == 0.0
        assert res["u"]["anchor"] == [0.0, 0.0, 0.0]

    def test_self_parent_loop_prevented_gracefully(self):
        """Layer with parent_index pointing to itself does not infinite recurse."""
        layer = LayerModel(index=1, name="SelfParent", uid="u-self", position=[100.0, 100.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=1)
        solver = KinematicSolver([layer], 1920, 1080, 3840, 2160, uniform_scale=2.0)
        res = solver.solve()
        assert res["u-self"]["relayout_status"] == "OK"
        assert res["u-self"]["position"] == [200.0, 200.0, 0.0]

    def test_missing_parent_index_in_layers_list_treated_as_root(self):
        """Layer pointing to parent_index that does not exist in layers list acts as root."""
        layer = LayerModel(index=1, name="OrphanChild", uid="u-orphan", position=[100.0, 100.0, 0.0], scale=[100.0, 100.0, 100.0], parent_index=999)
        solver = KinematicSolver([layer], 1920, 1080, 3840, 2160, uniform_scale=2.0)
        res = solver.solve()
        assert res["u-orphan"]["relayout_status"] == "OK"
        assert res["u-orphan"]["position"] == [200.0, 200.0, 0.0]
