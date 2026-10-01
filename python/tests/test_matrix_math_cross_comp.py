# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_matrix_math_cross_comp.py — Slot 12.5 Stage C / M1.

Direct unit tests for the cross-comp + collapse-aware matrix walker
in `core.matrix_math`:

    build_cross_comp_world_matrix
    cross_comp_world_to_local
    conform_layers_with_cross_comp_chain   (Form-2 single entry point)

The walker honors Q2 collapse-switch semantics at each cross-comp
boundary: Collapsed → wrapper transform composes through (Q2C).
Uncollapsed → wrapper transform neutralized to identity; walk halts
at the boundary; inner content's effective world space is the
precomp's own coord system (Q2B).

These tests verify the walker against known-correct expected values
with the same rigor as M0. M0 (`test_matrix_math.py`) stays green
and continues to exercise the intra-comp foundation.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
))

from core.matrix_math import (
    build_cross_comp_world_matrix,
    build_world_matrix,
    conform_layers_with_cross_comp_chain,
    cross_comp_world_to_local,
    mat4_identity,
    mat4_transform_point,
)


TOL = 1e-9


def _approx_list(actual, expected, tol=TOL):
    assert len(actual) == len(expected), (
        f"length mismatch: {len(actual)} vs {len(expected)}"
    )
    for i, (a, e) in enumerate(zip(actual, expected)):
        assert a == pytest.approx(e, abs=tol), (
            f"element {i}: {a} != {e}"
        )


def _layer(uid, position=None, scale=None, rotation_z=0,
           anchor=None, parent_uid=None, wrapper_layer_uid=None,
           collapse=False, containing_comp_id=None):
    """Compact layer-dict factory for the cross-comp tests. Keys
    match what `_layer_local_matrix` reads."""
    return {
        "uid":               uid,
        "position":          position if position is not None else [0, 0, 0],
        "scale":             scale if scale is not None else [100, 100, 100],
        "rotation_z":        rotation_z,
        "anchor":            anchor if anchor is not None else [0, 0, 0],
        "parent_uid":        parent_uid,
        "wrapper_layer_uid": wrapper_layer_uid,
        # Both legacy and v5 surface the collapse bit. Tests use the
        # legacy field; production uses flags.collapse_transformations.
        "collapseTransformations": collapse,
        "containing_comp_id": containing_comp_id,
    }


def _by_uid(*layers):
    return {layer["uid"]: layer for layer in layers}


# ---------------------------------------------------------------------------
# 1. Pure intra-comp chain — must regress against M0's build_world_matrix
# ---------------------------------------------------------------------------

class TestPureIntraCompChain:
    """When there are no cross-comp boundaries (every layer has
    wrapper_layer_uid=None), the cross-comp walker must produce the
    same world matrix as M0's intra-comp build_world_matrix."""

    def test_root_layer_returns_its_local_matrix(self):
        layer = _layer("u1", position=[100, 200, 0])
        layers_by_uid = _by_uid(layer)
        m = build_cross_comp_world_matrix("u1", layers_by_uid)
        # Intra-comp comparison: feed the same layer to M0 via index.
        layers_by_index = {1: dict(layer, index=1, parent_index=-1)}
        m_intra = build_world_matrix(1, layers_by_index)
        _approx_list(m, m_intra)

    def test_two_level_intra_comp_chain_matches_m0(self):
        # Parent at (100, 0), child at (10, 0). World position of child
        # = (110, 0). Cross-comp walker must match build_world_matrix.
        parent = _layer("up", position=[100, 0, 0])
        child = _layer("uc", position=[10, 0, 0], parent_uid="up")
        layers_by_uid = _by_uid(parent, child)
        m_cross = build_cross_comp_world_matrix("uc", layers_by_uid)
        p_world = mat4_transform_point(m_cross, 0, 0, 0)
        _approx_list(p_world, [110, 0, 0])

    def test_scaled_parent_intra_comp_matches_m0(self):
        # Parent scale=200% at origin, child at (50, 50). World = (100, 100).
        parent = _layer("up", scale=[200, 200, 100])
        child = _layer("uc", position=[50, 50, 0], parent_uid="up")
        m = build_cross_comp_world_matrix("uc", _by_uid(parent, child))
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [100, 100, 0])

    def test_unknown_uid_returns_identity(self):
        m = build_cross_comp_world_matrix("ghost", {})
        _approx_list(m, mat4_identity())

    def test_none_uid_returns_identity(self):
        m = build_cross_comp_world_matrix(None, {})
        _approx_list(m, mat4_identity())


# ---------------------------------------------------------------------------
# 2. Single uncollapsed boundary — Q2B (wrapper transform neutralized)
# ---------------------------------------------------------------------------

class TestUncollapsedBoundaryNeutralization:
    """The Q2B path: wrapper transform is neutralized to identity at
    the cross-comp boundary. The leaf's world space is the inner
    comp's coord system — the wrapper's transform contributes nothing
    to the leaf's world matrix."""

    def test_uncollapsed_wrapper_contributes_nothing_to_leaf(self):
        # Wrapper at (1000, 1000) in parent comp, UNCOLLAPSED.
        # Inner leaf at (50, 50) inside the precomp.
        # Q2B: leaf's world is just (50, 50), NOT (1050, 1050).
        wrapper = _layer("u_w", position=[1000, 1000, 0],
                         collapse=False, containing_comp_id=10)
        inner   = _layer("u_i", position=[50, 50, 0],
                         wrapper_layer_uid="u_w",
                         containing_comp_id=20)
        m = build_cross_comp_world_matrix("u_i", _by_uid(wrapper, inner))
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [50, 50, 0])

    def test_uncollapsed_wrapper_scale_does_not_apply(self):
        # If the wrapper's transform DID compose, scaled wrapper would
        # multiply inner positions. Uncollapsed → no such multiplication.
        wrapper = _layer("u_w", scale=[500, 500, 100], collapse=False)
        inner   = _layer("u_i", position=[10, 10, 0],
                         wrapper_layer_uid="u_w")
        m = build_cross_comp_world_matrix("u_i", _by_uid(wrapper, inner))
        p = mat4_transform_point(m, 0, 0, 0)
        # Q2B: inner stays at (10, 10) — wrapper's 500% scale neutralized.
        _approx_list(p, [10, 10, 0])

    def test_uncollapsed_with_intra_comp_chain_inside(self):
        # Inside the inner precomp, the leaf has an intra-comp parent.
        # The walk should compose the intra-comp chain but stop at the
        # uncollapsed boundary.
        wrapper = _layer("u_w", position=[999, 999, 0], collapse=False)
        inner_root = _layer("u_ir", position=[100, 0, 0],
                            wrapper_layer_uid="u_w")
        inner_child = _layer("u_ic", position=[10, 0, 0],
                             parent_uid="u_ir",
                             wrapper_layer_uid="u_w")
        m = build_cross_comp_world_matrix(
            "u_ic", _by_uid(wrapper, inner_root, inner_child)
        )
        p = mat4_transform_point(m, 0, 0, 0)
        # Intra-comp parent contributes 100; wrapper neutralized.
        _approx_list(p, [110, 0, 0])


# ---------------------------------------------------------------------------
# 3. Single collapsed boundary — Q2C (wrapper transform composes through)
# ---------------------------------------------------------------------------

class TestCollapsedBoundaryComposeThrough:
    """The Q2C path: at a collapsed cross-comp boundary, the wrapper's
    local-to-parent matrix composes into the inner content's world
    space. The boundary is transparent."""

    def test_collapsed_wrapper_translation_composes(self):
        # Wrapper at (1000, 1000), COLLAPSED. Inner at (50, 50).
        # Q2C: inner's world position = (1050, 1050).
        wrapper = _layer("c_w", position=[1000, 1000, 0], collapse=True)
        inner   = _layer("c_i", position=[50, 50, 0],
                         wrapper_layer_uid="c_w")
        m = build_cross_comp_world_matrix("c_i", _by_uid(wrapper, inner))
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [1050, 1050, 0])

    def test_collapsed_wrapper_scale_composes(self):
        # Wrapper scale=200%, inner at (10, 10).
        # Q2C: world = parent_scale × inner_local = (20, 20).
        wrapper = _layer("c_w", scale=[200, 200, 100], collapse=True)
        inner   = _layer("c_i", position=[10, 10, 0],
                         wrapper_layer_uid="c_w")
        m = build_cross_comp_world_matrix("c_i", _by_uid(wrapper, inner))
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [20, 20, 0])

    def test_collapsed_wrapper_translate_then_scale(self):
        # Wrapper at (100, 0) with 200% scale. Inner at (10, 10).
        # Q2C: world = T(100, 0) × S(2, 2) × inner = (100, 0) + (20, 20)
        # = (120, 20).
        wrapper = _layer("c_w", position=[100, 0, 0],
                         scale=[200, 200, 100], collapse=True)
        inner   = _layer("c_i", position=[10, 10, 0],
                         wrapper_layer_uid="c_w")
        m = build_cross_comp_world_matrix("c_i", _by_uid(wrapper, inner))
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [120, 20, 0])


# ---------------------------------------------------------------------------
# 4. Mixed-collapse chains — alternating uncollapsed / collapsed
# ---------------------------------------------------------------------------

class TestMixedCollapseChain:
    """Walks across multiple boundaries with mixed collapse states.
    The locked Q2 algorithm: 'walk the chain leaf→root, compose-through
    across collapsed runs, neutralize and reset across uncollapsed
    boundaries.' Once an uncollapsed boundary is crossed, the walk
    HALTS — the leaf's effective world space is sealed in the inner
    precomp's coord system."""

    def test_uncollapsed_halts_walk_even_with_collapsed_above(self):
        # Chain: Final → A(uncollapsed) → B(collapsed) → leaf.
        # The walk goes leaf → B → A. At A (uncollapsed) it halts.
        # B (collapsed) contributes its own transform; A does NOT.
        a = _layer("u_a", position=[10000, 0, 0], collapse=False)
        b = _layer("u_b", position=[100, 0, 0], collapse=True,
                   wrapper_layer_uid="u_a")
        leaf = _layer("u_l", position=[5, 0, 0],
                      wrapper_layer_uid="u_b")
        m = build_cross_comp_world_matrix("u_l", _by_uid(a, b, leaf))
        p = mat4_transform_point(m, 0, 0, 0)
        # Q2C boundary B contributes 100; Q2B boundary A neutralized.
        _approx_list(p, [105, 0, 0])

    def test_collapsed_then_collapsed_composes_all_the_way(self):
        # Two collapsed boundaries in a row → full composition.
        a = _layer("c_a", position=[1000, 0, 0], collapse=True)
        b = _layer("c_b", position=[100, 0, 0], collapse=True,
                   wrapper_layer_uid="c_a")
        leaf = _layer("c_l", position=[10, 0, 0],
                      wrapper_layer_uid="c_b")
        m = build_cross_comp_world_matrix("c_l", _by_uid(a, b, leaf))
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [1110, 0, 0])

    def test_collapsed_above_uncollapsed_does_not_leak(self):
        # Chain: A(collapsed) → B(uncollapsed) → leaf.
        # B uncollapsed → halt. A's collapsed state irrelevant — the
        # walk never reaches it. Leaf's world = leaf-local only.
        a = _layer("u_a", position=[10000, 0, 0], collapse=True)
        b = _layer("u_b", position=[200, 0, 0], collapse=False,
                   wrapper_layer_uid="u_a")
        leaf = _layer("u_l", position=[5, 0, 0],
                      wrapper_layer_uid="u_b")
        m = build_cross_comp_world_matrix("u_l", _by_uid(a, b, leaf))
        p = mat4_transform_point(m, 0, 0, 0)
        _approx_list(p, [5, 0, 0])


# ---------------------------------------------------------------------------
# 5. Collapse-flag source variations (legacy vs v5 LayerFlags)
# ---------------------------------------------------------------------------

class TestCollapseFlagSources:
    """The wrapper's collapse state can come from either the legacy
    top-level `collapseTransformations` field or the v5
    `flags.collapse_transformations` sub-dict. Both must be honored."""

    def test_v5_flags_collapse_transformations_recognized(self):
        wrapper = {
            "uid": "v5_w",
            "position": [100, 0, 0],
            "scale": [100, 100, 100],
            "rotation_z": 0,
            "anchor": [0, 0, 0],
            "parent_uid": None,
            "wrapper_layer_uid": None,
            # No legacy collapseTransformations — only flags.* present.
            "flags": {"collapse_transformations": True},
        }
        leaf = _layer("v5_l", position=[10, 0, 0],
                      wrapper_layer_uid="v5_w")
        m = build_cross_comp_world_matrix("v5_l", _by_uid(wrapper, leaf))
        p = mat4_transform_point(m, 0, 0, 0)
        # Recognized as collapsed → wrapper composes → (110, 0).
        _approx_list(p, [110, 0, 0])

    def test_neither_field_means_uncollapsed(self):
        # A wrapper that's silent on both fields counts as uncollapsed
        # (the conservative default — neutralize). Matches the
        # SovCore_Layer + scrape_manifest default of False.
        wrapper = {
            "uid": "n_w",
            "position": [100, 0, 0],
            "scale": [100, 100, 100],
            "rotation_z": 0,
            "anchor": [0, 0, 0],
            "parent_uid": None,
            "wrapper_layer_uid": None,
        }
        leaf = _layer("n_l", position=[10, 0, 0],
                      wrapper_layer_uid="n_w")
        m = build_cross_comp_world_matrix("n_l", _by_uid(wrapper, leaf))
        p = mat4_transform_point(m, 0, 0, 0)
        # Uncollapsed default → wrapper neutralized → (10, 0).
        _approx_list(p, [10, 0, 0])


# ---------------------------------------------------------------------------
# 6. Cycle guards — never recurse to RecursionError
# ---------------------------------------------------------------------------

class TestCycleGuards:
    def test_intra_comp_cycle_handled(self):
        # Two-node intra-comp cycle. Cycle guard halts walk.
        a = _layer("a", parent_uid="b")
        b = _layer("b", parent_uid="a")
        m = build_cross_comp_world_matrix("a", _by_uid(a, b))
        assert len(m) == 16

    def test_cross_comp_cycle_handled(self):
        # A wraps B; B wraps A via collapsed boundaries. Pathological
        # but the guard halts the walk regardless.
        a = _layer("a", wrapper_layer_uid="b", collapse=True)
        b = _layer("b", wrapper_layer_uid="a", collapse=True)
        m = build_cross_comp_world_matrix("a", _by_uid(a, b))
        assert len(m) == 16

    def test_self_referential_layer_handled(self):
        a = _layer("a", parent_uid="a")
        m = build_cross_comp_world_matrix("a", _by_uid(a))
        assert len(m) == 16


# ---------------------------------------------------------------------------
# 7. cross_comp_world_to_local — round-trip inverse
# ---------------------------------------------------------------------------

class TestCrossCompWorldToLocal:
    def test_root_layer_world_equals_local(self):
        a = _layer("a", position=[100, 200, 0])
        local = cross_comp_world_to_local([300, 400, 0], "a", _by_uid(a))
        _approx_list(local, [300, 400, 0])

    def test_intra_comp_parent_inverts_correctly(self):
        # Parent at (50, 50); leaf with no own translation, so its
        # world position is also (50, 50). Inverse: world (100, 100)
        # should map to local (50, 50).
        parent = _layer("p", position=[50, 50, 0])
        leaf = _layer("l", parent_uid="p")
        local = cross_comp_world_to_local(
            [100, 100, 0], "l", _by_uid(parent, leaf)
        )
        _approx_list(local, [50, 50, 0])

    def test_collapsed_wrapper_uses_wrapper_as_parent(self):
        # Wrapper at (1000, 0) collapsed. Inner with no parent_uid
        # but wrapper above. World (1050, 0) → inner local (50, 0).
        wrapper = _layer("w", position=[1000, 0, 0], collapse=True)
        inner = _layer("i", wrapper_layer_uid="w")
        local = cross_comp_world_to_local(
            [1050, 0, 0], "i", _by_uid(wrapper, inner)
        )
        _approx_list(local, [50, 0, 0])

    def test_uncollapsed_wrapper_uses_identity_as_parent(self):
        # Uncollapsed wrapper → leaf's parent space is identity, so
        # world == local. World (50, 0) → local (50, 0) regardless
        # of wrapper position.
        wrapper = _layer("w", position=[9999, 0, 0], collapse=False)
        inner = _layer("i", wrapper_layer_uid="w")
        local = cross_comp_world_to_local(
            [50, 0, 0], "i", _by_uid(wrapper, inner)
        )
        _approx_list(local, [50, 0, 0])


# ---------------------------------------------------------------------------
# 8. conform_layers_with_cross_comp_chain — Form-2 entry point
# ---------------------------------------------------------------------------

class TestConformLayersWithCrossCompChain:
    """Form-2 single-entry-point batch conform. Verifies behavior on
    representative chains so any future numpy-vectorized replacement
    can be cross-checked against these pinned cases."""

    def test_root_layer_uniform_scale_2x(self):
        layer = _layer("l", position=[100, 100, 0])
        layers_by_uid = _by_uid(layer)
        out = conform_layers_with_cross_comp_chain(
            [layer], layers_by_uid,
            src_center=[0, 0, 0],
            tgt_center=[0, 0, 0],
            uniform_scale=2.0,
        )
        _approx_list(out["l"], [200, 200, 0])

    def test_identity_conform_passes_through(self):
        layer = _layer("l", position=[300, 400, 0])
        out = conform_layers_with_cross_comp_chain(
            [layer], _by_uid(layer),
            src_center=[100, 100, 0],
            tgt_center=[100, 100, 0],
            uniform_scale=1.0,
        )
        _approx_list(out["l"], [300, 400, 0])

    def test_collapsed_wrapper_inner_remaps_in_wrapper_space(self):
        # Wrapper at (1000, 1000) collapsed. Inner at (50, 50) → world
        # (1050, 1050). With S=2, src_center=(0,0), tgt_center=(0,0)
        # → new world (2100, 2100). Inverse via wrapper world matrix
        # → inner local = (1100, 1100). (Wrapper inverts the +1000
        # translation, leaving 2100 - 1000 = 1100 in each axis.)
        wrapper = _layer("w", position=[1000, 1000, 0], collapse=True)
        inner   = _layer("i", position=[50, 50, 0],
                         wrapper_layer_uid="w")
        out = conform_layers_with_cross_comp_chain(
            [inner], _by_uid(wrapper, inner),
            src_center=[0, 0, 0],
            tgt_center=[0, 0, 0],
            uniform_scale=2.0,
        )
        _approx_list(out["i"], [1100, 1100, 0])

    def test_uncollapsed_wrapper_inner_remaps_in_own_space(self):
        # Wrapper transform neutralized → inner conforms in the inner
        # comp's own space. Inner at (50, 50), wrapper irrelevant.
        # World = (50, 50). S=2, src=(0,0), tgt=(0,0) → new world (100, 100).
        # Parent space inverse = identity (Q2B) → local = (100, 100).
        wrapper = _layer("w", position=[9999, 9999, 0], collapse=False)
        inner = _layer("i", position=[50, 50, 0],
                       wrapper_layer_uid="w")
        out = conform_layers_with_cross_comp_chain(
            [inner], _by_uid(wrapper, inner),
            src_center=[0, 0, 0],
            tgt_center=[0, 0, 0],
            uniform_scale=2.0,
        )
        _approx_list(out["i"], [100, 100, 0])

    def test_layer_without_uid_silently_skipped(self):
        layer_with_uid = _layer("l", position=[10, 0, 0])
        no_uid_layer = dict(layer_with_uid)
        no_uid_layer["uid"] = None
        out = conform_layers_with_cross_comp_chain(
            [layer_with_uid, no_uid_layer], _by_uid(layer_with_uid),
            src_center=[0, 0, 0],
            tgt_center=[0, 0, 0],
            uniform_scale=1.0,
        )
        # Only the uid-bearing layer surfaces.
        assert set(out.keys()) == {"l"}

    def test_scale_z_default_passes_z_through(self):
        layer = _layer("l", position=[10, 0, 50])
        out = conform_layers_with_cross_comp_chain(
            [layer], _by_uid(layer),
            src_center=[0, 0, 0],
            tgt_center=[0, 0, 0],
            uniform_scale=2.0,
        )
        # X scaled, Z unchanged.
        _approx_list(out["l"], [20, 0, 50])

    def test_scale_z_true_scales_z_by_s(self):
        layer = _layer("l", position=[10, 0, 50])
        out = conform_layers_with_cross_comp_chain(
            [layer], _by_uid(layer),
            src_center=[0, 0, 0],
            tgt_center=[0, 0, 0],
            uniform_scale=2.0,
            scale_z=True,
        )
        _approx_list(out["l"], [20, 0, 100])

    def test_returns_dict_keyed_by_uid(self):
        a = _layer("ua", position=[10, 0, 0])
        b = _layer("ub", position=[20, 0, 0])
        out = conform_layers_with_cross_comp_chain(
            [a, b], _by_uid(a, b),
            src_center=[0, 0, 0],
            tgt_center=[0, 0, 0],
            uniform_scale=1.0,
        )
        assert isinstance(out, dict)
        assert set(out.keys()) == {"ua", "ub"}


# ---------------------------------------------------------------------------
# 9. Corpus_01 chain — AE-auto-compensated non-uniform parent across a
#    collapsed cross-comp boundary (Stage C closure test)
# ---------------------------------------------------------------------------

class TestCorpus01NonUniformCompose:
    """Slot 12.5 Stage C corpus-smoke (2026-05-17) — exact Corpus_01
    Root_Omnibus chain. Pins M1's behavior against the realistic AE
    pattern Corpus_01 surfaced: a precomp wrapper parented to a non-
    uniformly scaled, corner-anchored null. AE's auto-parent-compensation
    stamps an inverse scale on the child wrapper so visual position is
    preserved source-side; M1's compose-through must produce identity
    world transform on that compensated chain.

    Source values are pulled verbatim from scrape_manifest.json for
    Corpus_01 (AE 26.2.1x2 scrape, 2026-05-17):

      NonUniform_Parent_Null   pos=[960,540]  scale=[150,90]    anchor=[0,0]
      Precomp_A_Wrapper        pos=[0,~0]     scale=[66.67,111.11]
                               anchor=[960,540]  collapse=True
      A_FX_Solid (in Precomp_A)
                               pos=[960,540]  scale=[100,100]   anchor=[960,540]

    The wrapper's compose-through (Q2C) plus the parent null's non-
    uniform scale combine to identity world (visual preservation by AE).
    M1 must reflect this exactly — any non-identity residual would
    leak into the inner content's world position when Stage D wires
    M1 into the wrapper-transform-recompute path.

    Test gap this closes: pre-2026-05-17 M1 tests covered cross-comp
    composition with UNIFORM parent scales only; the AE-compensated
    non-uniform pattern is the realistic case M1 was built for.
    """

    # AE's parent-compensation rounding artifact — the wrapper's Y position
    # comes back as -1.13e-13 instead of exactly 0. Carried through the
    # math; visible at the test tolerance.
    _AE_FLOAT_NOISE = -1.13686837721616e-13

    def _build_chain(self, *, wrapper_collapse=True):
        """Build the exact Corpus_01 Root_Omnibus chain as
        layer dicts keyed by uid. `wrapper_collapse` lets the
        same fixture exercise both Q2C (default, matches the corpus)
        and Q2B paths."""
        null_layer = {
            "uid": "uid_null",
            "position": [960, 540, 0],
            "scale":    [150, 90, 100],
            "anchor":   [0, 0, 0],
            "rotation_z": 0,
            "parent_uid": None,
            "wrapper_layer_uid": None,
            "collapseTransformations": False,
        }
        wrapper_layer = {
            "uid": "uid_wrapper",
            "position": [0, self._AE_FLOAT_NOISE, 0],
            "scale":    [66.6666666666667, 111.111111111111, 100],
            "anchor":   [960, 540, 0],
            "rotation_z": 0,
            "parent_uid": "uid_null",
            "wrapper_layer_uid": None,
            "collapseTransformations": wrapper_collapse,
        }
        inner_layer = {
            "uid": "uid_inner",
            "position": [960, 540, 0],
            "scale":    [100, 100, 100],
            "anchor":   [960, 540, 0],
            "rotation_z": 0,
            "parent_uid": None,
            "wrapper_layer_uid": "uid_wrapper",
            "collapseTransformations": False,
        }
        return _by_uid(null_layer, wrapper_layer, inner_layer)

    def test_wrapper_world_is_identity(self):
        """The wrapper's compose-through with its non-uniform-scaled
        corner-anchored parent null must produce identity world —
        that's AE's auto-parent-compensation working as intended,
        reflected by M1's matrix math."""
        layers_by_uid = self._build_chain()
        m = build_cross_comp_world_matrix("uid_wrapper", layers_by_uid)
        # Identity to floating-point tolerance.
        for row in range(4):
            for col in range(4):
                expected = 1.0 if row == col else 0.0
                actual = m[row * 4 + col]
                assert actual == pytest.approx(expected, abs=1e-9), (
                    f"matrix[{row}][{col}] = {actual}, expected {expected}"
                )

    def test_wrapper_bounds_map_identically(self):
        """A 1920×1080 wrapper bounds-corner walk: (0,0), (1920,1080),
        and the anchor (960,540) all map to themselves under the
        compensated identity world."""
        layers_by_uid = self._build_chain()
        m = build_cross_comp_world_matrix("uid_wrapper", layers_by_uid)
        for local in ([0, 0, 0], [960, 540, 0], [1920, 1080, 0]):
            world = mat4_transform_point(m, *local)
            _approx_list(world, local, tol=1e-6)

    def test_collapsed_inner_inherits_identity_world(self):
        """A full-bleed centered inner (matching A_FX_Solid) under the
        compensated collapsed wrapper inherits the wrapper's identity
        world. Inner-local origin maps to world origin; inner anchor
        (960,540) maps to world (960,540) — i.e. the inner content
        renders source-side at its source-side coords."""
        layers_by_uid = self._build_chain()
        m = build_cross_comp_world_matrix("uid_inner", layers_by_uid)
        _approx_list(mat4_transform_point(m, 0, 0, 0), [0, 0, 0], tol=1e-6)
        _approx_list(
            mat4_transform_point(m, 960, 540, 0), [960, 540, 0], tol=1e-6
        )

    def test_uncollapsed_inner_seals_in_inner_space(self):
        """If the wrapper were flipped UNCOLLAPSED (Q2B contrast),
        the inner's world space seals in the precomp's own coord
        system. The non-uniform null contribution doesn't leak across
        the boundary — the inner's world is just its local."""
        layers_by_uid = self._build_chain(wrapper_collapse=False)
        m = build_cross_comp_world_matrix("uid_inner", layers_by_uid)
        # Full-bleed centered: anchor at (960,540) maps to position (960,540).
        _approx_list(
            mat4_transform_point(m, 960, 540, 0), [960, 540, 0], tol=1e-6
        )

    def test_q2c_vs_q2b_delta_is_zero_on_compensated_chain(self):
        """The Q2C minus Q2B delta is the contribution the collapsed
        boundary composes through that the uncollapsed boundary
        suppresses. On the AE-compensated chain the wrapper's world is
        identity, so Q2C and Q2B should produce equal inner world
        positions — the compensation already netted the parent out.

        This is the Corpus_01 invariant: AE's auto-parent-compensation
        produces visual identity regardless of whether the wrapper is
        collapsed. M1's math must mirror that."""
        q2c = build_cross_comp_world_matrix(
            "uid_inner", self._build_chain(wrapper_collapse=True)
        )
        q2b = build_cross_comp_world_matrix(
            "uid_inner", self._build_chain(wrapper_collapse=False)
        )
        for col in (3, 7, 11):  # translation column components
            assert q2c[col] == pytest.approx(q2b[col], abs=1e-6), (
                f"Q2C vs Q2B translation[{col}] diverged on compensated chain "
                f"({q2c[col]} vs {q2b[col]})"
            )

    def test_form_2_entry_point_reads_anchor_world_coord(self):
        """Pins the resolved Form-2 contract (Stage D Item 4,
        2026-05-17): `conform_layers_with_cross_comp_chain` reads the
        anchor's world coordinate, not the translation column.

        AE's `layer.position` is the anchor's location in parent
        space. The Form-2 entry point now reads
        `mat4_transform_point(world_mat, anchor)` — the world coord
        of the anchor — and conforms that, then inverts the parent's
        world matrix to recover the anchor's new parent-local
        coord. The output IS the value Babysitter writes to
        `layer.position` directly.

        Pre-Item-4, the entry point read the translation column
        (`m[3]/m[7]/m[11]`) which is the world coord of the LOCAL
        ORIGIN (bounds corner). For a layer with anchor == position
        the translation column was (0,0,0), NOT the layer's logical
        position. The contract concern was pinned in this test under
        a different name pre-resolution; Item 4 chose option (a)
        from that pinning and renamed the test to reflect the
        resolution.

        Inner-layer values (Corpus_01 chain):
          source pos = [960, 540, 0]
          source anchor = [960, 540, 0] (full-bleed centered)
          world matrix = identity (AE auto-compensated wrapper chain)
          anchor world = mat4_transform_point(identity, 960, 540, 0)
                       = (960, 540, 0)
          conform identity (S=1, src==tgt) → (960, 540, 0)
          parent inverse = identity → final position (960, 540, 0)

        Null-layer values (root in active comp):
          source pos = [960, 540, 0]
          source anchor = [0, 0, 0] (corner — AE's addNull default)
          world matrix = T(960, 540) * S(1.5, 0.9)
          anchor world = mat4_transform_point(world_mat, 0, 0, 0)
                       = (960, 540, 0)
          identity conform → (960, 540, 0)
          parent inverse = identity → final position (960, 540, 0)

        Both layers now produce their AE-semantic position
        directly — the asymmetry between corner-anchored and
        center-anchored layers is RESOLVED at the Form-2 boundary.
        """
        layers_by_uid = self._build_chain()
        layers = [
            layers_by_uid["uid_null"],
            layers_by_uid["uid_wrapper"],
            layers_by_uid["uid_inner"],
        ]
        out = conform_layers_with_cross_comp_chain(
            layers, layers_by_uid,
            src_center=[960, 540, 0],
            tgt_center=[960, 540, 0],
            uniform_scale=1.0,
        )
        # Anchor-aware read: both layers report their AE-semantic
        # position. Inner's anchor==position cancellation no longer
        # masks the layer's logical location.
        _approx_list(out["uid_inner"], [960, 540, 0], tol=1e-6)
        _approx_list(out["uid_null"], [960, 540, 0], tol=1e-6)
