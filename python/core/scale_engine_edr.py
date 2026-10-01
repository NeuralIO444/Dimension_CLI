# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/scale_engine_edr.py
Sprint 4 extraction — _apply_equal_different_resolution_rule_set body.

This module contains only the extracted function body.  The public API
remains on ScaleEngine in scale_engine.py — nothing else should import
from here directly.
"""

from typing import Dict

from models.conformed_manifest import (
    ConformedTransforms,
    ConformedCameraProperties,
    ConformedLightProperties,
)
from core.logger import log
from core.matrix_math import (
    conform_layer_world_space,
    has_nonuniform_parent_scale,
    has_3d_rotated_parent,
)
from core.classify import is_layer_root, should_scale_z
from core.layer_utils import canon_tag as _canon


def apply_equal_different_resolution_rule_set(
    engine,
    *,
    orig_w: int,
    orig_h: int,
    src_cx: float,
    src_cy: float,
    tgt_cx: float,
    tgt_cy: float,
    S: float,
    fill_S: float,
    K: float,
    is_3d_camera_scene,
    layer_indices,
    layers_by_index: Dict[int, dict],
) -> list:
    """Extracted body of ScaleEngine._apply_equal_different_resolution_rule_set.

    See scale_engine.py docstring for the full specification.
    ``engine`` is the ScaleEngine instance (provides self.* access).
    """
    # Q2 lock — pure uniform scale, no bleed, no K branching.
    S_uniform = (engine.target_width / orig_w) if orig_w > 0 else 1.0

    log.info(
        "Equal_different_resolution rule set — uniform scale",
        extra={
            "S": round(S_uniform, 6),
            "target": f"{int(engine.target_width)}x{int(engine.target_height)}",
            "ratio_h_diagnostic": (
                round(engine.target_height / orig_h, 6) if orig_h > 0 else None
            ),
        },
    )

    conformed_layers = []
    centroids = []
    # Reset per-run accumulators (also initialized in __init__).
    engine.collapsed_layer_warnings = []
    engine.child_layers = []
    engine.tag_passthrough = []

    _resolution = getattr(engine, "placement_resolution", None)
    if _resolution is not None:
        _sealed_cids = _resolution.sealed_precomp_cids
    else:
        _scene_active_fb = getattr(engine, "scene_preserve_active", False)
        _all_cids_fb = {getattr(_l0, "containing_comp_id", None)
                        for _l0 in engine.manifest.layers}
        _root_cid_fb = next(
            (_c for _c in (getattr(_l0, "containing_comp_id", None)
                           for _l0 in engine.manifest.layers)
             if _c is not None), None)
        _sealed_cids = (
            (_all_cids_fb - {None, _root_cid_fb})
            if (_scene_active_fb and _root_cid_fb is not None) else set())

    for layer in engine.manifest.layers:
        # Fix 1 — per-layer comp center. Layers in nested precomps with
        # different dimensions use the nested comp's own center as origin
        # for center-remap math; root-comp layers use (src_cx, src_cy).
        _comp_id = getattr(layer, "containing_comp_id", None)
        _lsrc_cx, _lsrc_cy = engine._cx_cy_for_comp(_comp_id, src_cx, src_cy)

        kind = (getattr(layer, "layer_kind", "av") or "av").lower()
        scale_z = should_scale_z(layer, is_3d_camera_scene)

        collapse = getattr(layer, "collapseTransformations", False)
        if collapse:
            engine.collapsed_layer_warnings.append(layer.name)

        p = layer.position or [0.0, 0.0, 0.0]
        s = layer.scale or [100.0, 100.0, 100.0]
        rz = layer.rotation_z or 0.0
        rx = layer.rotation_x or 0.0
        ry = layer.rotation_y or 0.0
        ori = layer.orientation
        a = layer.anchor or [0.0, 0.0, 0.0]

        parent_idx = getattr(layer, "parent_index", -1)
        is_root = is_layer_root(parent_idx, layer_indices, getattr(layer, "containing_comp_id", None))

        # ── WORLD-SPACE OVERRIDE conditions (preserved from narrow) ──
        # Same logic as narrow — collapse / non-uniform parent / 3D-rotated
        # parent each require world-space matrix decomposition. Uses
        # S_uniform instead of the bleed-adjusted S the narrow path uses.
        is_tuple_keys = False
        if layers_by_index:
            first_key = next(iter(layers_by_index))
            is_tuple_keys = isinstance(first_key, tuple)
        lookup_key = (getattr(layer, "containing_comp_id", None), layer.index) if is_tuple_keys else layer.index
        layer_dict_for_ws = layers_by_index.get(lookup_key, {})
        collapse_triggered = not is_root and collapse
        parent_chain_triggered = (
            not is_root
            and not collapse
            and (
                has_nonuniform_parent_scale(layer_dict_for_ws, layers_by_index)
                or has_3d_rotated_parent(layer_dict_for_ws, layers_by_index)
            )
        )
        use_world_space = collapse_triggered or parent_chain_triggered

        # ── TAG: GUIDE / PROTECT — structural pass-through ──────────
        content_tag = getattr(layer, "content_tag", None)
        content_tag_canon = _canon(content_tag)
        if content_tag_canon in ("GUIDE", "PROTECT"):
            # Sprint 1 Fix 1: if GUIDE was assigned by an AE label color
            # (manual_label source), reroute to CENTER — label colors are
            # organisational and should not suppress the conform transform.
            if content_tag_canon == "GUIDE":
                _src = getattr(layer, "content_tag_source", None) or ""
                if _src == "manual_label":
                    content_tag_canon = "CENTER"
                    log.debug("scale.layer.guide_manual_label_reroute",
                              extra={"layer": layer.name, "rerouted_to": "CENTER"})
                    # fall through to position + scale math below
                else:
                    tf = ConformedTransforms(
                        is_root=False,
                        position=list(p),
                        scale=list(s),
                        rotation=rz,
                        anchor=list(a),
                        rotation_x=rx if rx != 0 else None,
                        rotation_y=ry if ry != 0 else None,
                        orientation=ori,
                    )
                    layer_dict = layer.model_dump()
                    layer_dict["conformed_transforms"] = tf.model_dump()
                    conformed_layers.append(layer_dict)
                    engine.tag_passthrough.append(layer.name)
                    continue
            else:
                # PROTECT — always pass-through regardless of source
                tf = ConformedTransforms(
                    is_root=False,
                    position=list(p),
                    scale=list(s),
                    rotation=rz,
                    anchor=list(a),
                    rotation_x=rx if rx != 0 else None,
                    rotation_y=ry if ry != 0 else None,
                    orientation=ori,
                )
                layer_dict = layer.model_dump()
                layer_dict["conformed_transforms"] = tf.model_dump()
                conformed_layers.append(layer_dict)
                engine.tag_passthrough.append(layer.name)
                continue

        if not is_root and not use_world_space:
            engine.child_layers.append(layer.name)

        # ── Position + scale ───────────────────────────────────────
        if use_world_space:
            p_conformed = conform_layer_world_space(
                layers_by_index[lookup_key],
                layers_by_index,
                [_lsrc_cx, _lsrc_cy],
                [tgt_cx, tgt_cy],
                S_uniform,
                scale_z=scale_z,
            )
            if collapse_triggered:
                s_conformed = [
                    s[0] * S_uniform,
                    s[1] * S_uniform,
                    (s[2] * S_uniform if scale_z else s[2]) if len(s) > 2 else 100.0,
                ]
            else:
                s_conformed = list(s)
            is_root = True
        elif is_root:
            _is_nested_scene_unit = (
                getattr(layer, "containing_comp_id", None) in _sealed_cids)
            if _is_nested_scene_unit:
                p_conformed = list(p)
                s_conformed = list(s)
                log.debug("scale.layer.scene_preserve_nested_passthrough",
                          extra={"layer": layer.name,
                                 "containing_comp_id":
                                     getattr(layer, "containing_comp_id", None)})
            else:
                # Pure uniform-scale center-remap. No K split, no gravity,
                # no per-tag overrides — every root layer goes through
                # this single formula.
                p_conformed = [
                    ((p[0] - _lsrc_cx) * S_uniform) + tgt_cx,
                    ((p[1] - _lsrc_cy) * S_uniform) + tgt_cy,
                    (p[2] * S_uniform if scale_z else p[2]) if len(p) > 2 else 0.0,
                ]
                s_conformed = [
                    s[0] * S_uniform,
                    s[1] * S_uniform,
                    (s[2] * S_uniform if scale_z else s[2]) if len(s) > 2 else 100.0,
                ]
        else:
            # CHILD: pass position and scale through — parent cascade
            # handles them. SHATTER GUARD enforces this below.
            p_conformed = list(p)
            s_conformed = list(s)

        a_conformed = list(a)
        centroids.append(p_conformed)

        # ── Camera / light intrinsics — pure S_uniform ─────────────
        cam_conformed = None
        light_conformed = None
        if kind == "camera" and layer.camera is not None:
            cam_src = layer.camera
            poi = None
            if cam_src.pointOfInterest is not None:
                if not is_root:
                    poi = list(cam_src.pointOfInterest)
                else:
                    poi_z = (
                        cam_src.pointOfInterest[2]
                        if len(cam_src.pointOfInterest) > 2 else 0.0
                    )
                    is_comp_3d_scene = False
                    if isinstance(is_3d_camera_scene, set):
                        is_comp_3d_scene = getattr(layer, "containing_comp_id", None) in is_3d_camera_scene
                    elif isinstance(is_3d_camera_scene, dict):
                        is_comp_3d_scene = is_3d_camera_scene.get(getattr(layer, "containing_comp_id", None), False)
                    else:
                        is_comp_3d_scene = bool(is_3d_camera_scene)

                    poi = [
                        ((cam_src.pointOfInterest[0] - _lsrc_cx) * S_uniform) + tgt_cx,
                        ((cam_src.pointOfInterest[1] - _lsrc_cy) * S_uniform) + tgt_cy,
                        poi_z * S_uniform if is_comp_3d_scene else poi_z,
                    ]
            cam_conformed = ConformedCameraProperties(
                zoom=(cam_src.zoom * S_uniform) if cam_src.zoom is not None else None,
                pointOfInterest=poi,
                depthOfField=cam_src.depthOfField,
                focusDistance=(
                    cam_src.focusDistance * S_uniform
                    if cam_src.focusDistance is not None else None
                ),
                aperture=cam_src.aperture,
                blurLevel=cam_src.blurLevel,
            )
        elif kind == "light" and layer.light is not None:
            lt_src = layer.light
            light_conformed = ConformedLightProperties(
                lightType=lt_src.lightType,
                intensity=lt_src.intensity,
                color=list(lt_src.color) if lt_src.color is not None else None,
                coneAngle=lt_src.coneAngle,
                coneFeather=lt_src.coneFeather,
                falloff=lt_src.falloff,
                falloffDistance=(
                    lt_src.falloffDistance * S_uniform
                    if lt_src.falloffDistance is not None else None
                ),
                radius=(
                    lt_src.radius * S_uniform
                    if lt_src.radius is not None else None
                ),
                castsShadows=lt_src.castsShadows,
            )

        tf = ConformedTransforms(
            is_root=is_root,
            position=p_conformed,
            scale=s_conformed,
            rotation=rz,
            anchor=a_conformed,
            rotation_x=rx if rx != 0 else None,
            rotation_y=ry if ry != 0 else None,
            orientation=ori,
            camera=cam_conformed,
            light=light_conformed,
        )
        layer_dict = layer.model_dump()
        layer_dict["conformed_transforms"] = tf.model_dump()
        conformed_layers.append(layer_dict)

    engine._check_spatial_bounds(centroids)

    # ── SHATTER GUARD ─────────────────────────────────────────────
    # Children must not have position or scale mutated; parent
    # cascade handles them. Same invariant as the narrow path.
    from core.scale_engine import SpatialBoundError
    for layer, out_dict in zip(engine.manifest.layers, conformed_layers):
        tf = out_dict.get("conformed_transforms")
        if tf is None:
            continue
        if not tf.get("is_root", True):
            src_p = layer.position or [0.0, 0.0, 0.0]
            src_s = layer.scale or [100.0, 100.0, 100.0]
            out_p = tf.get("position")
            out_s = tf.get("scale")
            if out_p is None or out_s is None:
                continue
            for axis in range(min(len(src_p), len(out_p))):
                if abs(src_p[axis] - out_p[axis]) > 0.001:
                    raise SpatialBoundError(
                        f"SHATTER GUARD: child layer '{layer.name}' position[{axis}] "
                        f"was mutated ({src_p[axis]} → {out_p[axis]}). "
                        f"Child transforms must pass through unchanged."
                    )
            for axis in range(min(len(src_s), len(out_s))):
                if abs(src_s[axis] - out_s[axis]) > 0.001:
                    raise SpatialBoundError(
                        f"SHATTER GUARD: child layer '{layer.name}' scale[{axis}] "
                        f"was mutated ({src_s[axis]} → {out_s[axis]}). "
                        f"Child transforms must pass through unchanged."
                    )

    return conformed_layers
