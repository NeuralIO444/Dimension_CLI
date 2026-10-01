# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/occlusion/engine.py
Spatial Occlusion Engine orchestration facade.
"""

from __future__ import annotations

import copy
import os
from dataclasses import asdict
from typing import Dict, List, Optional, Tuple

from core.logger import log
from core.layer_utils import canon_tag as _canon
from core.occlusion.constants import (
    SOECorrection,
    STRUCTURAL_TAGS,
    TRANSLATABLE_TAGS,
)
from core.occlusion.mask_solver import OcclusionMask, compute_world_bounds
from core.occlusion.parent_kinematics import (
    local_position_for_world_target,
    world_transform_for,
)
from core.occlusion.repulsion_solver import resolve_inter_layer_repulsion
from core.occlusion.strategy_ladder import (
    try_anchor_shift,
    try_tighten_margin,
    try_translate,
)
from core.occlusion.unit_gate import (
    is_position_keyed,
    process_preserving_root,
    process_sealed_precomps,
    process_translatable_units,
    unit_gate,
)

# Opt-in only — see parent_kinematics.py's module docstring and
# tracking issue #329 for why this defaults OFF. Explicitly passing
# kinematic_aware=True/False to OcclusionEngine() always overrides the
# env var.
_KINEMATIC_AWARE_ENV = "DIMENSION_SOE_KINEMATIC_AWARE"


def _default_kinematic_aware() -> bool:
    return os.environ.get(_KINEMATIC_AWARE_ENV, "") == "1"


class OcclusionEngine:
    """Per-conform SOE pass. Construct once per (mask, preset) and
    call `run(conformed)` to get back `(corrected_dict, corrections)`."""

    def __init__(self, mask: OcclusionMask, preset_id: str, resolution=None,
                 kinematic_aware: Optional[bool] = None,
                 inactive_keys: Optional[set] = None):
        self.mask = mask
        self.preset_id = preset_id
        self.resolution = resolution
        # PR-V2 — `(comp_id, index)` keys of layers a `variant:` directive
        # hides on this target. They must not take part in spring
        # relaxation: an invisible layer participating pushes the VISIBLE
        # ones around to avoid colliding with nothing. Empty by default,
        # which is byte-identical to pre-PR-V2 behaviour.
        self.inactive_keys: set = set(inactive_keys or ())
        self.kinematic_aware = (
            _default_kinematic_aware() if kinematic_aware is None else bool(kinematic_aware)
        )
        self._layers_by_key: Dict[Tuple[Optional[int], int], dict] = {}

    def run(self, conformed: dict) -> Tuple[dict, List[SOECorrection]]:
        out = copy.deepcopy(conformed)
        corrections: List[SOECorrection] = []
        layers = out.get("layers") or []
        scene_warnings: List[str] = []
        handled_keys: set = set()

        # Built unconditionally but cheaply (a dict comprehension over
        # layers already in memory) — only ever read when
        # kinematic_aware is True and a layer actually has a parent, so
        # this costs nothing on the (default) unaware path.
        self._layers_by_key = {self._layer_key(l): l for l in layers}

        resolution = self.resolution
        if unit_gate(resolution):
            process_sealed_precomps(
                self.mask, layers, resolution, scene_warnings, handled_keys,
                self._layer_key, self._get_position, self._conformed_bounds,
                self._zone_label,
            )
            process_preserving_root(
                self.mask, layers, resolution, scene_warnings, handled_keys,
                self._layer_key, self._get_position, self._conformed_bounds,
                self._zone_label,
            )
            process_translatable_units(
                self.mask, layers, resolution, corrections, handled_keys,
                self._layer_key, self._get_position, self._set_position,
                self._conformed_bounds, self._record,
            )

        for layer in layers:
            if self._layer_key(layer) in handled_keys:
                continue
            correction = self._process_layer(layer)
            if correction is not None:
                corrections.append(correction)

        overflow_warnings = resolve_inter_layer_repulsion(
            self.mask, layers, corrections, handled_keys,
            self._get_position, self._set_position,
            self._conformed_bounds, self._layer_key,
            self._record, self._zone_label,
        )

        out.setdefault("warnings", {})["soe_corrections"] = len(corrections)
        if scene_warnings:
            out["warnings"]["soe_scene_preserve_warnings"] = scene_warnings
        if overflow_warnings:
            out["warnings"]["soe_overflow"] = overflow_warnings

        log.info(
            "SOE pass complete",
            extra={
                "preset": self.preset_id,
                "layers": len(layers),
                "corrections": len(corrections),
                "scene_preserve_warnings": len(scene_warnings),
            },
        )
        return out, corrections

    def _layer_key(self, layer: dict) -> Tuple[Optional[int], int]:
        return (layer.get("containing_comp_id"),
                int(layer.get("index", 0) or 0))

    def _zone_label(self, metrics: dict) -> str:
        if metrics["overlap_cutoff_px"] > 0:
            return "CUTOFF"
        return metrics["centroid_zone"]

    def _process_layer(self, layer: dict) -> Optional[SOECorrection]:
        kind = (layer.get("layer_kind") or "av").lower()
        tag = layer.get("content_tag")
        tag_canon = _canon(tag)
        original_pos = self._get_position(layer)

        # PR-V2 — hidden by a `variant:` directive on this target. Recorded
        # as an explicit skip rather than dropped silently, so the SOE
        # corrections sidecar and the report show WHY a layer no other
        # layer had to avoid was left alone.
        if self._layer_key(layer) in self.inactive_keys:
            return self._skip(layer, "SKIPPED_VARIANT_INACTIVE",
                              "hidden on this target by a variant: directive",
                              original_pos)

        if kind in ("camera", "light"):
            return self._skip(layer, "SKIPPED_STRUCTURAL", "structural layer kind", original_pos)

        if tag_canon in STRUCTURAL_TAGS:
            return self._skip(layer, "SKIPPED_STRUCTURAL", f"content_tag={tag}", original_pos)

        if layer.get("world_bounds") is None and layer.get("source_rect") is None:
            return self._skip(layer, "SKIPPED_NO_BOUNDS", "no source_rect or world_bounds", original_pos)

        if tag_canon not in TRANSLATABLE_TAGS:
            return None

        bounds = self._conformed_bounds(layer, original_pos)
        if bounds is None:
            return self._skip(layer, "SKIPPED_NO_BOUNDS", "could not compute conformed bounds", original_pos)

        metrics = self.mask.classify_obb(
            bounds, self._layer_rotation(layer), self._rotation_pivot(original_pos)
        )
        original_zone = metrics["centroid_zone"]

        if original_zone == "GO" and metrics["overlap_cutoff_px"] == 0:
            return None

        if is_position_keyed(layer):
            return self._record(
                layer, original_pos, original_pos,
                original_zone, "SKIPPED_KEYED", 0.0,
                original_zone=original_zone,
                notes="position is keyframed — manual review required",
            )

        # Strategy Ladder
        translated = try_translate(
            self.mask, layer, original_pos, bounds, metrics,
            self._conformed_bounds, self._set_position, self._record
        )
        if translated is not None:
            return translated

        if tag_canon == "BOTTOM":
            tightened = try_tighten_margin(
                self.mask, layer, original_pos,
                self._conformed_bounds, self._set_position, self._record
            )
            if tightened is not None:
                return tightened

        if tag_canon == "TOP" and self._is_manual(layer):
            shifted = try_anchor_shift(
                self.mask, layer, original_pos, bounds,
                self._conformed_bounds, self._set_position, self._record
            )
            if shifted is not None:
                return shifted

        return self._record(
            layer, original_pos, original_pos,
            original_zone, "SOE_FAILED", 0.0,
            original_zone=original_zone,
            notes="no GO placement reachable — flag for human review",
        )

    def _layer_rotation(self, layer: dict) -> float:
        # A rotated parent contributes to this layer's effective WORLD
        # rotation even though the layer's own local rotation field
        # never changes — see _world_transform's docstring.
        world = self._world_transform(layer)
        if world is not None:
            try:
                return float(world["rotation"])
            except (TypeError, ValueError):
                return 0.0
        cf = layer.get("conformed_transforms") or {}
        raw = cf.get("rotation")
        if raw is None:
            raw = layer.get("rotation_z")
        if raw is None:
            raw = layer.get("rotation")
        try:
            return float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _rotation_pivot(position: List[float]) -> Tuple[float, float]:
        return (float(position[0]), float(position[1]))

    def _is_manual(self, layer: dict) -> bool:
        src = (layer.get("content_tag_source") or "")
        return src.startswith("manual_")

    def _has_parent(self, layer: dict) -> bool:
        p_idx = layer.get("parent_index", -1)
        return p_idx is not None and p_idx > 0

    def _world_transform(self, layer: dict) -> Optional[Dict[str, object]]:
        """Returns the full world-space decomposition (position, scale,
        rotation, anchor) when kinematic_aware and layer has a parent;
        None otherwise, signaling callers to use the layer's own local
        conformed_transforms fields directly (they already are world
        space for an unparented layer)."""
        if self.kinematic_aware and self._has_parent(layer):
            return world_transform_for(layer, self._layers_by_key)
        return None

    def _get_position(self, layer: dict) -> List[float]:
        # scale_engine.py's ROOT/CHILD Anti-Shatter invariant leaves a
        # CHILD layer's conformed_transforms.position in parent-local
        # space (by design — see scale_engine.py's module docstring).
        # Treating that as world/comp space is only correct for a
        # layer with no parent. See parent_kinematics.py.
        world = self._world_transform(layer)
        if world is not None:
            return world["position"]
        cf = layer.get("conformed_transforms") or {}
        pos = cf.get("position") or layer.get("position")
        if not pos:
            return [0.0, 0.0, 0.0]
        return [float(v) for v in pos]

    def _set_position(self, layer: dict, position: List[float]) -> None:
        # `position` here is in whatever space _get_position() handed
        # the strategy ladder — world space when kinematic_aware and
        # parented, so it must be converted back to parent-local
        # before writing, or After Effects will misinterpret it as a
        # further local offset from the parent.
        if self.kinematic_aware and self._has_parent(layer):
            position = local_position_for_world_target(layer, self._layers_by_key, position)
        layer.setdefault("conformed_transforms", {})["position"] = list(position)
        layer["position"] = list(position)

    def _conformed_bounds(self, layer: dict, position: List[float],
                          override_anchor: Optional[List[float]] = None
                          ) -> Optional[Dict[str, float]]:
        # A layer's own local .scale is only its true effective (world)
        # scale when it has no parent — a parent's own scale compounds
        # into how big this layer actually renders. See
        # _world_transform's docstring.
        world = self._world_transform(layer)
        if world is not None:
            scale = list(world["scale"])
            rotation = float(world.get("rotation", 0.0))
        else:
            cf = layer.get("conformed_transforms") or {}
            scale = list(cf.get("scale") or layer.get("scale") or [100, 100, 100])
            rotation = float(cf.get("rotation_z") or layer.get("rotation_z") or 0.0)
        anchor = (override_anchor
                  or list((layer.get("conformed_transforms") or {}).get("anchor")
                          or layer.get("anchor")
                          or [0.0, 0.0, 0.0]))
        rect = layer.get("source_rect")
        if rect is None:
            return layer.get("world_bounds")
        return compute_world_bounds(position, anchor, scale, rect, rotation_deg=rotation)

    def _skip(self, layer: dict, strategy: str, notes: str,
              original_pos: List[float]) -> SOECorrection:
        return self._record(
            layer, original_pos, original_pos,
            zone="UNKNOWN", strategy=strategy, move_distance_px=0.0,
            original_zone=None, notes=notes,
        )

    def _record(self, layer: dict,
                original_pos: List[float], corrected_pos: List[float],
                zone: str, strategy: str, move_distance_px: float,
                original_zone: Optional[str],
                notes: str) -> SOECorrection:
        return SOECorrection(
            layer_index=int(layer.get("index", 0) or 0),
            layer_uid=layer.get("uid"),
            layer_name=str(layer.get("name") or ""),
            content_tag=layer.get("content_tag"),
            original_position=list(original_pos),
            corrected_position=list(corrected_pos),
            zone_hit=zone,
            strategy=strategy,
            move_distance_px=float(move_distance_px),
            original_zone_hit=original_zone,
            notes=notes,
        )


def corrections_to_jsonable(corrections: List[SOECorrection]) -> List[dict]:
    return [asdict(c) for c in corrections]
