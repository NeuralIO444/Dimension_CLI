# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/kinematics.py
Next-Gen Relayout Architecture — World-Space Forward & Inverse Kinematics Engine.

Solves multi-generational After Effects parenting chains (Child -> Null 1 -> Null 2 -> Gimbal)
in comp world space, completely eradicating compound double-scaling (S x S) and anchor-point
orbital drift.

Mathematical Invariants:
  1. Forward Kinematics:
     M_world(i) = M_world(parent) @ M_local(i)
  2. World-Space Aspect Remap:
     M_world'(i) = T_conform @ M_world(i)
  3. Inverse Kinematics Decomposition:
     M_local'(i) = (M_world'(parent))^(-1) @ M_world'(i)
  4. Determinant Non-Inversion & Zero-Scale Fallback:
     If det(M_parent) < 1e-6 (singular / black-hole null), gracefully defaults
     to Center-World conform with a structured diagnostic warning.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from models.scrape_manifest import LayerModel


def create_affine_matrix(
    position: Tuple[float, float] | List[float],
    scale: Tuple[float, float] | List[float],
    rotation_deg: float = 0.0,
    anchor: Tuple[float, float] | List[float] = (0.0, 0.0),
) -> np.ndarray:
    """Builds a 3x3 2D affine transformation matrix.

    M = T(pos) @ R(rot) @ S(scale / 100) @ T(-anchor)
    """
    px, py = float(position[0]), float(position[1])
    sx, sy = float(scale[0]) / 100.0, float(scale[1]) / 100.0
    ax, ay = float(anchor[0]), float(anchor[1])
    rad = math.radians(float(rotation_deg))
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)

    # Translation to position
    t_pos = np.array([
        [1.0, 0.0, px],
        [0.0, 1.0, py],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)

    # Rotation
    r_mat = np.array([
        [cos_r, -sin_r, 0.0],
        [sin_r,  cos_r, 0.0],
        [0.0,    0.0,   1.0],
    ], dtype=np.float64)

    # Scale
    s_mat = np.array([
        [sx,  0.0, 0.0],
        [0.0, sy,  0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)

    # Translation from anchor point
    t_anch = np.array([
        [1.0, 0.0, -ax],
        [0.0, 1.0, -ay],
        [0.0, 0.0,  1.0],
    ], dtype=np.float64)

    return t_pos @ r_mat @ s_mat @ t_anch


def decompose_affine_matrix(
    matrix: np.ndarray,
    anchor: Tuple[float, float] | List[float] = (0.0, 0.0),
) -> Dict[str, Any]:
    """Decomposes a 3x3 affine matrix back into position, scale (%), and rotation (deg).

    Preserves the specified anchor point and calculates the matching position.
    """
    ax, ay = float(anchor[0]), float(anchor[1])
    a = float(matrix[0, 0])
    b = float(matrix[0, 1])
    c = float(matrix[0, 2])
    d = float(matrix[1, 0])
    e = float(matrix[1, 1])
    f = float(matrix[1, 2])

    # Position in parent space is where the anchor point transforms to:
    # P = M @ [ax, ay, 1]^T
    pos_x = a * ax + b * ay + c
    pos_y = d * ax + e * ay + f

    # Scale extraction from column vectors
    sx = math.sqrt(a * a + d * d)
    det = a * e - b * d
    sy = math.sqrt(b * b + e * e)
    if det < 0:
        sy = -sy

    # Rotation extraction
    rot_rad = math.atan2(d, a)
    rot_deg = math.degrees(rot_rad)

    return {
        "position": [round(pos_x, 4), round(pos_y, 4), 0.0],
        "scale": [round(sx * 100.0, 4), round(sy * 100.0, 4), 100.0],
        "rotation": round(rot_deg, 4),
        "anchor": [round(ax, 4), round(ay, 4), 0.0],
    }


class KinematicSolver:
    """Evaluates parenting trees in world space and produces normalized local transforms."""

    def __init__(
        self,
        layers: List[LayerModel],
        source_width: int,
        source_height: int,
        target_width: int,
        target_height: int,
        uniform_scale: float,
        center_offset: Tuple[float, float] = (0.0, 0.0),
    ):
        self.layers = layers
        self.src_w = source_width
        self.src_h = source_height
        self.tgt_w = target_width
        self.tgt_h = target_height
        self.scale_factor = uniform_scale
        self.center_offset = center_offset

        self._layers_by_index: Dict[int, LayerModel] = {l.index: l for l in layers}
        self._layers_by_uid: Dict[str, LayerModel] = {l.uid: l for l in layers if l.uid}
        self._parent_map: Dict[int, Optional[int]] = {}

        for l in layers:
            p_idx = getattr(l, "parent_index", -1)
            if p_idx is not None and p_idx > 0 and p_idx != l.index and p_idx in self._layers_by_index:
                self._parent_map[l.index] = p_idx
            else:
                self._parent_map[l.index] = None

    def compute_local_matrix(self, layer: LayerModel) -> np.ndarray:
        """Constructs the initial local affine matrix for a layer."""
        pos = layer.position[:2] if layer.position else [self.src_w / 2.0, self.src_h / 2.0]
        scale = layer.scale[:2] if layer.scale else [100.0, 100.0]
        rot = getattr(layer, "rotation_z", None)
        if rot is None:
            rot = getattr(layer, "rotation", 0.0) or 0.0
        anchor = layer.anchor[:2] if layer.anchor else [0.0, 0.0]
        return create_affine_matrix(pos, scale, rot, anchor)

    def compute_world_matrix(self, layer_index: int, memo: Optional[Dict[int, np.ndarray]] = None) -> np.ndarray:
        """Recursively computes the world-space matrix for a layer via Forward Kinematics."""
        if memo is None:
            memo = {}
        if layer_index in memo:
            return memo[layer_index]

        layer = self._layers_by_index.get(layer_index)
        if not layer:
            return np.eye(3, dtype=np.float64)

        local_mat = self.compute_local_matrix(layer)
        parent_index = self._parent_map.get(layer_index)

        if parent_index is not None and parent_index != layer_index:
            parent_world = self.compute_world_matrix(parent_index, memo)
            world_mat = parent_world @ local_mat
        else:
            world_mat = local_mat

        memo[layer_index] = world_mat
        return world_mat

    def solve(
        self,
        gravity_offsets: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Solves entire hierarchy, applying conform remap in world space and decomposing locally."""
        offsets = gravity_offsets or {}
        world_matrices: Dict[int, np.ndarray] = {}
        conformed_world_matrices: Dict[int, np.ndarray] = {}
        results: Dict[str, Dict[str, Any]] = {}

        # 1. Forward Kinematics: Extract initial world matrices for all layers
        for layer in self.layers:
            self.compute_world_matrix(layer.index, world_matrices)

        # 2. Compute Conformed World Matrices
        # T_conform = T(center_offset + gravity_offset) @ S(uniform_scale)
        for layer in self.layers:
            w_mat = world_matrices[layer.index]
            uid = layer.uid or f"idx-{layer.index}"
            g_offset = offsets.get(uid, (0.0, 0.0))

            # Target world-space transformation matrix
            # Maps source comp coords (x, y) to target comp coords:
            # x' = x * S + center_offset_x + g_offset_x
            # y' = y * S + center_offset_y + g_offset_y
            t_remap = np.array([
                [self.scale_factor, 0.0, self.center_offset[0] + g_offset[0]],
                [0.0, self.scale_factor, self.center_offset[1] + g_offset[1]],
                [0.0, 0.0, 1.0],
            ], dtype=np.float64)

            conformed_world_matrices[layer.index] = t_remap @ w_mat

        # 3. Inverse Kinematics: Decompose conformed world matrix into local space
        for layer in self.layers:
            uid = layer.uid or f"idx-{layer.index}"
            cw_mat = conformed_world_matrices[layer.index]
            parent_index = self._parent_map.get(layer.index)
            anchor = layer.anchor[:2] if layer.anchor else [0.0, 0.0]

            if parent_index is not None and parent_index in conformed_world_matrices:
                parent_cw_mat = conformed_world_matrices[parent_index]
                det = np.linalg.det(parent_cw_mat[:2, :2])

                if abs(det) < 1e-6:
                    # Parent is singular (zero-scale) -> Guardrail: Fallback to Center-World
                    fallback_pos = [self.tgt_w / 2.0, self.tgt_h / 2.0, 0.0]
                    results[uid] = {
                        "position": fallback_pos,
                        "scale": [round(100.0 * self.scale_factor, 4), round(100.0 * self.scale_factor, 4), 100.0],
                        "rotation": 0.0,
                        "anchor": [anchor[0], anchor[1], 0.0],
                        "relayout_status": "FALLBACK_CENTER_WORLD",
                        "guardrail_reason": "SINGULAR_PARENT_MATRIX",
                    }
                    continue

                try:
                    parent_inv = np.linalg.inv(parent_cw_mat)
                    conformed_local_mat = parent_inv @ cw_mat
                except np.linalg.LinAlgError:
                    # Inversion error fallback
                    fallback_pos = [self.tgt_w / 2.0, self.tgt_h / 2.0, 0.0]
                    results[uid] = {
                        "position": fallback_pos,
                        "scale": [round(100.0 * self.scale_factor, 4), round(100.0 * self.scale_factor, 4), 100.0],
                        "rotation": 0.0,
                        "anchor": [anchor[0], anchor[1], 0.0],
                        "relayout_status": "FALLBACK_CENTER_WORLD",
                        "guardrail_reason": "MATRIX_INVERSION_FAILURE",
                    }
                    continue
            else:
                # Root layer (no parent) -> local is world
                conformed_local_mat = cw_mat

            decomp = decompose_affine_matrix(conformed_local_mat, anchor)
            decomp["relayout_status"] = "OK"
            results[uid] = decomp

        return results


# ── 4x4 3D Homogeneous Kinematics Engine (TASK-ENG-07 / Issue #296) ────────


def create_3d_affine_matrix(
    position: Tuple[float, float, float] | List[float],
    scale: Tuple[float, float, float] | List[float],
    rotation_xyz: Tuple[float, float, float] | List[float] = (0.0, 0.0, 0.0),
    orientation_xyz: Tuple[float, float, float] | List[float] = (0.0, 0.0, 0.0),
    anchor: Tuple[float, float, float] | List[float] = (0.0, 0.0, 0.0),
) -> np.ndarray:
    """Builds a 4x4 homogeneous 3D transformation matrix for AE 3D layers and cameras.

    Matrix composition order in AE 3D:
      M = T(pos) @ R_orient @ R_rot @ S(scale / 100) @ T(-anchor)
      where R = R_z @ R_y @ R_x
    """
    px = float(position[0]) if len(position) > 0 else 0.0
    py = float(position[1]) if len(position) > 1 else 0.0
    pz = float(position[2]) if len(position) > 2 else 0.0

    sx = float(scale[0]) / 100.0 if len(scale) > 0 else 1.0
    sy = float(scale[1]) / 100.0 if len(scale) > 1 else 1.0
    sz = float(scale[2]) / 100.0 if len(scale) > 2 else 1.0

    ax = float(anchor[0]) if len(anchor) > 0 else 0.0
    ay = float(anchor[1]) if len(anchor) > 1 else 0.0
    az = float(anchor[2]) if len(anchor) > 2 else 0.0

    # Translation to Position
    t_pos = np.eye(4, dtype=np.float64)
    t_pos[0, 3] = px
    t_pos[1, 3] = py
    t_pos[2, 3] = pz

    # Helper Euler Matrix Builder (Z @ Y @ X)
    def _euler_matrix(rx_deg: float, ry_deg: float, rz_deg: float) -> np.ndarray:
        rx, ry, rz = math.radians(rx_deg), math.radians(ry_deg), math.radians(rz_deg)
        cx, sx_val = math.cos(rx), math.sin(rx)
        cy, sy_val = math.cos(ry), math.sin(ry)
        cz, sz_val = math.cos(rz), math.sin(rz)

        mat_x = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, cx, -sx_val, 0.0],
            [0.0, sx_val, cx, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ], dtype=np.float64)

        mat_y = np.array([
            [cy, 0.0, sy_val, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [-sy_val, 0.0, cy, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ], dtype=np.float64)

        mat_z = np.array([
            [cz, -sz_val, 0.0, 0.0],
            [sz_val, cz, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ], dtype=np.float64)

        return mat_z @ mat_y @ mat_x

    rot_mat = _euler_matrix(rotation_xyz[0], rotation_xyz[1], rotation_xyz[2])
    orient_mat = _euler_matrix(orientation_xyz[0], orientation_xyz[1], orientation_xyz[2])
    total_rot = orient_mat @ rot_mat

    # Scale Matrix
    s_mat = np.eye(4, dtype=np.float64)
    s_mat[0, 0] = sx
    s_mat[1, 1] = sy
    s_mat[2, 2] = sz

    # Translation from Anchor Point
    t_anch = np.eye(4, dtype=np.float64)
    t_anch[0, 3] = -ax
    t_anch[1, 3] = -ay
    t_anch[2, 3] = -az

    return t_pos @ total_rot @ s_mat @ t_anch


def decompose_3d_affine_matrix(
    matrix: np.ndarray,
    anchor: Tuple[float, float, float] | List[float] = (0.0, 0.0, 0.0),
) -> Dict[str, Any]:
    """Decomposes a 4x4 homogeneous transformation matrix back into 3D transforms.

    Calculates:
      - Position (x, y, z) matching the preserved 3D anchor point.
      - 3D Scale (sx, sy, sz) in %.
      - 3-Axis Euler Rotation (rot_x, rot_y, rot_z) in degrees.
    """
    ax = float(anchor[0]) if len(anchor) > 0 else 0.0
    ay = float(anchor[1]) if len(anchor) > 1 else 0.0
    az = float(anchor[2]) if len(anchor) > 2 else 0.0

    # 1. Transform anchor point to get world/parent position:
    # P = M @ [ax, ay, az, 1]^T
    anch_vec = np.array([ax, ay, az, 1.0], dtype=np.float64)
    pos_vec = matrix @ anch_vec
    pos_x, pos_y, pos_z = pos_vec[0], pos_vec[1], pos_vec[2]

    # 2. Extract column vectors for scale
    c0 = matrix[:3, 0]
    c1 = matrix[:3, 1]
    c2 = matrix[:3, 2]

    sx = float(np.linalg.norm(c0))
    sy = float(np.linalg.norm(c1))
    sz = float(np.linalg.norm(c2))

    # Protect against zero division
    r0 = c0 / sx if sx > 1e-9 else np.array([1.0, 0.0, 0.0])
    r1 = c1 / sy if sy > 1e-9 else np.array([0.0, 1.0, 0.0])
    r2 = c2 / sz if sz > 1e-9 else np.array([0.0, 0.0, 1.0])

    rot_3x3 = np.column_stack([r0, r1, r2])
    det = float(np.linalg.det(rot_3x3))
    if det < 0:
        sz = -sz
        r2 = -r2
        rot_3x3[:, 2] = r2

    # 3. Euler angles from 3x3 rotation matrix (Z-Y-X convention)
    # sin(ry) = -rot_3x3[2, 0]
    sin_ry = max(-1.0, min(1.0, -float(rot_3x3[2, 0])))
    ry_rad = math.asin(sin_ry)
    cos_ry = math.cos(ry_rad)

    if abs(cos_ry) > 1e-6:
        rx_rad = math.atan2(float(rot_3x3[2, 1]), float(rot_3x3[2, 2]))
        rz_rad = math.atan2(float(rot_3x3[1, 0]), float(rot_3x3[0, 0]))
    else:
        # Gimbal lock
        rx_rad = math.atan2(-float(rot_3x3[1, 2]), float(rot_3x3[1, 1]))
        rz_rad = 0.0

    return {
        "position": [round(pos_x, 4), round(pos_y, 4), round(pos_z, 4)],
        "scale": [round(sx * 100.0, 4), round(sy * 100.0, 4), round(sz * 100.0, 4)],
        "rotation_x": round(math.degrees(rx_rad), 4),
        "rotation_y": round(math.degrees(ry_rad), 4),
        "rotation_z": round(math.degrees(rz_rad), 4),
        "anchor": [round(ax, 4), round(ay, 4), round(az, 4)],
    }


class KinematicSolver3D:
    """Solves 3D camera and multi-axis gimbal parenting hierarchies in 3D world space."""

    def __init__(
        self,
        layers: List[LayerModel],
        source_width: int,
        source_height: int,
        target_width: int,
        target_height: int,
        uniform_scale: float,
        depth_scale: float = 1.0,
        center_offset: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    ):
        self.layers = layers
        self.src_w = source_width
        self.src_h = source_height
        self.tgt_w = target_width
        self.tgt_h = target_height
        self.scale_factor = uniform_scale
        self.depth_scale = depth_scale
        self.center_offset = center_offset

        self._layers_by_index: Dict[int, LayerModel] = {l.index: l for l in layers}
        self._parent_map: Dict[int, Optional[int]] = {}

        for l in layers:
            p_idx = getattr(l, "parent_index", -1)
            if p_idx is not None and p_idx > 0 and p_idx != l.index and p_idx in self._layers_by_index:
                self._parent_map[l.index] = p_idx
            else:
                self._parent_map[l.index] = None

    def compute_local_3d_matrix(self, layer: LayerModel) -> np.ndarray:
        pos = layer.position if layer.position else [self.src_w / 2.0, self.src_h / 2.0, 0.0]
        scale = layer.scale if layer.scale else [100.0, 100.0, 100.0]
        anchor = layer.anchor if layer.anchor else [0.0, 0.0, 0.0]

        rx = getattr(layer, "rotation_x", 0.0) or 0.0
        ry = getattr(layer, "rotation_y", 0.0) or 0.0
        rz = getattr(layer, "rotation_z", None)
        if rz is None:
            rz = getattr(layer, "rotation", 0.0) or 0.0

        orient = getattr(layer, "orientation", (0.0, 0.0, 0.0)) or (0.0, 0.0, 0.0)
        return create_3d_affine_matrix(pos, scale, (rx, ry, rz), orient, anchor)

    def compute_world_3d_matrix(self, layer_index: int, memo: Optional[Dict[int, np.ndarray]] = None) -> np.ndarray:
        if memo is None:
            memo = {}
        if layer_index in memo:
            return memo[layer_index]

        layer = self._layers_by_index.get(layer_index)
        if not layer:
            return np.eye(4, dtype=np.float64)

        local_mat = self.compute_local_3d_matrix(layer)
        parent_index = self._parent_map.get(layer_index)

        if parent_index is not None and parent_index != layer_index:
            parent_world = self.compute_world_3d_matrix(parent_index, memo)
            world_mat = parent_world @ local_mat
        else:
            world_mat = local_mat

        memo[layer_index] = world_mat
        return world_mat

    def solve(self) -> Dict[str, Dict[str, Any]]:
        world_matrices: Dict[int, np.ndarray] = {}
        conformed_world_matrices: Dict[int, np.ndarray] = {}
        results: Dict[str, Dict[str, Any]] = {}

        for layer in self.layers:
            self.compute_world_3d_matrix(layer.index, world_matrices)

        # 3D Conformed Remap Matrix
        t_remap = np.array([
            [self.scale_factor, 0.0, 0.0, self.center_offset[0]],
            [0.0, self.scale_factor, 0.0, self.center_offset[1]],
            [0.0, 0.0, self.depth_scale, self.center_offset[2]],
            [0.0, 0.0, 0.0, 1.0],
        ], dtype=np.float64)

        for layer in self.layers:
            conformed_world_matrices[layer.index] = t_remap @ world_matrices[layer.index]

        for layer in self.layers:
            uid = layer.uid or f"idx-{layer.index}"
            cw_mat = conformed_world_matrices[layer.index]
            parent_index = self._parent_map.get(layer.index)
            anchor = layer.anchor if layer.anchor else [0.0, 0.0, 0.0]

            if parent_index is not None and parent_index in conformed_world_matrices:
                parent_cw_mat = conformed_world_matrices[parent_index]
                det = np.linalg.det(parent_cw_mat[:3, :3])

                if abs(det) < 1e-6:
                    fallback_pos = [self.tgt_w / 2.0, self.tgt_h / 2.0, 0.0]
                    results[uid] = {
                        "position": fallback_pos,
                        "scale": [round(100.0 * self.scale_factor, 4)] * 3,
                        "rotation_x": 0.0,
                        "rotation_y": 0.0,
                        "rotation_z": 0.0,
                        "anchor": list(anchor),
                        "relayout_status": "FALLBACK_CENTER_WORLD",
                        "guardrail_reason": "SINGULAR_PARENT_MATRIX",
                    }
                    continue

                try:
                    parent_inv = np.linalg.inv(parent_cw_mat)
                    conformed_local_mat = parent_inv @ cw_mat
                except np.linalg.LinAlgError:
                    fallback_pos = [self.tgt_w / 2.0, self.tgt_h / 2.0, 0.0]
                    results[uid] = {
                        "position": fallback_pos,
                        "scale": [round(100.0 * self.scale_factor, 4)] * 3,
                        "rotation_x": 0.0,
                        "rotation_y": 0.0,
                        "rotation_z": 0.0,
                        "anchor": list(anchor),
                        "relayout_status": "FALLBACK_CENTER_WORLD",
                        "guardrail_reason": "MATRIX_INVERSION_FAILURE",
                    }
                    continue
            else:
                conformed_local_mat = cw_mat

            decomp = decompose_3d_affine_matrix(conformed_local_mat, anchor)
            decomp["relayout_status"] = "OK"
            results[uid] = decomp

        return results
