# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/matrix_math.py
Dimension Engine v4.4 — World-Space Matrix Decomposition (Full 3D)

Handles layers whose parents have non-uniform scale or 3D rotations.
In those cases, simple uniform-scale math applied in local space produces
visible drift — the child's world-space position is distorted by the parent's
transform chain.

The correct approach:
  1. Compose the full parent-chain transform matrix (world matrix)
  2. Apply the conform scale to that world-space position
  3. Decompose back into local-space values that AE can accept

v4.4 upgrade: mat3 (2D affine) → mat4 (full 3D affine).
AE's transform order per layer: anchor → scale → orientation → rx → ry → rz → position.
All matrices are 4×4 row-major, stored as flat 16-element lists:
  [ m00 m01 m02 m03 ]
  [ m10 m11 m12 m13 ]
  [ m20 m21 m22 m23 ]
  [  0   0   0   1  ]

This module is used by ScaleEngine for layers where collapseTransformations=True,
where parent_index points to a layer with non-uniform scale, or for any 3D layer
with rotated parents.
"""

import math
from typing import Dict, List, Optional, Union

from core.logger import log


class SingularMatrixError(ValueError):
    """Raised when a matrix is singular and cannot be inverted."""
    pass



# --- 4×4 Matrix primitives ---

def mat4_identity() -> List[float]:
    return [
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    ]


def mat4_mul(a: List[float], b: List[float]) -> List[float]:
    """Multiply two 4×4 row-major matrices."""
    r = [0.0] * 16
    for row in range(4):
        for col in range(4):
            s = 0.0
            for k in range(4):
                s += a[row * 4 + k] * b[k * 4 + col]
            r[row * 4 + col] = s
    return r


def mat4_translate(tx: float, ty: float, tz: float) -> List[float]:
    return [
        1, 0, 0, tx,
        0, 1, 0, ty,
        0, 0, 1, tz,
        0, 0, 0, 1,
    ]


def mat4_scale(sx: float, sy: float, sz: float) -> List[float]:
    return [
        sx, 0, 0, 0,
        0, sy, 0, 0,
        0, 0, sz, 0,
        0, 0, 0, 1,
    ]


def mat4_rot_x(deg: float) -> List[float]:
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return [
        1, 0, 0, 0,
        0, c, -s, 0,
        0, s, c, 0,
        0, 0, 0, 1,
    ]


def mat4_rot_y(deg: float) -> List[float]:
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return [
        c, 0, s, 0,
        0, 1, 0, 0,
        -s, 0, c, 0,
        0, 0, 0, 1,
    ]


def mat4_rot_z(deg: float) -> List[float]:
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return [
        c, -s, 0, 0,
        s, c, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    ]


def mat4_inv(m: List[float]) -> List[float]:
    """Invert a 4×4 matrix via cofactor expansion. Raises SingularMatrixError if singular."""
    # Unpack for readability
    m00, m01, m02, m03 = m[0], m[1], m[2], m[3]
    m10, m11, m12, m13 = m[4], m[5], m[6], m[7]
    m20, m21, m22, m23 = m[8], m[9], m[10], m[11]
    m30, m31, m32, m33 = m[12], m[13], m[14], m[15]

    # 2×2 subdeterminants
    s0 = m00 * m11 - m10 * m01
    s1 = m00 * m12 - m10 * m02
    s2 = m00 * m13 - m10 * m03
    s3 = m01 * m12 - m11 * m02
    s4 = m01 * m13 - m11 * m03
    s5 = m02 * m13 - m12 * m03

    c5 = m22 * m33 - m32 * m23
    c4 = m21 * m33 - m31 * m23
    c3 = m21 * m32 - m31 * m22
    c2 = m20 * m33 - m30 * m23
    c1 = m20 * m32 - m30 * m22
    c0 = m20 * m31 - m30 * m21

    det = s0 * c5 - s1 * c4 + s2 * c3 + s3 * c2 - s4 * c1 + s5 * c0
    if abs(det) < 1e-12:
        raise SingularMatrixError("Cannot invert singular matrix (determinant is zero or near-zero)")
    inv_det = 1.0 / det

    return [
        ( m11 * c5 - m12 * c4 + m13 * c3) * inv_det,
        (-m01 * c5 + m02 * c4 - m03 * c3) * inv_det,
        ( m31 * s5 - m32 * s4 + m33 * s3) * inv_det,
        (-m21 * s5 + m22 * s4 - m23 * s3) * inv_det,

        (-m10 * c5 + m12 * c2 - m13 * c1) * inv_det,
        ( m00 * c5 - m02 * c2 + m03 * c1) * inv_det,
        (-m30 * s5 + m32 * s2 - m33 * s1) * inv_det,
        ( m20 * s5 - m22 * s2 + m23 * s1) * inv_det,

        ( m10 * c4 - m11 * c2 + m13 * c0) * inv_det,
        (-m00 * c4 + m01 * c2 - m03 * c0) * inv_det,
        ( m30 * s4 - m31 * s2 + m33 * s0) * inv_det,
        (-m20 * s4 + m21 * s2 - m23 * s0) * inv_det,

        (-m10 * c3 + m11 * c1 - m12 * c0) * inv_det,
        ( m00 * c3 - m01 * c1 + m02 * c0) * inv_det,
        (-m30 * s3 + m31 * s1 - m32 * s0) * inv_det,
        ( m20 * s3 - m21 * s1 + m22 * s0) * inv_det,
    ]


def mat4_transform_point(m: List[float], x: float, y: float, z: float) -> List[float]:
    """Apply a 4×4 matrix to a 3D point (assumes w=1)."""
    xo = m[0] * x + m[1] * y + m[2] * z + m[3]
    yo = m[4] * x + m[5] * y + m[6] * z + m[7]
    zo = m[8] * x + m[9] * y + m[10] * z + m[11]
    return [xo, yo, zo]


# --- AE transform → matrix ---

def layer_to_matrix(
    position: List[float],
    anchor: List[float],
    scale_pct: List[float],
    rotation_z: float,
    rotation_x: float = 0.0,
    rotation_y: float = 0.0,
    orientation: Optional[List[float]] = None,
) -> List[float]:
    """
    Build the local→parent 4×4 transform matrix for one AE layer.

    AE applies transforms in this order (innermost first):
      1. Translate by -anchor  (move pivot to origin)
      2. Scale
      3. Orientation (if 3D)
      4. X Rotation (if 3D)
      5. Y Rotation (if 3D)
      6. Z Rotation
      7. Translate by position

    Result: T(pos) * Rz * Ry * Rx * R(orientation) * S * T(-anchor)
    """
    ax = anchor[0] if len(anchor) > 0 else 0.0
    ay = anchor[1] if len(anchor) > 1 else 0.0
    az = anchor[2] if len(anchor) > 2 else 0.0
    px = position[0] if len(position) > 0 else 0.0
    py = position[1] if len(position) > 1 else 0.0
    pz = position[2] if len(position) > 2 else 0.0
    sx = (scale_pct[0] if len(scale_pct) > 0 else 100.0) / 100.0
    sy = (scale_pct[1] if len(scale_pct) > 1 else 100.0) / 100.0
    sz = (scale_pct[2] if len(scale_pct) > 2 else 100.0) / 100.0

    # Start from right: T(-anchor)
    m = mat4_translate(-ax, -ay, -az)
    # Scale
    m = mat4_mul(mat4_scale(sx, sy, sz), m)
    # Orientation (3D only — Euler XYZ applied as a single rotation)
    if orientation and any(v != 0 for v in orientation):
        ox = orientation[0] if len(orientation) > 0 else 0.0
        oy = orientation[1] if len(orientation) > 1 else 0.0
        oz = orientation[2] if len(orientation) > 2 else 0.0
        m = mat4_mul(mat4_rot_z(oz), m)
        m = mat4_mul(mat4_rot_y(oy), m)
        m = mat4_mul(mat4_rot_x(ox), m)
    # Individual axis rotations (3D only for X/Y)
    if rotation_x != 0:
        m = mat4_mul(mat4_rot_x(rotation_x), m)
    if rotation_y != 0:
        m = mat4_mul(mat4_rot_y(rotation_y), m)
    # Z rotation (always)
    if rotation_z != 0:
        m = mat4_mul(mat4_rot_z(rotation_z), m)
    # Translate by position
    m = mat4_mul(mat4_translate(px, py, pz), m)
    return m


# --- World-space decomposition ---

def build_world_matrix(layer_index: Union[int, tuple], layers_by_index: dict) -> List[float]:
    """
    Compose the full world-space 4×4 matrix for a layer by traversing its
    parent chain.

    layers_by_index: dict mapping AE layer index (or tuple) → layer data dict
      Each dict must have: position, scale, rotation_z, anchor, parent_index
      Optional 3D fields: rotation_x, rotation_y, orientation

    Cycle handling: if a parent-chain cycle is detected (corrupt manifest
    data — should not happen in real scrapes), the walk halts at the
    cycle point and returns the partial composition computed up to that
    point. A warning is logged with the involved indices. The walker
    never recurses to RecursionError.
    """
    return _build_world_matrix_walk(layer_index, layers_by_index, set())


def _build_world_matrix_walk(
    layer_index: Union[int, tuple],
    layers_by_index: dict,
    visited: set,
) -> List[float]:
    """Internal recursion with explicit cycle-visited set. Top-level
    callers go through `build_world_matrix`, which seeds the empty set."""
    if layer_index in visited:
        log.warning(
            "Parent chain cycle detected in build_world_matrix",
            extra={
                "cycle_index": layer_index,
                "visited_chain": sorted(visited),
            },
        )
        return mat4_identity()
    visited.add(layer_index)

    # Detect key format
    is_tuple_keys = False
    if layers_by_index:
        first_key = next(iter(layers_by_index))
        is_tuple_keys = isinstance(first_key, tuple)

    if is_tuple_keys:
        if isinstance(layer_index, tuple):
            lookup_key = layer_index
        else:
            lookup_key = (None, layer_index)
    else:
        if isinstance(layer_index, tuple):
            lookup_key = layer_index[1]
        else:
            lookup_key = layer_index

    layer = layers_by_index.get(lookup_key)
    if layer is None:
        return mat4_identity()

    p = layer.get("position") or [0.0, 0.0, 0.0]
    s = layer.get("scale") or [100.0, 100.0, 100.0]
    rz = layer.get("rotation_z") or 0.0
    rx = layer.get("rotation_x") or 0.0
    ry = layer.get("rotation_y") or 0.0
    a = layer.get("anchor") or [0.0, 0.0, 0.0]
    ori = layer.get("orientation")

    local_mat = layer_to_matrix(p, a, s, rz, rx, ry, ori)

    parent_index = layer.get("parent_index", -1)
    if parent_index == -1:
        return local_mat

    comp_id = layer.get("containing_comp_id")
    parent_key = (comp_id, parent_index) if is_tuple_keys else parent_index

    if parent_key not in layers_by_index:
        return local_mat

    parent_mat = _build_world_matrix_walk(parent_key, layers_by_index, visited)
    return mat4_mul(parent_mat, local_mat)


def world_to_local_position(
    world_pos: List[float],
    parent_index: Union[int, tuple],
    layers_by_index: dict,
) -> List[float]:
    """
    Convert a world-space position into the local space of the given parent.
    If parent_index == -1, world space IS local space (root layer).
    """
    is_tuple_keys = False
    if layers_by_index:
        first_key = next(iter(layers_by_index))
        is_tuple_keys = isinstance(first_key, tuple)

    if is_tuple_keys:
        if isinstance(parent_index, tuple):
            parent_key = parent_index
            actual_parent_idx = parent_index[1]
        else:
            parent_key = (None, parent_index)
            actual_parent_idx = parent_index
    else:
        if isinstance(parent_index, tuple):
            parent_key = parent_index[1]
            actual_parent_idx = parent_index[1]
        else:
            parent_key = parent_index
            actual_parent_idx = parent_index

    if actual_parent_idx == -1 or parent_key not in layers_by_index:
        return list(world_pos)

    parent_world = build_world_matrix(parent_key, layers_by_index)
    inv = mat4_inv(parent_world)

    wx = world_pos[0] if len(world_pos) > 0 else 0.0
    wy = world_pos[1] if len(world_pos) > 1 else 0.0
    wz = world_pos[2] if len(world_pos) > 2 else 0.0
    return mat4_transform_point(inv, wx, wy, wz)


def conform_layer_world_space(
    layer: dict,
    layers_by_index: dict,
    src_center: List[float],
    tgt_center: List[float],
    uniform_scale: float,
    scale_z: bool = False,
) -> List[float]:
    """
    Conform a layer's position via world-space decomposition.

    Steps:
      1. Get world-space position (compose parent chain)
      2. Apply conform scale in world space
         - XY: center-remap always
         - Z: multiply by S only in 3D camera scenes (scale_z=True)
      3. Convert back to local space (invert parent chain)

    Returns the conformed position in local space [x, y, z], ready to write to AE.
    """
    is_tuple_keys = False
    if layers_by_index:
        first_key = next(iter(layers_by_index))
        is_tuple_keys = isinstance(first_key, tuple)

    comp_id = layer.get("containing_comp_id")
    lookup_key = (comp_id, layer["index"]) if is_tuple_keys else layer["index"]

    world_mat = build_world_matrix(lookup_key, layers_by_index)
    # The translation column of the world matrix IS the world position
    world_x = world_mat[3]   # mat4 row-major: tx is at [0][3] = index 3
    world_y = world_mat[7]   # ty is at [1][3] = index 7
    world_z = world_mat[11]  # tz is at [2][3] = index 11

    # Apply conform: center-remap in world space
    new_wx = ((world_x - src_center[0]) * uniform_scale) + tgt_center[0]
    new_wy = ((world_y - src_center[1]) * uniform_scale) + tgt_center[1]
    # Z: scale by S in 3D camera scenes to preserve depth ratios.
    # In 2D comps, Z passes through unchanged.
    new_wz = world_z * uniform_scale if scale_z else world_z

    # Convert back to local space
    parent_index = layer.get("parent_index", -1)
    parent_lookup_key = (comp_id, parent_index) if is_tuple_keys else parent_index
    local_pos = world_to_local_position([new_wx, new_wy, new_wz], parent_lookup_key, layers_by_index)

    return [local_pos[0], local_pos[1], local_pos[2]]


def has_nonuniform_parent_scale(layer: dict, layers_by_index: dict) -> bool:
    """
    Return True if any ancestor has mismatched X/Y scale (> 0.1% difference).
    These layers need world-space decomposition instead of simple center-remap.

    Cycle handling: tracks visited indices and halts if the chain loops
    back on itself. Returns the predicate's value as evaluated up to the
    cycle point (no True findings beyond it).
    """
    visited = set()
    parent_index = layer.get("parent_index", -1)
    comp_id = layer.get("containing_comp_id")

    is_tuple_keys = False
    if layers_by_index:
        first_key = next(iter(layers_by_index))
        is_tuple_keys = isinstance(first_key, tuple)

    while parent_index != -1:
        parent_key = (comp_id, parent_index) if is_tuple_keys else parent_index
        if parent_key in visited:
            log.warning(
                "Parent chain cycle detected in has_nonuniform_parent_scale",
                extra={"cycle_index": parent_key, "visited_chain": sorted(visited)},
            )
            return False
        visited.add(parent_key)
        parent = layers_by_index.get(parent_key)
        if parent is None:
            break
        s = parent.get("scale") or [100.0, 100.0, 100.0]
        if abs(s[0] - s[1]) > 0.1:
            return True
        parent_index = parent.get("parent_index", -1)
    return False



# ── Slot 12.5 Stage C M1 — cross-comp + collapse-aware matrix walk ────
#
# Pre-Stage-C `build_world_matrix` walks intra-comp parent chains only
# (`layer.parent_index` within one comp's `layers_by_index`). Slot 12.5
# introduces nested precomp scraping; a leaf's "world space" can cross
# comp boundaries, and at each boundary the wrapper layer's
# `collapseTransformations` flag determines whether the wrapper
# composes into the inner content (Q2C — collapsed) or whether the
# inner content's transforms are sealed within their own coord space
# (Q2B — uncollapsed, wrapper neutralized).
#
# Both math paths use the same mat4 primitives M0 verified. The
# cross-comp walker is an extension on top of that tested foundation,
# NOT a replacement.
#
# Form-2 vectorization seam (scope-doc Q6 design constraint):
# `conform_layers_with_cross_comp_chain` is the single entry point
# for batch conform via the cross-comp chain. Callers do not reach
# into matrix internals — a numpy vectorized impl can swap in later
# without touching callers. No numpy code in Slot 12.5.


def _is_layer_collapsed(layer: dict) -> bool:
    """Read the collapse-transformations bit from either the legacy
    flat field or the v5 LayerFlags sub-dict. Both surface the same AE
    property (`AVLayer.collapseTransformation`); we accept either to
    keep the walker tolerant of legacy 5.0 manifests and v5 records."""
    if layer.get("collapseTransformations"):
        return True
    flags = layer.get("flags") or {}
    if isinstance(flags, dict) and flags.get("collapse_transformations"):
        return True
    return False


def _layer_local_matrix(layer: dict) -> List[float]:
    """Layer-local 4×4 from a layer dict. Same field reading as
    `build_world_matrix` uses; lifted into a helper so the cross-comp
    walker reuses the field-default logic exactly."""
    p = layer.get("position") or [0.0, 0.0, 0.0]
    s = layer.get("scale") or [100.0, 100.0, 100.0]
    rz = layer.get("rotation_z") or 0.0
    rx = layer.get("rotation_x") or 0.0
    ry = layer.get("rotation_y") or 0.0
    a = layer.get("anchor") or [0.0, 0.0, 0.0]
    ori = layer.get("orientation")
    return layer_to_matrix(p, a, s, rz, rx, ry, ori)


def build_cross_comp_world_matrix(
    layer_uid: Optional[str],
    layers_by_uid: dict,
) -> List[float]:
    """Compose a leaf layer's world-space 4×4 matrix by walking the
    cross-comp parent chain leaf → root, applying Q2 collapse-switch
    semantics at every cross-comp boundary.

    Two interleaved hierarchies:
      - Intra-comp:   `layer.parent_uid` → walk up to the comp's root
                      layer (parent_uid is None for that layer).
      - Cross-comp:   `layer.wrapper_layer_uid` → the precomp wrapper
                      layer in the parent comp (populated by Stage B's
                      recursive scrape walker; None for layers at the
                      active comp's top level).

    At each cross-comp boundary, the wrapper layer's collapse flag
    determines the composition:
      - Collapsed (Q2C) → wrapper's local-to-parent matrix composes in;
        the walk continues into the wrapper's intra-comp parent chain.
      - Uncollapsed (Q2B) → wrapper's transform is NEUTRALIZED (treated
        as identity). The walk stops at this boundary; the leaf's
        effective world space is the inner comp's coord system. The
        2D-flatten AE renders at this boundary means inner transforms
        are sealed; the wrapper's role in conform is handled separately
        as an ordinary layer in the parent comp.

    Cycle-safe via a visited-uid set; never recurses to RecursionError.

    Returns a 4×4 row-major matrix (16-element list).

    layers_by_uid: dict mapping layer.uid (str) → layer dict.
    Each layer dict carries at minimum: uid, position, scale,
    rotation_z, anchor, parent_uid. Optional cross-comp fields
    (populated by Stage B): wrapper_layer_uid. Optional collapse
    fields (either accepted): top-level `collapseTransformations`
    (legacy 5.0) OR `flags.collapse_transformations` (v5)."""
    return _cross_comp_walk(layer_uid, layers_by_uid, set())


def _cross_comp_walk(
    layer_uid: Optional[str],
    layers_by_uid: dict,
    visited: set,
) -> List[float]:
    """Internal cross-comp walker with explicit visited-uid set.
    Top-level callers go through `build_cross_comp_world_matrix`."""
    if layer_uid is None:
        return mat4_identity()
    if layer_uid in visited:
        log.warning(
            "Parent chain cycle detected in build_cross_comp_world_matrix",
            extra={
                "cycle_uid": layer_uid,
                "visited_chain": sorted(visited),
            },
        )
        return mat4_identity()
    visited.add(layer_uid)

    layer = layers_by_uid.get(layer_uid)
    if layer is None:
        return mat4_identity()

    local_mat = _layer_local_matrix(layer)

    # Step 1 — intra-comp parent (within the leaf's containing comp).
    parent_uid = layer.get("parent_uid")
    if parent_uid is not None and parent_uid in layers_by_uid:
        parent_mat = _cross_comp_walk(parent_uid, layers_by_uid, visited)
        return mat4_mul(parent_mat, local_mat)

    # Step 2 — no intra-comp parent: check for a cross-comp wrapper.
    wrapper_uid = layer.get("wrapper_layer_uid")
    if wrapper_uid is None or wrapper_uid not in layers_by_uid:
        # No wrapper → we're at the active comp's top level. Root.
        return local_mat

    # Step 3 — cross-comp boundary. Q2 collapse-switch.
    wrapper = layers_by_uid[wrapper_uid]
    if _is_layer_collapsed(wrapper):
        # Q2C compose-through. Continue the walk through the wrapper's
        # own chain; the wrapper's local matrix joins via _walk.
        wrapper_world = _cross_comp_walk(wrapper_uid, layers_by_uid, visited)
        return mat4_mul(wrapper_world, local_mat)

    # Q2B: wrapper transform neutralized. Walk halts here — the inner
    # content's world space is the precomp's own coord system. AE 2D-
    # flattens the precomp at this boundary; the wrapper's transform
    # in the parent comp is conformed separately as an ordinary layer.
    return local_mat


def _parent_world_for_inverse(
    layer_uid: str,
    layers_by_uid: dict,
) -> List[float]:
    """Return the world matrix of the leaf's effective parent — the
    matrix that, when inverted, maps a world position back to the
    leaf's local space.

    For an intra-comp parented layer: the parent's world matrix.
    For a layer at the comp root above a collapsed wrapper: the
    wrapper's world matrix (Q2C).
    For a layer at the comp root above an uncollapsed wrapper: identity
    (Q2B — the inner space is sealed; local = world within the comp).
    For a layer with no parent and no wrapper: identity.
    """
    layer = layers_by_uid.get(layer_uid)
    if layer is None:
        return mat4_identity()

    parent_uid = layer.get("parent_uid")
    if parent_uid is not None and parent_uid in layers_by_uid:
        return build_cross_comp_world_matrix(parent_uid, layers_by_uid)

    wrapper_uid = layer.get("wrapper_layer_uid")
    if wrapper_uid is not None and wrapper_uid in layers_by_uid:
        wrapper = layers_by_uid[wrapper_uid]
        if _is_layer_collapsed(wrapper):
            return build_cross_comp_world_matrix(wrapper_uid, layers_by_uid)
        # Q2B: parent space is identity (inner space sealed).
        return mat4_identity()

    return mat4_identity()


def cross_comp_world_to_local(
    world_pos: List[float],
    layer_uid: str,
    layers_by_uid: dict,
) -> List[float]:
    """Cross-comp inverse of `build_cross_comp_world_matrix`'s effect
    on a position. Converts a target world-space coordinate back to
    the leaf's local space, honoring Q2 collapse-switch semantics
    (Q2C wrapper inverse, Q2B identity inverse).

    If the effective parent matrix is singular (a parent with a zero
    scale axis, for example), returns the world position unchanged
    rather than raising — same fall-back as `world_to_local_position`."""
    parent_world = _parent_world_for_inverse(layer_uid, layers_by_uid)
    inv = mat4_inv(parent_world)
    wx = world_pos[0] if len(world_pos) > 0 else 0.0
    wy = world_pos[1] if len(world_pos) > 1 else 0.0
    wz = world_pos[2] if len(world_pos) > 2 else 0.0
    return mat4_transform_point(inv, wx, wy, wz)


def conform_layers_with_cross_comp_chain(
    layers: List[dict],
    layers_by_uid: dict,
    src_center: List[float],
    tgt_center: List[float],
    uniform_scale: float,
    scale_z: bool = False,
    target_layers_by_uid: Optional[dict] = None,
) -> Dict[str, List[float]]:
    """Slot 12.5 Stage C M1 — Form-2 single entry point.

    Conforms a batch of layers via the cross-comp matrix walk.
    For each layer:
      1. Compute its world matrix via build_cross_comp_world_matrix.
      2. Read the layer's ANCHOR location in world space — NOT the
         translation column. AE's `layer.position` is the anchor's
         location in parent space, so the conform must operate on
         the anchor's world coordinate. (Stage D Item 4 Form-2
         resolution, 2026-05-17.)
      3. Apply the conform in world space:
            new_anchor_world.x = (anchor_world.x - src_center.x) * S + tgt_center.x
            new_anchor_world.y = (anchor_world.y - src_center.y) * S + tgt_center.y
            new_anchor_world.z = anchor_world.z * S  (when scale_z; else passthrough)
      4. Invert the leaf's effective PARENT matrix and apply to the
         new world anchor — the result is the new anchor location in
         parent space, i.e. the new `layer.position` Babysitter writes.

    Form-2 contract resolution (Stage D Item 4, supersedes Stage C
    Item 2's "translation column" read):
      - Pre-Item-4: `world_x = world_mat[3]` read the local-origin's
        world coord (the layer's bounds corner). For a layer with
        anchor == position (full-bleed centered solids and similar),
        the translation column was (0, 0, 0), NOT the layer's logical
        position — which would have produced wrong conformed
        positions when Babysitter wrote them as `layer.position`.
      - Post-Item-4: read `mat4_transform_point(world_mat, anchor)`.
        For the same anchor==position case the value reads the
        anchor's world coord, which equals the layer's source-side
        `position` value — matching AE's `layer.position` semantics
        directly so Babysitter writes a correct conformed value.

    Item 4 critical input — `target_layers_by_uid` (defaults to
    `layers_by_uid` for back-compat):
      - The source-world read (step 1) uses `layers_by_uid` —
        source-side layer dicts (`position`/`scale`/`anchor` from the
        scrape manifest).
      - The parent-inverse (step 4) uses `target_layers_by_uid` —
        TARGET-side layer dicts where each parent's transforms are
        the post-conform values (from scale_engine output). Without
        this split, M1 would invert the SOURCE parent's world and
        return a wrong local position for any chain where the parent
        was conformed (non-trivial conforms with any parent layer).
      - Investigation surfaced this on Corpus_01: source-side-only
        M1 returned (-280, 467) for the wrapper; target-side parent
        inverse returns (0, 0) — the correct AE-semantic position.
      - When the caller has no target-side layer data (identity
        conform, M1's own unit tests, etc.), omit the arg —
        defaults to the source-side dict and behavior matches the
        identity case (source == target world).

    Returns dict {layer_uid → [x, y, z]} of conformed AE-`layer.position`
    values (the anchor's location in parent space, post-conform).

    Form-2 vectorization constraint (scope-doc Q6): this is the SOLE
    call site for cross-comp matrix work in conform paths. A future
    numpy vectorized implementation can replace this function body
    without touching callers — they pass dicts and floats and receive
    dicts and floats. No numpy in Slot 12.5; the seam is structural.

    Layers without a `uid` are silently skipped (the walker keys on
    uid; no uid means no chain identity)."""
    result: Dict[str, List[float]] = {}
    for layer in layers:
        uid = layer.get("uid")
        if uid is None:
            continue

        world_mat = build_cross_comp_world_matrix(uid, layers_by_uid)

        # Form-2 anchor-aware read (Stage D Item 4 resolution).
        # The anchor's world coord = layer.position semantics; the
        # translation column would read bounds-corner world, which
        # AE consumers don't expect.
        anchor = layer.get("anchor") or [0.0, 0.0, 0.0]
        ax = anchor[0] if len(anchor) > 0 else 0.0
        ay = anchor[1] if len(anchor) > 1 else 0.0
        az = anchor[2] if len(anchor) > 2 else 0.0
        anchor_world = mat4_transform_point(world_mat, ax, ay, az)
        anchor_world_x = anchor_world[0]
        anchor_world_y = anchor_world[1]
        anchor_world_z = anchor_world[2]

        new_wx = ((anchor_world_x - src_center[0]) * uniform_scale) + tgt_center[0]
        new_wy = ((anchor_world_y - src_center[1]) * uniform_scale) + tgt_center[1]
        new_wz = anchor_world_z * uniform_scale if scale_z else anchor_world_z

        # Convert the new anchor world coord back to parent-local
        # space via parent's world inverse. `cross_comp_world_to_local`
        # uses `_parent_world_for_inverse` (NOT the layer's own world),
        # so the result is the anchor's location in parent space —
        # i.e. the new `layer.position`. Use the TARGET-side parent
        # dict (when supplied) so the parent's CONFORMED transform
        # drives the inverse — see Item 4 docstring above.
        parent_dict = target_layers_by_uid if target_layers_by_uid is not None else layers_by_uid
        result[uid] = cross_comp_world_to_local(
            [new_wx, new_wy, new_wz],
            uid,
            parent_dict,
        )
    return result


# ── End Slot 12.5 Stage C M1 ────────────────────────────────────────


def has_3d_rotated_parent(layer: dict, layers_by_index: dict) -> bool:
    """
    Return True if any ancestor has non-zero rotation_x, rotation_y, or orientation.
    These layers need full mat4 world-space decomposition.

    Cycle handling: tracks visited indices and halts if the chain loops
    back on itself. Returns the predicate's value as evaluated up to the
    cycle point (no True findings beyond it).
    """
    visited = set()
    parent_index = layer.get("parent_index", -1)
    comp_id = layer.get("containing_comp_id")

    is_tuple_keys = False
    if layers_by_index:
        first_key = next(iter(layers_by_index))
        is_tuple_keys = isinstance(first_key, tuple)

    while parent_index != -1:
        parent_key = (comp_id, parent_index) if is_tuple_keys else parent_index
        if parent_key in visited:
            log.warning(
                "Parent chain cycle detected in has_3d_rotated_parent",
                extra={"cycle_index": parent_key, "visited_chain": sorted(visited)},
            )
            return False
        visited.add(parent_key)
        parent = layers_by_index.get(parent_key)
        if parent is None:
            break
        if (parent.get("rotation_x") or 0.0) != 0.0:
            return True
        if (parent.get("rotation_y") or 0.0) != 0.0:
            return True
        ori = parent.get("orientation")
        if ori and any(v != 0 for v in ori):
            return True
        parent_index = parent.get("parent_index", -1)
    return False
