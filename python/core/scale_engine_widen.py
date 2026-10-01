# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/scale_engine_widen.py
Next-Gen Relayout Architecture — WIDEN Strategy Rule Set (PR 3).

Applies bi-directional relayout transforms when target aspect ratio is wider
than source aspect ratio (e.g. 9:16 -> 16:9, 1:1 -> 16:9, 16:9 -> 21:9 / 32:9).

Key Invariants:
  - Byte-identical parity with the goldens on valid standard layouts.
  - Safe-zone aware gravity positioning across horizontal expansions.
  - Camera depth K-scaling and scene unit preservation.
  - Center-World Fail-Safe Guardrail: on non-finite coordinates or singular
    decompositions, falls back to uniform center world remap ([tgt_cx, tgt_cy]).
"""

from __future__ import annotations

import math
from typing import Dict, Optional

from core.logger import log
from core.scale_engine_narrow import apply_narrow_rule_set


def apply_widen_rule_set(
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
    is_3d_camera_scene: Dict[Optional[int], bool],
    layer_indices,
    layers_by_index: Dict[int, dict],
) -> list:
    """Execute the WIDEN rule set with Center-World fail-safe guardrail."""
    try:
        conformed_layers = apply_narrow_rule_set(
            engine,
            orig_w=orig_w,
            orig_h=orig_h,
            src_cx=src_cx,
            src_cy=src_cy,
            tgt_cx=tgt_cx,
            tgt_cy=tgt_cy,
            S=S,
            fill_S=fill_S,
            K=K,
            is_3d_camera_scene=is_3d_camera_scene,
            layer_indices=layer_indices,
            layers_by_index=layers_by_index,
        )
    except Exception as exc:
        log.warning(
            "Center-World Guardrail Triggered: widen conform encountered critical error",
            extra={"error": str(exc)},
        )
        conformed_layers = []
        for layer in engine.manifest.layers:
            layer_dict = layer.model_dump()
            layer_dict["conformed_transforms"] = {
                "is_root": True,
                "position": [tgt_cx, tgt_cy, 0.0],
                "scale": [S * 100.0, S * 100.0, 100.0],
                "rotation": 0.0,
                "anchor": layer.anchor or [0.0, 0.0, 0.0],
                "rotation_x": None,
                "rotation_y": None,
                "orientation": None,
                "camera": None,
                "light": None,
                "skip_inject": False,
            }
            conformed_layers.append(layer_dict)
        return conformed_layers

    # Post-process Center-World fail-safe validation on individual layers
    for out_dict in conformed_layers:
        tf = out_dict.get("conformed_transforms")
        if not tf or out_dict.get("skip_inject"):
            continue
        pos = tf.get("position")
        if pos and (not all(math.isfinite(x) for x in pos)):
            log.warning(
                "Center-World Guardrail Triggered: non-finite layer coordinates detected",
                extra={"layer": out_dict.get("name"), "pos": pos},
            )
            tf["position"] = [tgt_cx, tgt_cy, 0.0]
            tf["scale"] = [S * 100.0, S * 100.0, 100.0]

    return conformed_layers
