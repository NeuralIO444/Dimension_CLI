# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_matrix_math.py — Slot 12.5 Stage A / M0.

Direct unit tests for `core.matrix_math`. The module has zero standalone
test coverage today; it is exercised only indirectly via `scale_engine`
integration paths. M0 lands these tests BEFORE Stage C's cross-comp +
collapse-aware matrix extension (M1) so the foundation is verified
before it is extended.

Coverage per the Slot 12.5 implementation plan:
    - mat4 primitives (identity, mul, translate, scale, rot_x/y/z, inv, transform_point)
    - layer_to_matrix (AE transform order)
    - build_world_matrix (parent-chain composition; root-layer fall-through; cycle handling)
    - world_to_local_position (round-trip via matrix inverse)
    - conform_layer_world_space (full pass: world remap → local space)
    - has_nonuniform_parent_scale, has_3d_rotated_parent (predicate accuracy)
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
))

from core.matrix_math import (
    build_world_matrix,
    conform_layer_world_space,
    has_3d_rotated_parent,
    has_nonuniform_parent_scale,
    layer_to_matrix,
    mat4_identity,
    mat4_inv,
    mat4_mul,
    mat4_rot_x,
    mat4_rot_y,
    mat4_rot_z,
    mat4_scale,
    mat4_transform_point,
    mat4_translate,
    world_to_local_position,
    SingularMatrixError,
)


# Tolerance for float matrix / point comparisons. Tight enough that a real
# computation error fails the test; loose enough that ULP drift from
# trig + matmul doesn't false-alarm.
TOL = 1e-9


def _approx_list(actual, expected, tol=TOL):
    """List/tuple comparator with elementwise tolerance."""
    assert len(actual) == len(expected), (
        f"length mismatch: {len(actual)} vs {len(expected)}"
    )
    for i, (a, e) in enumerate(zip(actual, expected)):
        assert a == pytest.approx(e, abs=tol), (
            f"element {i}: {a} != {e}"
        )


# ---------------------------------------------------------------------------
# Mat4 primitives
# ---------------------------------------------------------------------------

class TestMat4Identity:
    def test_identity_has_16_elements(self):
        m = mat4_identity()
        assert len(m) == 16

    def test_identity_diagonal_ones_off_diagonal_zeros(self):
        m = mat4_identity()
        for row in range(4):
            for col in range(4):
                expected = 1.0 if row == col else 0.0
                assert m[row * 4 + col] == expected


class TestMat4Mul:
    def test_identity_times_any_is_any(self):
        m = mat4_translate(3, 5, 7)
        product = mat4_mul(mat4_identity(), m)
        _approx_list(product, m)

    def test_any_times_identity_is_any(self):
        m = mat4_rot_z(37)
        product = mat4_mul(m, mat4_identity())
        _approx_list(product, m)

    def test_associativity(self):
        a = mat4_translate(1, 2, 3)
        b = mat4_rot_z(45)
        c = mat4_scale(2, 3, 4)
        left = mat4_mul(mat4_mul(a, b), c)
        right = mat4_mul(a, mat4_mul(b, c))
        _approx_list(left, right)

    def test_non_commutativity(self):
        # T * R != R * T for non-trivial T and R.
        t = mat4_translate(10, 0, 0)
        r = mat4_rot_z(90)
        tr = mat4_mul(t, r)
        rt = mat4_mul(r, t)
        # Verify they differ at the translation column.
        differs = any(abs(tr[i] - rt[i]) > 1e-6 for i in (3, 7, 11))
        assert differs


class TestMat4Translate:
    def test_translate_moves_point(self):
        m = mat4_translate(10, 20, 30)
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [10, 20, 30])

    def test_translate_preserves_existing_position(self):
        m = mat4_translate(5, 6, 7)
        p = mat4_transform_point(m, 1, 2, 3)
        _approx_list(p, [6, 8, 10])


class TestMat4Scale:
    def test_scale_scales_point(self):
        m = mat4_scale(2, 3, 4)
        p = mat4_transform_point(m, 1, 1, 1)
        _approx_list(p, [2, 3, 4])

    def test_scale_about_origin_only(self):
        # Pure scale should leave origin fixed.
        m = mat4_scale(5, 5, 5)
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [0, 0, 0])


class TestMat4Rotations:
    def test_rot_z_90_rotates_x_to_y(self):
        m = mat4_rot_z(90)
        p = mat4_transform_point(m, 1, 0, 0)
        _approx_list(p, [0, 1, 0])

    def test_rot_z_negative_90_rotates_x_to_negative_y(self):
        m = mat4_rot_z(-90)
        p = mat4_transform_point(m, 1, 0, 0)
        _approx_list(p, [0, -1, 0])

    def test_rot_x_90_rotates_y_to_z(self):
        m = mat4_rot_x(90)
        p = mat4_transform_point(m, 0, 1, 0)
        _approx_list(p, [0, 0, 1])

    def test_rot_y_90_rotates_z_to_x(self):
        m = mat4_rot_y(90)
        p = mat4_transform_point(m, 0, 0, 1)
        _approx_list(p, [1, 0, 0])

    def test_rot_360_returns_to_origin(self):
        m = mat4_rot_z(360)
        p = mat4_transform_point(m, 7, 0, 0)
        _approx_list(p, [7, 0, 0], tol=1e-6)


class TestMat4Inv:
    def test_identity_inverse_is_identity(self):
        inv = mat4_inv(mat4_identity())
        _approx_list(inv, mat4_identity())

    def test_translate_inverse_round_trip(self):
        m = mat4_translate(11, -22, 33)
        inv = mat4_inv(m)
        assert inv is not None
        round_trip = mat4_mul(m, inv)
        _approx_list(round_trip, mat4_identity())

    def test_scale_inverse_round_trip(self):
        m = mat4_scale(2, 3, 4)
        inv = mat4_inv(m)
        assert inv is not None
        round_trip = mat4_mul(m, inv)
        _approx_list(round_trip, mat4_identity())

    def test_rotation_inverse_round_trip(self):
        m = mat4_rot_z(37.5)
        inv = mat4_inv(m)
        assert inv is not None
        round_trip = mat4_mul(m, inv)
        _approx_list(round_trip, mat4_identity(), tol=1e-9)

    def test_composed_inverse_round_trip(self):
        # A non-trivial composition: T * Rz * S.
        m = mat4_mul(
            mat4_translate(5, 6, 7),
            mat4_mul(mat4_rot_z(45), mat4_scale(2, 3, 4)),
        )
        inv = mat4_inv(m)
        assert inv is not None
        round_trip = mat4_mul(m, inv)
        _approx_list(round_trip, mat4_identity(), tol=1e-8)

    def test_singular_matrix_raises_exception(self):
        # All-zero matrix is singular (det = 0).
        singular = [0.0] * 16
        with pytest.raises(SingularMatrixError):
            mat4_inv(singular)

    def test_zero_scale_singular(self):
        # Scale with one axis zero is singular.
        singular = mat4_scale(0, 1, 1)
        with pytest.raises(SingularMatrixError):
            mat4_inv(singular)


class TestMat4TransformPoint:
    def test_identity_leaves_point_unchanged(self):
        p = mat4_transform_point(mat4_identity(), 3, 5, 7)
        _approx_list(p, [3, 5, 7])

    def test_combined_transform_applies_in_order(self):
        # Translate then scale: a single composed matrix T * S applied to
        # point p should give T(S(p)). Verify against direct application.
        m = mat4_mul(mat4_translate(10, 20, 30), mat4_scale(2, 2, 2))
        p = mat4_transform_point(m, 1, 1, 1)
        _approx_list(p, [12, 22, 32])


# ---------------------------------------------------------------------------
# layer_to_matrix — AE transform order: T(pos) * Rz * Ry * Rx * R(ori) * S * T(-anchor)
# ---------------------------------------------------------------------------

class TestLayerToMatrix:
    def test_default_layer_is_identity(self):
        m = layer_to_matrix(
            position=[0, 0, 0],
            anchor=[0, 0, 0],
            scale_pct=[100, 100, 100],
            rotation_z=0,
        )
        _approx_list(m, mat4_identity())

    def test_position_only_is_pure_translation(self):
        m = layer_to_matrix(
            position=[100, 200, 0],
            anchor=[0, 0, 0],
            scale_pct=[100, 100, 100],
            rotation_z=0,
        )
        # Origin should move to position.
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [100, 200, 0])

    def test_scale_about_zero_anchor(self):
        m = layer_to_matrix(
            position=[0, 0, 0],
            anchor=[0, 0, 0],
            scale_pct=[200, 200, 100],
            rotation_z=0,
        )
        # Point (1, 1, 1) → (2, 2, 1).
        p = mat4_transform_point(m, 1, 1, 1)
        _approx_list(p, [2, 2, 1])

    def test_anchor_pivot_unaffected_by_scale(self):
        # A non-zero anchor IS the pivot of scale + rotation. AE scales
        # about the anchor point: scale=200% means a layer at position=anchor
        # has its anchor land at position regardless of scale. So
        # transform_point(anchor) == position.
        m = layer_to_matrix(
            position=[100, 100, 0],
            anchor=[50, 50, 0],
            scale_pct=[200, 200, 100],
            rotation_z=0,
        )
        p = mat4_transform_point(m, 50, 50, 0)
        _approx_list(p, [100, 100, 0])

    def test_rotation_z_about_anchor(self):
        # Z-rotation pivots about the anchor. anchor=(0,0), rotate 90° →
        # point (1,0) goes to (0,1).
        m = layer_to_matrix(
            position=[0, 0, 0],
            anchor=[0, 0, 0],
            scale_pct=[100, 100, 100],
            rotation_z=90,
        )
        p = mat4_transform_point(m, 1, 0, 0)
        _approx_list(p, [0, 1, 0])

    def test_full_transform_order_against_manual_composition(self):
        # Build the same matrix manually via the AE order and verify
        # layer_to_matrix produces it.
        position = [50, 100, 0]
        anchor = [25, 30, 0]
        scale_pct = [150, 75, 100]
        rotation_z = 30
        # Manual: T(pos) * Rz * S * T(-anchor)
        manual = mat4_translate(-anchor[0], -anchor[1], -anchor[2])
        manual = mat4_mul(
            mat4_scale(
                scale_pct[0] / 100, scale_pct[1] / 100, scale_pct[2] / 100
            ),
            manual,
        )
        manual = mat4_mul(mat4_rot_z(rotation_z), manual)
        manual = mat4_mul(
            mat4_translate(position[0], position[1], position[2]), manual
        )

        actual = layer_to_matrix(
            position=position,
            anchor=anchor,
            scale_pct=scale_pct,
            rotation_z=rotation_z,
        )
        _approx_list(actual, manual)

    def test_3d_rotations_compose_in_order_rx_ry_rz(self):
        # Pure X-rotation first, then Y, then Z. The composition order
        # documented in the module is Rz * Ry * Rx * R(ori) * S * T(-anchor).
        m = layer_to_matrix(
            position=[0, 0, 0],
            anchor=[0, 0, 0],
            scale_pct=[100, 100, 100],
            rotation_z=0,
            rotation_x=90,
        )
        # rot_x(90) sends (0, 1, 0) to (0, 0, 1).
        p = mat4_transform_point(m, 0, 1, 0)
        _approx_list(p, [0, 0, 1])

    def test_orientation_applied_before_individual_rotations(self):
        # orientation=(90, 0, 0) is X-axis 90° rotation. With no other
        # rotations: (0,1,0) → (0,0,1).
        m = layer_to_matrix(
            position=[0, 0, 0],
            anchor=[0, 0, 0],
            scale_pct=[100, 100, 100],
            rotation_z=0,
            orientation=[90, 0, 0],
        )
        p = mat4_transform_point(m, 0, 1, 0)
        _approx_list(p, [0, 0, 1])


# ---------------------------------------------------------------------------
# build_world_matrix
# ---------------------------------------------------------------------------

class TestBuildWorldMatrix:
    def test_layer_not_in_dict_returns_identity(self):
        m = build_world_matrix(99, {})
        _approx_list(m, mat4_identity())

    def test_root_layer_returns_its_local_matrix(self):
        layers = {
            1: {
                "position": [100, 200, 0],
                "scale": [100, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": -1,
            },
        }
        m = build_world_matrix(1, layers)
        expected = layer_to_matrix(
            position=[100, 200, 0],
            anchor=[0, 0, 0],
            scale_pct=[100, 100, 100],
            rotation_z=0,
        )
        _approx_list(m, expected)

    def test_parent_index_negative_one_falls_through(self):
        # parent_index = -1 explicitly means "no parent" in AE land.
        layers = {
            1: {
                "position": [10, 10, 0],
                "scale": [100, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": -1,
            },
        }
        m = build_world_matrix(1, layers)
        # World position equals local position (no parent contribution).
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [10, 10, 0])

    def test_parent_index_not_in_dict_falls_through(self):
        # parent_index points at a layer NOT in the dict — should behave
        # as if root (no parent contribution).
        layers = {
            1: {
                "position": [10, 10, 0],
                "scale": [100, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": 99,
            },
        }
        m = build_world_matrix(1, layers)
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [10, 10, 0])

    def test_parent_chain_composes_translation(self):
        # Parent at (100, 0), child at (10, 0). Child's world position = (110, 0).
        layers = {
            1: {
                "position": [100, 0, 0],
                "scale": [100, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": -1,
            },
            2: {
                "position": [10, 0, 0],
                "scale": [100, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": 1,
            },
        }
        m = build_world_matrix(2, layers)
        # World position of child's local origin.
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [110, 0, 0])

    def test_parent_chain_composes_scale_then_translate(self):
        # Parent: scale=200%, position=(0,0). Child: position=(10, 10),
        # scale=100%. The child's local origin in the parent's space is
        # (10, 10); the parent then scales that by 2x → world (20, 20).
        layers = {
            1: {
                "position": [0, 0, 0],
                "scale": [200, 200, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": -1,
            },
            2: {
                "position": [10, 10, 0],
                "scale": [100, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": 1,
            },
        }
        m = build_world_matrix(2, layers)
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [20, 20, 0])

    def test_grandparent_chain_composes(self):
        # Three-level chain. Each contributes a +10 translation along X.
        layers = {
            1: {
                "position": [10, 0, 0],
                "scale": [100, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": -1,
            },
            2: {
                "position": [10, 0, 0],
                "scale": [100, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": 1,
            },
            3: {
                "position": [10, 0, 0],
                "scale": [100, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": 2,
            },
        }
        m = build_world_matrix(3, layers)
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [30, 0, 0])

    def test_two_node_cycle_handled_without_recursion_error(self):
        # 2-node cycle: 1's parent is 2; 2's parent is 1. Pre-Stage-B
        # this recursed to RecursionError; the Stage B cycle guard halts
        # the walk at the cycle point and returns a 4×4 matrix instead.
        layers = {
            1: {
                "position": [10, 0, 0],
                "scale": [100, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": 2,
            },
            2: {
                "position": [20, 0, 0],
                "scale": [100, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": 1,
            },
        }
        m = build_world_matrix(1, layers)
        assert len(m) == 16

    def test_three_node_cycle_handled_without_recursion_error(self):
        # Three-way cycle 1 → 2 → 3 → 1.
        layers = {
            1: {"position": [1, 0, 0], "scale": [100, 100, 100],
                "rotation_z": 0, "anchor": [0, 0, 0], "parent_index": 2},
            2: {"position": [1, 0, 0], "scale": [100, 100, 100],
                "rotation_z": 0, "anchor": [0, 0, 0], "parent_index": 3},
            3: {"position": [1, 0, 0], "scale": [100, 100, 100],
                "rotation_z": 0, "anchor": [0, 0, 0], "parent_index": 1},
        }
        m = build_world_matrix(1, layers)
        assert len(m) == 16

    def test_self_referential_parent_handled(self):
        # A layer that parents itself (parent_index == own index).
        layers = {
            1: {"position": [5, 5, 0], "scale": [100, 100, 100],
                "rotation_z": 0, "anchor": [0, 0, 0], "parent_index": 1},
        }
        m = build_world_matrix(1, layers)
        assert len(m) == 16


# ---------------------------------------------------------------------------
# world_to_local_position
# ---------------------------------------------------------------------------

class TestWorldToLocalPosition:
    def test_root_parent_returns_world_unchanged(self):
        result = world_to_local_position([100, 200, 0], -1, {})
        _approx_list(result, [100, 200, 0])

    def test_parent_not_in_dict_returns_world_unchanged(self):
        result = world_to_local_position([100, 200, 0], 99, {})
        _approx_list(result, [100, 200, 0])

    def test_round_trip_through_translated_parent(self):
        # Parent at (50, 50). A world position of (100, 100) should map
        # to local (50, 50) inside the parent's space.
        layers = {
            1: {
                "position": [50, 50, 0],
                "scale": [100, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": -1,
            },
        }
        local = world_to_local_position([100, 100, 0], 1, layers)
        _approx_list(local, [50, 50, 0])

    def test_round_trip_through_scaled_parent(self):
        # Parent scale 200%. World (40, 60) → local (20, 30).
        layers = {
            1: {
                "position": [0, 0, 0],
                "scale": [200, 200, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": -1,
            },
        }
        local = world_to_local_position([40, 60, 0], 1, layers)
        _approx_list(local, [20, 30, 0])

    def test_round_trip_full_circle(self):
        # Pick a parent, compute child's world position, then convert
        # back to local — should equal original local position.
        layers = {
            1: {
                "position": [25, 50, 0],
                "scale": [150, 80, 100],
                "rotation_z": 15,
                "anchor": [10, 5, 0],
                "parent_index": -1,
            },
            2: {
                "position": [30, 40, 0],
                "scale": [100, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": 1,
            },
        }
        # Child's world position.
        child_world = build_world_matrix(2, layers)
        wx, wy, wz = child_world[3], child_world[7], child_world[11]
        # Round-trip: world → local via parent inverse.
        local = world_to_local_position([wx, wy, wz], 1, layers)
        _approx_list(local, [30, 40, 0], tol=1e-7)

    def test_singular_parent_matrix_raises_exception(self):
        # Parent with a zero-scale axis is singular — world_to_local_position raises SingularMatrixError.
        layers = {
            1: {
                "position": [0, 0, 0],
                "scale": [0, 100, 100],  # singular: x scale = 0
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": -1,
            },
        }
        with pytest.raises(SingularMatrixError):
            world_to_local_position([10, 20, 30], 1, layers)


# ---------------------------------------------------------------------------
# conform_layer_world_space
# ---------------------------------------------------------------------------

class TestConformLayerWorldSpace:
    def _root_layer(self, index, position):
        return {
            "index": index,
            "position": list(position),
            "scale": [100, 100, 100],
            "rotation_z": 0,
            "anchor": [0, 0, 0],
            "parent_index": -1,
        }

    def test_identity_conform_unchanged(self):
        # S=1, src_center == tgt_center → world position passes through.
        layer = self._root_layer(1, [200, 300, 0])
        layers_by_index = {1: layer}
        result = conform_layer_world_space(
            layer,
            layers_by_index,
            src_center=[100, 100, 0],
            tgt_center=[100, 100, 0],
            uniform_scale=1.0,
        )
        _approx_list(result, [200, 300, 0])

    def test_uniform_scale_2x_root_layer(self):
        # World position (200, 200), src=(0,0), tgt=(0,0), S=2 → (400, 400).
        layer = self._root_layer(1, [200, 200, 0])
        layers_by_index = {1: layer}
        result = conform_layer_world_space(
            layer,
            layers_by_index,
            src_center=[0, 0, 0],
            tgt_center=[0, 0, 0],
            uniform_scale=2.0,
        )
        _approx_list(result, [400, 400, 0])

    def test_center_remap_off_origin(self):
        # src_center=(100, 100), tgt_center=(500, 500), S=1.
        # World (150, 150) → (150-100)*1 + 500 = 550, same for y → (550, 550).
        layer = self._root_layer(1, [150, 150, 0])
        layers_by_index = {1: layer}
        result = conform_layer_world_space(
            layer,
            layers_by_index,
            src_center=[100, 100, 0],
            tgt_center=[500, 500, 0],
            uniform_scale=1.0,
        )
        _approx_list(result, [550, 550, 0])

    def test_scale_z_false_leaves_z_unchanged(self):
        layer = {
            "index": 1,
            "position": [100, 100, 50],
            "scale": [100, 100, 100],
            "rotation_z": 0,
            "anchor": [0, 0, 0],
            "parent_index": -1,
        }
        layers_by_index = {1: layer}
        result = conform_layer_world_space(
            layer,
            layers_by_index,
            src_center=[0, 0, 0],
            tgt_center=[0, 0, 0],
            uniform_scale=2.0,
            scale_z=False,
        )
        # X and Y scaled 2x; Z unchanged.
        _approx_list(result, [200, 200, 50])

    def test_scale_z_true_scales_z_by_s(self):
        layer = {
            "index": 1,
            "position": [100, 100, 50],
            "scale": [100, 100, 100],
            "rotation_z": 0,
            "anchor": [0, 0, 0],
            "parent_index": -1,
        }
        layers_by_index = {1: layer}
        result = conform_layer_world_space(
            layer,
            layers_by_index,
            src_center=[0, 0, 0],
            tgt_center=[0, 0, 0],
            uniform_scale=2.0,
            scale_z=True,
        )
        # All three axes scale by 2.
        _approx_list(result, [200, 200, 100])

    def test_conform_through_non_uniform_parent_lands_correct_world(self):
        # Parent scale (200%, 100%) at origin. Child at local (50, 50).
        # World position = parent_scale × child_local = (100, 50).
        # Conform S=2, src=(0,0), tgt=(0,0) → world (200, 100).
        # Back to local (parent inverse: divide by (2, 1)) → (100, 100).
        layers = {
            1: {
                "position": [0, 0, 0],
                "scale": [200, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": -1,
            },
            2: {
                "index": 2,
                "position": [50, 50, 0],
                "scale": [100, 100, 100],
                "rotation_z": 0,
                "anchor": [0, 0, 0],
                "parent_index": 1,
            },
        }
        result = conform_layer_world_space(
            layers[2],
            layers,
            src_center=[0, 0, 0],
            tgt_center=[0, 0, 0],
            uniform_scale=2.0,
        )
        _approx_list(result, [100, 100, 0], tol=1e-7)


# ---------------------------------------------------------------------------
# has_nonuniform_parent_scale
# ---------------------------------------------------------------------------

class TestHasNonuniformParentScale:
    def test_no_parent_returns_false(self):
        layer = {"parent_index": -1}
        assert has_nonuniform_parent_scale(layer, {}) is False

    def test_parent_not_in_dict_returns_false(self):
        layer = {"parent_index": 99}
        assert has_nonuniform_parent_scale(layer, {}) is False

    def test_uniform_parent_returns_false(self):
        layer = {"parent_index": 1}
        layers = {
            1: {"scale": [100, 100, 100], "parent_index": -1},
        }
        assert has_nonuniform_parent_scale(layer, layers) is False

    def test_non_uniform_parent_returns_true(self):
        layer = {"parent_index": 1}
        layers = {
            1: {"scale": [150, 80, 100], "parent_index": -1},
        }
        assert has_nonuniform_parent_scale(layer, layers) is True

    def test_grandparent_non_uniform_returns_true(self):
        layer = {"parent_index": 2}
        layers = {
            1: {"scale": [200, 50, 100], "parent_index": -1},
            2: {"scale": [100, 100, 100], "parent_index": 1},
        }
        assert has_nonuniform_parent_scale(layer, layers) is True

    def test_within_tolerance_returns_false(self):
        # Threshold per the module: difference must exceed 0.1 (a tenth
        # of a percent). 100.05 vs 100.0 is below threshold → False.
        layer = {"parent_index": 1}
        layers = {
            1: {"scale": [100.05, 100.00, 100], "parent_index": -1},
        }
        assert has_nonuniform_parent_scale(layer, layers) is False

    def test_above_tolerance_returns_true(self):
        layer = {"parent_index": 1}
        layers = {
            1: {"scale": [100.2, 100.0, 100], "parent_index": -1},
        }
        assert has_nonuniform_parent_scale(layer, layers) is True

    def test_cycle_in_parent_chain_returns_false_without_infinite_loop(self):
        # Two-node cycle in the parent chain — no non-uniform scale,
        # walker should terminate at the cycle point and return False.
        layer = {"parent_index": 1}
        layers = {
            1: {"scale": [100, 100, 100], "parent_index": 2},
            2: {"scale": [100, 100, 100], "parent_index": 1},
        }
        assert has_nonuniform_parent_scale(layer, layers) is False

    def test_finds_non_uniform_before_cycle(self):
        # The non-uniform scale is on layer 1 (the first ancestor visited).
        # The cycle is 2 → 3 → 2 further up the chain. Walker must surface
        # the True finding before reaching the cycle.
        layer = {"parent_index": 1}
        layers = {
            1: {"scale": [150, 90, 100], "parent_index": 2},
            2: {"scale": [100, 100, 100], "parent_index": 3},
            3: {"scale": [100, 100, 100], "parent_index": 2},
        }
        assert has_nonuniform_parent_scale(layer, layers) is True


# ---------------------------------------------------------------------------
# has_3d_rotated_parent
# ---------------------------------------------------------------------------

class TestHas3DRotatedParent:
    def test_no_parent_returns_false(self):
        layer = {"parent_index": -1}
        assert has_3d_rotated_parent(layer, {}) is False

    def test_parent_not_in_dict_returns_false(self):
        layer = {"parent_index": 99}
        assert has_3d_rotated_parent(layer, {}) is False

    def test_parent_z_rotation_only_returns_false(self):
        # Z rotation alone is a 2D operation; doesn't count as "3D rotated."
        layer = {"parent_index": 1}
        layers = {
            1: {"rotation_z": 45, "parent_index": -1},
        }
        assert has_3d_rotated_parent(layer, layers) is False

    def test_parent_rotation_x_returns_true(self):
        layer = {"parent_index": 1}
        layers = {
            1: {"rotation_x": 30, "parent_index": -1},
        }
        assert has_3d_rotated_parent(layer, layers) is True

    def test_parent_rotation_y_returns_true(self):
        layer = {"parent_index": 1}
        layers = {
            1: {"rotation_y": 30, "parent_index": -1},
        }
        assert has_3d_rotated_parent(layer, layers) is True

    def test_parent_orientation_returns_true(self):
        layer = {"parent_index": 1}
        layers = {
            1: {"orientation": [10, 0, 0], "parent_index": -1},
        }
        assert has_3d_rotated_parent(layer, layers) is True

    def test_orientation_all_zero_returns_false(self):
        layer = {"parent_index": 1}
        layers = {
            1: {"orientation": [0, 0, 0], "parent_index": -1},
        }
        assert has_3d_rotated_parent(layer, layers) is False

    def test_grandparent_rotation_returns_true(self):
        layer = {"parent_index": 2}
        layers = {
            1: {"rotation_x": 45, "parent_index": -1},
            2: {"parent_index": 1},
        }
        assert has_3d_rotated_parent(layer, layers) is True

    def test_cycle_in_parent_chain_returns_false_without_infinite_loop(self):
        # Two-node cycle, no 3D rotations — walker terminates at the
        # cycle point and returns False.
        layer = {"parent_index": 1}
        layers = {
            1: {"parent_index": 2},
            2: {"parent_index": 1},
        }
        assert has_3d_rotated_parent(layer, layers) is False

    def test_finds_3d_rotation_before_cycle(self):
        # Rotation on layer 1 (first ancestor); cycle further up.
        # The True finding must surface before the cycle terminates the walk.
        layer = {"parent_index": 1}
        layers = {
            1: {"rotation_x": 30, "parent_index": 2},
            2: {"parent_index": 3},
            3: {"parent_index": 2},
        }
        assert has_3d_rotated_parent(layer, layers) is True
