# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/occlusion/parent_kinematics.py
SOE parent-chain world/local position conversion — the first production
caller of core.kinematics's affine primitives.

Why this exists: scale_engine.py's ROOT/CHILD Anti-Shatter invariant
deliberately leaves a CHILD layer's conformed_transforms.position in
PARENT-LOCAL space (see scale_engine.py's module docstring). SOE
(engine.py's OcclusionEngine) reads conformed_transforms.position
directly and treats it as comp/world space when classifying a layer
against the safe-zone mask and when writing a corrected position back.
For a layer with no parent this is correct (local IS world). For a
parented layer it is not: the safe-zone classification is computed
against the wrong coordinates, and any correction written back is
interpreted by After Effects as a further parent-local offset, not the
world-space target SOE intended.

This module is opt-in (see engine.py's `kinematic_aware` flag, default
OFF) rather than a default-on change to the ROOT/CHILD invariant
itself — see docs/roadmap tracking issue #329 for why: the fixture
corpus has zero examples of a TYPE/LEGALS/etc.-tagged layer with a
parent (the only parented layers observed are camera-to-null dolly
rigs, already handled separately), so this ships as real, wired,
tested production code without changing default behavior for any
existing client project.

Reuses core.kinematics.create_affine_matrix / decompose_affine_matrix
(the free, LayerModel-independent primitives) rather than
KinematicSolver directly, since KinematicSolver couples to
LayerModel's field layout and re-derives the conform from source
values; this module instead operates on already-conformed dict layers
(the same shape OcclusionEngine already works with), composing
forward kinematics from conformed_transforms values only.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from core.kinematics import create_affine_matrix, decompose_affine_matrix

# Defensive recursion cap — a real AE parenting chain is never anywhere
# near this deep. Guards against a malformed/cyclic parent_index chain
# (should not happen; ScrapeManifest.cross_validate_parent_refs checks
# parent_uid/parent_index agreement at scrape time, but this module
# doesn't assume that validation ran) turning into infinite recursion.
_MAX_CHAIN_DEPTH = 64


def _layer_key(layer: dict) -> Tuple[Optional[int], int]:
    return (layer.get("containing_comp_id"), int(layer.get("index", 0) or 0))


def _parent_key(layer: dict) -> Optional[Tuple[Optional[int], int]]:
    """Returns the (containing_comp_id, parent_index) key for layer's
    parent, or None if layer has no parent. Parenting is scoped to a
    single comp — a layer's parent is always another layer in the same
    containing_comp_id, never a cross-comp reference."""
    p_idx = layer.get("parent_index", -1)
    if p_idx is None or p_idx <= 0:
        return None
    return (layer.get("containing_comp_id"), int(p_idx))


def _local_matrix(layer: dict) -> np.ndarray:
    """Builds the affine matrix for layer's own conformed local transform."""
    cf = layer.get("conformed_transforms") or {}
    position = cf.get("position") or layer.get("position") or [0.0, 0.0, 0.0]
    scale = cf.get("scale") or layer.get("scale") or [100.0, 100.0, 100.0]
    anchor = cf.get("anchor") or layer.get("anchor") or [0.0, 0.0, 0.0]
    rotation = cf.get("rotation")
    if rotation is None:
        rotation = layer.get("rotation_z")
    if rotation is None:
        rotation = layer.get("rotation") or 0.0
    return create_affine_matrix(position[:2], scale[:2], float(rotation), anchor[:2])


def world_matrix_for(
    layer: dict,
    layers_by_key: Dict[Tuple[Optional[int], int], dict],
) -> np.ndarray:
    """Forward kinematics: composes layer's world-space conformed affine
    matrix by walking up its parent chain within the same comp. A layer
    with no parent (or whose parent isn't present in layers_by_key) is
    its own world matrix — matches scale_engine.py's own "parent exists
    in manifest" ROOT/CHILD test."""
    chain: List[dict] = []
    current: Optional[dict] = layer
    seen: set = set()
    depth = 0
    while current is not None:
        key = _layer_key(current)
        if key in seen or depth >= _MAX_CHAIN_DEPTH:
            break
        seen.add(key)
        chain.append(current)
        depth += 1
        parent_key = _parent_key(current)
        current = layers_by_key.get(parent_key) if parent_key else None

    world = np.eye(3, dtype=np.float64)
    for node in reversed(chain):
        world = world @ _local_matrix(node)
    return world


def world_transform_for(
    layer: dict,
    layers_by_key: Dict[Tuple[Optional[int], int], dict],
) -> Dict[str, object]:
    """Layer's true world-space conformed transform — position, scale,
    and rotation all correctly compounded through the parent chain
    (not just position). A CHILD layer's own conformed_transforms.scale
    and .rotation are LOCAL values (relative to its parent, unchanged
    by scale_engine.py's ROOT/CHILD pass-through) — a rotated or
    non-100%-scaled parent chain changes the layer's effective WORLD
    scale/rotation even though its own local fields never do. Bounding-
    box sizing and OBB rotation for the safe-zone mask both need the
    world values, not the local ones, or classification is wrong even
    when only reading (not correcting) a parented layer's placement.
    """
    world = world_matrix_for(layer, layers_by_key)
    anchor = (layer.get("conformed_transforms") or {}).get("anchor") \
        or layer.get("anchor") or [0.0, 0.0, 0.0]
    return decompose_affine_matrix(world, anchor[:2])


def world_position_for(
    layer: dict,
    layers_by_key: Dict[Tuple[Optional[int], int], dict],
) -> List[float]:
    """Layer's true world-space conformed position (position[2] is
    always 0.0 — this module is 2D, matching create_affine_matrix)."""
    return world_transform_for(layer, layers_by_key)["position"]


def local_position_for_world_target(
    layer: dict,
    layers_by_key: Dict[Tuple[Optional[int], int], dict],
    world_target: List[float],
) -> List[float]:
    """Inverse kinematics: given a desired WORLD-space position for
    layer (scale/rotation/anchor unchanged — SOE only ever moves
    position), returns the LOCAL position to write into
    conformed_transforms so After Effects renders layer at that world
    position. A root layer's local position IS its world position."""
    parent_key = _parent_key(layer)
    parent = layers_by_key.get(parent_key) if parent_key else None
    if parent is None:
        return [float(world_target[0]), float(world_target[1]), 0.0]

    parent_world = world_matrix_for(parent, layers_by_key)
    det = np.linalg.det(parent_world[:2, :2])
    if abs(det) < 1e-6:
        # Singular parent (zero-scale null) — no invertible local
        # answer exists. Fall back to leaving position unchanged
        # (caller's original local position) rather than producing a
        # nonsensical result; matches KinematicSolver's Center-World
        # guardrail posture of never propagating a NaN/Inf position.
        cf = layer.get("conformed_transforms") or {}
        fallback = cf.get("position") or layer.get("position") or [0.0, 0.0, 0.0]
        return [float(fallback[0]), float(fallback[1]), 0.0]

    cf = layer.get("conformed_transforms") or {}
    scale = cf.get("scale") or layer.get("scale") or [100.0, 100.0, 100.0]
    anchor = cf.get("anchor") or layer.get("anchor") or [0.0, 0.0, 0.0]
    rotation = cf.get("rotation")
    if rotation is None:
        rotation = layer.get("rotation_z")
    if rotation is None:
        rotation = layer.get("rotation") or 0.0

    target_world_matrix = create_affine_matrix(
        [float(world_target[0]), float(world_target[1])],
        scale[:2], float(rotation), anchor[:2],
    )
    local_matrix = np.linalg.inv(parent_world) @ target_world_matrix
    decomp = decompose_affine_matrix(local_matrix, anchor[:2])
    return decomp["position"]
