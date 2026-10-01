# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/manifest_diff.py

Compare two manifest JSON dicts and report divergences. Skips static-
value false positives when the underlying keyframe streams and
expressions are identical (playhead-sampling noise).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


_STATIC_TO_TEMPORAL_KEYS: dict[str, tuple[str, ...]] = {
    "position": ("position", "position_x", "position_y", "position_z"),
    "scale": ("scale", "scale_x", "scale_y", "scale_z"),
    "anchor": ("anchor", "anchor_point", "anchor_x", "anchor_y", "anchor_z"),
    "rotation_x": ("rotation_x",),
    "rotation_y": ("rotation_y",),
    "rotation_z": ("rotation_z", "rotation"),
    "orientation": ("orientation",),
}


@dataclass(frozen=True)
class ManifestDivergence:
    """One reported difference between two manifests."""

    path: str
    ref_value: Any
    new_value: Any
    layer_name: Optional[str] = None
    layer_index: Optional[int] = None
    skipped_playhead_noise: bool = False


def _layer_key(layer: dict) -> str:
    uid = layer.get("uid")
    if uid:
        return f"uid:{uid}"
    return f"idx:{layer.get('index')}:{layer.get('name', '?')}"


_CONFORMED_LAYER_FIELDS = frozenset({
    "index", "name", "uid", "layer_kind",
    "conformed_transforms", "conformed_keys",
})


def _layer_view(layer: dict, *, conformed_only: bool) -> dict:
    """Return the layer slice to compare.

    Conformed-only mode skips source carryover fields (top-level
    position, camera block, etc.) that cause false positives when
    diffing REF vs conformed manifests.
    """
    if not conformed_only:
        return layer
    return {k: layer[k] for k in _CONFORMED_LAYER_FIELDS if k in layer}


def _index_layers(manifest: dict, *, conformed_only: bool = False) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for layer in manifest.get("layers", []) or []:
        if isinstance(layer, dict):
            out[_layer_key(layer)] = _layer_view(layer, conformed_only=conformed_only)
    return out


def _normalize_keyframe_stream(stream: Any) -> Any:
    if not isinstance(stream, dict):
        return stream
    keys = ("times", "values", "keyInInterpolationType", "keyOutInterpolationType")
    if not any(k in stream for k in keys):
        return stream
    return {k: stream.get(k) for k in keys if k in stream}


def _temporal_streams(layer: dict, static_field: str) -> List[Any]:
    temporal = layer.get("temporal_data") or {}
    if not isinstance(temporal, dict):
        return []
    candidates = _STATIC_TO_TEMPORAL_KEYS.get(static_field, (static_field,))
    streams = []
    for key in candidates:
        if key in temporal:
            norm = _normalize_keyframe_stream(temporal[key])
            if norm:
                streams.append(norm)
    return streams


def _has_keyframes(stream: Any) -> bool:
    if not isinstance(stream, dict):
        return False
    times = stream.get("times")
    return isinstance(times, list) and len(times) >= 1


def _expressions_for_field(layer: dict, static_field: str) -> Tuple[Optional[str], Optional[str]]:
    expressions = layer.get("expressions") or {}
    if not isinstance(expressions, dict):
        return None, None
    keys = _STATIC_TO_TEMPORAL_KEYS.get(static_field, (static_field,))
    for key in keys:
        if key in expressions:
            return expressions.get(key), key
    return expressions.get(static_field), static_field


def _is_playhead_noise(
    ref_layer: dict,
    new_layer: dict,
    static_field: str,
    ref_val: Any,
    new_val: Any,
) -> bool:
    if ref_val == new_val:
        return False
    ref_streams = _temporal_streams(ref_layer, static_field)
    new_streams = _temporal_streams(new_layer, static_field)
    if not ref_streams or not new_streams:
        return False
    if not any(_has_keyframes(s) for s in ref_streams):
        return False
    if not any(_has_keyframes(s) for s in new_streams):
        return False
    if ref_streams != new_streams:
        return False
    ref_expr, _ = _expressions_for_field(ref_layer, static_field)
    new_expr, _ = _expressions_for_field(new_layer, static_field)
    return ref_expr == new_expr


def _static_field_from_path(prefix: str) -> Optional[str]:
    for field in _STATIC_TO_TEMPORAL_KEYS:
        token = f".{field}"
        if token in prefix and (
            prefix.endswith(field)
            or prefix.endswith(f"{field}[")
            or f"{field}[" in prefix
        ):
            return field
    return None


def _record_divergence(
    divergences: List[ManifestDivergence],
    prefix: str,
    ref: Any,
    new: Any,
    *,
    ref_layer: Optional[dict] = None,
    new_layer: Optional[dict] = None,
) -> None:
    static_field = _static_field_from_path(prefix)
    if (
        static_field
        and ref_layer is not None
        and new_layer is not None
        and _is_playhead_noise(ref_layer, new_layer, static_field, ref, new)
    ):
        divergences.append(ManifestDivergence(
            prefix, ref, new,
            layer_name=ref_layer.get("name"),
            layer_index=ref_layer.get("index"),
            skipped_playhead_noise=True,
        ))
        return

    layer = ref_layer or new_layer or {}
    divergences.append(ManifestDivergence(
        prefix, ref, new,
        layer_name=layer.get("name"),
        layer_index=layer.get("index"),
    ))


def _walk_dict(
    ref: Any,
    new: Any,
    prefix: str,
    divergences: List[ManifestDivergence],
    *,
    ref_layer: Optional[dict] = None,
    new_layer: Optional[dict] = None,
) -> None:
    if ref == new:
        return
    if isinstance(ref, dict) and isinstance(new, dict):
        keys = sorted(set(ref.keys()) | set(new.keys()))
        for key in keys:
            child_prefix = f"{prefix}.{key}" if prefix else key
            rv = ref.get(key)
            nv = new.get(key)
            if (
                ref_layer is not None
                and new_layer is not None
                and key in _STATIC_TO_TEMPORAL_KEYS
                and rv != nv
                and _is_playhead_noise(ref_layer, new_layer, key, rv, nv)
            ):
                _record_divergence(
                    divergences, child_prefix, rv, nv,
                    ref_layer=ref_layer, new_layer=new_layer,
                )
                continue
            _walk_dict(
                rv, nv, child_prefix, divergences,
                ref_layer=ref_layer, new_layer=new_layer,
            )
        return
    if isinstance(ref, list) and isinstance(new, list):
        if len(ref) != len(new):
            _record_divergence(
                divergences, prefix, ref, new,
                ref_layer=ref_layer, new_layer=new_layer,
            )
            return
        if ref != new:
            static_field = _static_field_from_path(prefix)
            if (
                static_field
                and ref_layer is not None
                and new_layer is not None
                and _is_playhead_noise(
                    ref_layer, new_layer, static_field, ref, new,
                )
            ):
                _record_divergence(
                    divergences, prefix, ref, new,
                    ref_layer=ref_layer, new_layer=new_layer,
                )
                return
        for i, (rv, nv) in enumerate(zip(ref, new)):
            if rv == nv:
                continue
            _walk_dict(
                rv, nv, f"{prefix}[{i}]", divergences,
                ref_layer=ref_layer, new_layer=new_layer,
            )
        return

    _record_divergence(
        divergences, prefix, ref, new,
        ref_layer=ref_layer, new_layer=new_layer,
    )


def compare_manifest_dicts(
    ref: dict,
    new: dict,
    *,
    conformed_only: bool = False,
) -> List[ManifestDivergence]:
    """Return all divergences between two manifest dicts."""
    divergences: List[ManifestDivergence] = []

    if not conformed_only:
        ref_info = ref.get("project_info") or {}
        new_info = new.get("project_info") or {}
        _walk_dict(ref_info, new_info, "project_info", divergences)

    ref_layers = _index_layers(ref, conformed_only=conformed_only)
    new_layers = _index_layers(new, conformed_only=conformed_only)
    all_keys = sorted(set(ref_layers.keys()) | set(new_layers.keys()))
    for key in all_keys:
        ref_layer = ref_layers.get(key)
        new_layer = new_layers.get(key)
        if ref_layer is None:
            divergences.append(ManifestDivergence(
                f"layers[{key}]", None, new_layer.get("name"),
            ))
            continue
        if new_layer is None:
            divergences.append(ManifestDivergence(
                f"layers[{key}]", ref_layer.get("name"), None,
            ))
            continue
        _walk_dict(
            ref_layer, new_layer, f"layers[{key}]",
            divergences,
            ref_layer=ref_layer,
            new_layer=new_layer,
        )
    return divergences


def load_manifest(path: Path | str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compare_manifest_files(
    ref_path: Path | str,
    new_path: Path | str,
    *,
    conformed_only: bool = False,
) -> List[ManifestDivergence]:
    return compare_manifest_dicts(
        load_manifest(ref_path),
        load_manifest(new_path),
        conformed_only=conformed_only,
    )


def material_divergences(divergences: Iterable[ManifestDivergence]) -> List[ManifestDivergence]:
    """Filter out playhead-noise false positives."""
    return [d for d in divergences if not d.skipped_playhead_noise]