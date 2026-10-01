# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/lerp_engine.py
Dimension Engine — Keyframe LERP Engine (Registry-Driven)

Handles keyframe interpolation and scaling for animated layer properties.
Part of the 3-layer anti-shatter invariant: ScaleEngine → LerpEngine → Babysitter.

Architecture:
  - property_registry.REGISTRY is the single source of truth for per-property
    scaling rules. This engine is now a thin orchestrator: for each keyframe
    stream, it looks up the property's lerp_rule and delegates the per-value
    math to property_registry.apply_rule().
  - The only per-property logic still living here is the dead-zone filter,
    which is a data-cleanup concern, not a scaling concern.
  - Unknown properties (not in the registry) are passed through unchanged
    and logged at DEBUG level. The engine never drops a keyframe stream —
    conservative failure is always preferred to silent loss.

ROOT / CHILD INVARIANT (unchanged — enforced by rule selection):
  - Root layers transform their position/scale/POI per the registry rule.
  - Child layers pass through — rules suffixed with ROOT_* gate themselves
    on the is_root flag, so child keyframes are never mutated.
  - Anchor/rotation/opacity always pass through regardless.

Dead-zone filter:
  Consecutive keyframe pairs where all axes differ by < DEAD_ZONE (0.001 units)
  are collapsed to a single keyframe. Strips floating-point noise.
"""

from typing import Any, Dict, List, Optional, Sequence

from models.conformed_manifest import ConformedKeyData, ConformedKeys
from core.logger import log
from core import property_registry as registry

# Sub-pixel threshold below which a keyframe delta is treated as noise
DEAD_ZONE = 0.001


def _values_equal(v0: Any, v1: Any) -> bool:
    """Return True if v0 and v1 are within DEAD_ZONE on every axis."""
    if isinstance(v0, list) and isinstance(v1, list):
        return all(abs(a - b) < DEAD_ZONE for a, b in zip(v0, v1))
    if isinstance(v0, (int, float)) and isinstance(v1, (int, float)):
        return abs(float(v0) - float(v1)) < DEAD_ZONE
    return False


def _dead_zone_filter(
    times: List[float],
    values: List[Any],
    in_types: Optional[List[int]],
    out_types: Optional[List[int]],
):
    """
    Collapse consecutive near-duplicate keyframes. Returns
    (filtered_times, filtered_values, filtered_in_types, filtered_out_types, skipped).
    Bug K fix: Preserves both the first and last keyframe of any identical sequence
    so constant holds do not collapse into a single keyframe (which causes drops).
    """
    filtered_times = [times[0]]
    filtered_values = [values[0]]
    filtered_in = [in_types[0]] if in_types else None
    filtered_out = [out_types[0]] if out_types else None
    skipped = 0

    for i in range(1, len(times)):
        if not _values_equal(values[i], filtered_values[-1]):
            # If breaking a sequence of identical values and we skipped some,
            # preserve the LAST keyframe of that sequence before the break.
            if times[i - 1] != filtered_times[-1]:
                filtered_times.append(times[i - 1])
                filtered_values.append(values[i - 1])
                if in_types and filtered_in is not None:
                    filtered_in.append(in_types[i - 1])
                if out_types and filtered_out is not None:
                    filtered_out.append(out_types[i - 1])
            
            # Append the new differing keyframe
            filtered_times.append(times[i])
            filtered_values.append(values[i])
            if in_types and filtered_in is not None:
                filtered_in.append(in_types[i])
            if out_types and filtered_out is not None:
                filtered_out.append(out_types[i])
        else:
            skipped += 1

    # End of stream: if the very last item was skipped, add it to cap the hold sequence
    if len(times) > 1 and times[-1] != filtered_times[-1]:
        filtered_times.append(times[-1])
        filtered_values.append(values[-1])
        if in_types and filtered_in is not None:
            filtered_in.append(in_types[-1])
        if out_types and filtered_out is not None:
            filtered_out.append(out_types[-1])

    return filtered_times, filtered_values, filtered_in, filtered_out, skipped


class LerpEngine:

    # ── Scalar / array LERP primitives (unchanged public API) ─────────

    @staticmethod
    def lerp(v0: float, v1: float, t: float) -> float:
        """Linear interpolation between two scalar values at fraction t."""
        return v0 + t * (v1 - v0)

    @staticmethod
    def interpolate_array(arr1: List[float], arr2: List[float], t: float) -> List[float]:
        """Element-wise LERP between two equal-length float lists."""
        return [LerpEngine.lerp(v0, v1, t) for v0, v1 in zip(arr1, arr2)]

    # ── Conformed-keyframe packaging (registry-driven) ─────────────────

    def package_conformed_keys(
        self,
        layer_temporal_data: Dict[str, Any],
        uniform_scale: float,
        is_root: bool = True,
        src_center: Optional[List[float]] = None,
        tgt_center: Optional[List[float]] = None,
        scale_z: bool = False,
        K: Optional[float] = None,
        layer_kind: str = "av",
    ) -> ConformedKeys:
        """
        Scale every keyframe stream in layer_temporal_data according to the
        per-property rule declared in property_registry.REGISTRY, then apply
        the dead-zone filter.

        Properties not found in the registry are passed through unchanged
        (no scaling) and logged — this is the conservative failure mode:
        we'd rather inject a slightly-wrong keyframe than drop it silently.

        Args:
            layer_temporal_data: dict keyed by temporal_key ("position", ...)
            uniform_scale:       final uniform S from ScaleEngine.conform()
            is_root:             True if layer has no parent in manifest
            src_center:          [cx, cy] of source comp
            tgt_center:          [cx, cy] of target comp
            scale_z:             True if Z scales by S (3D camera scene)
            K:                   camera depth-axis scalar
                                 (max(W_ratio, H_ratio), no bleed). Bug J:
                                 cameras' depth-axis keyframes use K instead
                                 of S. Falls back to S when None (e.g., for
                                 non-Bug-J callers or AV-only conforms).
            layer_kind:          "av" | "camera" | "light". Camera triggers
                                 the K branch in apply_rule for depth-axis
                                 components. Default "av" for safety.
        """
        keys_out = ConformedKeys()
        if not layer_temporal_data:
            return keys_out

        # Callers MUST supply both centers whenever the manifest contains any
        # spatial property (position, POI, separated axes).  If either is None
        # the center-remap math degrades to a pure MULTIPLY_BY_S, which shifts
        # off-center content and is visually wrong.  Catch this at the boundary
        # rather than silently producing bad keyframes.
        if src_center is None or tgt_center is None:
            raise ValueError(
                "LerpEngine.package_conformed_keys: src_center and tgt_center are required "
                "for spatial property scaling. Pass [comp_width/2, comp_height/2] for each."
            )

        src_pair = (float(src_center[0]), float(src_center[1]))
        tgt_pair = (float(tgt_center[0]), float(tgt_center[1]))

        for temporal_key, data in layer_temporal_data.items():
            if not data:
                continue

            times = data.get("times", []) or []
            values = data.get("values", []) or []
            in_types = data.get("keyInInterpolationType", None)
            out_types = data.get("keyOutInterpolationType", None)

            if not times:
                continue

            # Registry lookup. Unknown keys pass through untouched.
            pdef = registry.by_temporal_key(temporal_key)
            if pdef is None:
                log.debug(
                    "lerp.property.unregistered",
                    extra={
                        "property": temporal_key,
                        "src_keys": len(times),
                        "action": "pass_through",
                    },
                )
                rule = registry.ScaleRule.PASS_THROUGH
            else:
                rule = pdef.lerp_rule

            scaled_values = [
                registry.apply_rule(
                    rule,
                    v,
                    uniform_scale=uniform_scale,
                    is_root=is_root,
                    src_center=src_pair,
                    tgt_center=tgt_pair,
                    scale_z=scale_z,
                    K=K,
                    layer_kind=layer_kind,
                )
                for v in values
            ]

            (
                filtered_times,
                filtered_values,
                filtered_in,
                filtered_out,
                skipped,
            ) = _dead_zone_filter(times, scaled_values, in_types, out_types)

            # "scaled" tells the log whether the registry rule actually moved
            # the values for this particular (is_root, rule) combination —
            # useful for debugging child layers on root-gated rules.
            rule_applied = rule not in (
                registry.ScaleRule.PASS_THROUGH,
                registry.ScaleRule.BOOLEAN_PASS_THROUGH,
            )
            if rule in (
                registry.ScaleRule.ROOT_CENTER_REMAP_XY_SCALE_Z,
                registry.ScaleRule.ROOT_MULTIPLY_BY_S,
                registry.ScaleRule.ROOT_REMAP_AXIS_X,
                registry.ScaleRule.ROOT_REMAP_AXIS_Y,
                registry.ScaleRule.ROOT_SCALE_AXIS_Z,
            ):
                rule_applied = rule_applied and is_root

            log.debug(
                "lerp.property.filtered",
                extra={
                    "property": temporal_key,
                    "src_keys": len(times),
                    "out_keys": len(filtered_times),
                    "skipped": skipped,
                    "scaled": rule_applied,
                    "rule": rule.value,
                },
            )

            # ConformedKeys uses the temporal_key as the field name (e.g.
            # "position", "camera_zoom") — matches the registry's temporal_key.
            if hasattr(keys_out, temporal_key):
                setattr(
                    keys_out,
                    temporal_key,
                    ConformedKeyData(
                        times=filtered_times,
                        values=filtered_values,
                        keyInInterpolationType=filtered_in,
                        keyOutInterpolationType=filtered_out,
                    ),
                )
            else:
                log.warning(
                    "lerp.property.unknown_field",
                    extra={
                        "property": temporal_key,
                        "reason": "ConformedKeys has no such attribute",
                    },
                )

        return keys_out

    @classmethod
    def package_conformed_effect_keys(
        cls,
        keys_dict: Dict[str, Any],
        match_name: str,
        display_name: str,
        value_kind: Optional[str],
        uniform_scale: float,
        src_center: Sequence[float],
        tgt_center: Sequence[float],
    ) -> Optional[ConformedKeyData]:
        """
        TASK-SUB-FEAT (#274): Conform keyframe streams on effect and layer-style properties.

        Scales pixel-space distances, radiuses, and spatial points according to
        property_registry rules, while preserving normalized percentages and angles untouched.
        """
        if not keys_dict:
            return None
        times = keys_dict.get("times", []) or []
        values = keys_dict.get("values", []) or []
        in_types = keys_dict.get("keyInInterpolationType", None)
        out_types = keys_dict.get("keyOutInterpolationType", None)
        if not times or not values or len(times) != len(values):
            return None

        rule_str = registry.lookup_effect_scale_rule(match_name, display_name, value_kind)
        try:
            rule = registry.ScaleRule(rule_str)
        except ValueError:
            rule = registry.ScaleRule.PASS_THROUGH

        src_pair = (float(src_center[0]), float(src_center[1]))
        tgt_pair = (float(tgt_center[0]), float(tgt_center[1]))

        scaled_values = [
            registry.apply_rule(
                rule,
                v,
                uniform_scale=uniform_scale,
                is_root=True,
                src_center=src_pair,
                tgt_center=tgt_pair,
                scale_z=False,
            )
            for v in values
        ]

        (
            filtered_times,
            filtered_values,
            filtered_in,
            filtered_out,
            skipped,
        ) = _dead_zone_filter(times, scaled_values, in_types, out_types)

        return ConformedKeyData(
            times=filtered_times,
            values=filtered_values,
            keyInInterpolationType=filtered_in,
            keyOutInterpolationType=filtered_out,
        )


NON_NORMAL_TRANSFER_MODES = frozenset({
    "add", "screen", "multiply", "overlay", "soft_light", "hard_light",
    "color_dodge", "color_burn", "linear_dodge", "linear_burn",
    "darken", "lighten", "difference", "exclusion", "hue", "saturation",
    "color", "luminosity", "classic_color_dodge", "classic_color_burn",
    "pin_light", "hard_mix", "vivid_light", "linear_light", "subtract", "divide"
})


def is_composite_dependent_transfer_mode(mode_name: Optional[str]) -> bool:
    """
    TASK-SUB-FEAT (#274): Return True if transfer mode depends on background composite luminance.

    Layers with non-normal blending modes are sensitive to spatial nudges that move them
    over contrasting background elements, requiring constrained SOE repulsion margins.
    """
    if not mode_name:
        return False
    norm = mode_name.strip().lower().replace(" ", "_").replace("-", "_")
    return norm in NON_NORMAL_TRANSFER_MODES
