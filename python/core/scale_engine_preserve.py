# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/scale_engine_preserve.py
Sprint 4 extraction — _apply_preserve_rule_set body.

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


def apply_preserve_rule_set(
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
    """Extracted body of ScaleEngine._apply_preserve_rule_set.

    See scale_engine.py docstring for the full specification.
    ``engine`` is the ScaleEngine instance (provides self.* access).
    """
    # Reset per-run accumulators so a prior call (or the engine's
    # __init__ defaults) don't leak into the report. Preserve never
    # appends to these; they end up empty.
    engine.collapsed_layer_warnings = []
    engine.child_layers = []
    engine.tag_passthrough = []

    conformed_layers = []
    skipped_count = 0

    for layer in engine.manifest.layers:
        kind = (getattr(layer, "layer_kind", "av") or "av").lower()

        p = layer.position or [0.0, 0.0, 0.0]
        s = layer.scale or [100.0, 100.0, 100.0]
        rz = layer.rotation_z or 0.0
        rx = layer.rotation_x or 0.0
        ry = layer.rotation_y or 0.0
        ori = layer.orientation
        a = layer.anchor or [0.0, 0.0, 0.0]

        # Camera intrinsics: copy verbatim from source. No K/S scale,
        # no center-remap, no POI scale. Pure pass-through.
        cam_conformed = None
        if kind == "camera" and layer.camera is not None:
            cam_src = layer.camera
            cam_conformed = ConformedCameraProperties(
                zoom=cam_src.zoom,
                pointOfInterest=(
                    list(cam_src.pointOfInterest)
                    if cam_src.pointOfInterest is not None else None
                ),
                depthOfField=cam_src.depthOfField,
                focusDistance=cam_src.focusDistance,
                aperture=cam_src.aperture,
                blurLevel=cam_src.blurLevel,
            )

        # Light intrinsics: copy verbatim from source.
        light_conformed = None
        if kind == "light" and layer.light is not None:
            lt_src = layer.light
            light_conformed = ConformedLightProperties(
                lightType=lt_src.lightType,
                intensity=lt_src.intensity,
                color=(list(lt_src.color) if lt_src.color is not None else None),
                coneAngle=lt_src.coneAngle,
                coneFeather=lt_src.coneFeather,
                falloff=lt_src.falloff,
                falloffDistance=lt_src.falloffDistance,
                radius=lt_src.radius,
                castsShadows=lt_src.castsShadows,
            )

        tf = ConformedTransforms(
            # is_root=False keeps the existing shatter-guard semantics
            # neutral; skip_inject=True is the actual zero-write signal.
            is_root=False,
            position=list(p),
            scale=list(s),
            rotation=rz,
            anchor=list(a),
            rotation_x=rx if rx != 0 else None,
            rotation_y=ry if ry != 0 else None,
            orientation=ori,
            camera=cam_conformed,
            light=light_conformed,
            skip_inject=True,
        )

        layer_dict = layer.model_dump()
        layer_dict["conformed_transforms"] = tf.model_dump()
        conformed_layers.append(layer_dict)
        skipped_count += 1

    # One INFO log per conform listing the count — per-layer logging
    # would be too verbose on large comps and produces no signal
    # beyond the count itself.
    log.info(
        "Preserve rule set — zero-write pass-through",
        extra={
            "layers_marked_skip_inject": skipped_count,
            "target": f"{int(engine.target_width)}x{int(engine.target_height)}",
        },
    )

    return conformed_layers
