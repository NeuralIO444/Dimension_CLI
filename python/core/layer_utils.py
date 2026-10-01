# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/layer_utils.py

Shared pure helpers for layer classification, tagging, and grouping used across
analysis stages (1-4). Extracted to eliminate duplication in pairing.py,
style_extraction.py, style_proposals.py, surveyor.py, etc.

Design:
- Pure functions, no I/O, no side effects.
- Comp-scoped identity respected (use containing_comp_id + index where relevant).
- Kept minimal to avoid pulling in heavy deps (e.g. no ScaleEngine).
- All consumers should import from here; old local copies are being phased out.

See CLAUDE.md "sharp edges" for history of why duplication existed (avoiding
conform-pipeline import cycles) and why we now centralize helpers.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.tag_registry import REGISTRY as _TAG_REGISTRY

def canon_tag(tag: Optional[str]) -> Optional[str]:
    """Resolve a manifest tag to its canonical id (or pass through if unknown).

    Used by surveyor, pairing, style extraction/proposals, etc. Mirrors patterns
    in scale_engine and occlusion_engine for consistency.
    """
    if not tag:
        return tag
    return _TAG_REGISTRY.normalize(tag) or tag


def is_structural(layer: Any) -> bool:
    """Exclude structural layers (camera, light, null, guide, protect) from
    content analysis, pairing, style signals, etc.

    Checks layer_kind, flags, and canonical tag. Must stay in sync across
    consumers — now enforced by this single implementation.
    """
    layer_kind = (getattr(layer, "layer_kind", None) or "av").lower()
    if layer_kind in ("camera", "light"):
        return True
    flags = getattr(layer, "flags", None)
    if flags is not None:
        if getattr(flags, "null_layer", False):
            return True
        if getattr(flags, "guide", False):
            return True
    canon = canon_tag(getattr(layer, "content_tag", None))
    if canon in ("GUIDE", "PROTECT", "NULL"):
        return True
    return False


def world_bounds_center(wb: Dict[str, float]) -> Tuple[float, float]:
    """Center of world_bounds rect (l,r,t,b)."""
    if not wb or "l" not in wb:
        return 0.0, 0.0
    cx = (wb["l"] + wb["r"]) / 2.0
    cy = (wb["t"] + wb["b"]) / 2.0
    return cx, cy


LayerKey = Tuple[Optional[int], int]  # (containing_comp_id, index)


def layer_key(layer: Any) -> LayerKey:
    """Comp-scoped layer identity key. Use everywhere instead of flat index."""
    comp_id = getattr(layer, "containing_comp_id", None)
    idx = getattr(layer, "index", 0) or 0
    return (comp_id, idx)


def group_centroids_from_world_bounds(
    manifest: Any,
) -> Dict[Tuple[Optional[int], str], Tuple[float, float]]:
    """Same-tag group centroids computed from world_bounds centres.

    Used by style_proposals.py to compare current layout against style
    signals — which were themselves extracted from world_bounds in
    style_extraction.py. Using position would produce a different origin
    and make the delta comparison meaningless.

    Layers without world_bounds are skipped (they can't contribute a
    meaningful spatial signal).
    """
    _group_positions: Dict[Tuple[Optional[int], str], list] = {}
    layers = getattr(manifest, "layers", []) or []
    for layer in layers:
        tag = canon_tag(getattr(layer, "content_tag", None))
        if not tag or tag in ("GUIDE", "PROTECT"):
            continue
        if is_structural(layer):
            continue
        wb = getattr(layer, "world_bounds", None)
        if not wb or "l" not in wb:
            continue
        comp_id = getattr(layer, "containing_comp_id", None)
        cx, cy = world_bounds_center(wb)
        _group_positions.setdefault((comp_id, tag), []).append((cx, cy))

    centroids: Dict[Tuple[Optional[int], str], Tuple[float, float]] = {}
    for key, positions in _group_positions.items():
        n = len(positions)
        if n == 0:
            continue
        centroids[key] = (
            sum(p[0] for p in positions) / n,
            sum(p[1] for p in positions) / n,
        )
    return centroids


def spatial_group_key(layer: Any, bin_size: float = 200.0) -> Tuple[Optional[int], str, int]:
    """Spatial + tag key for grouping. Bins y to avoid lumping distant same-tag layers (e.g. cyan vs logo)."""
    comp_id = getattr(layer, "containing_comp_id", None)
    tag = canon_tag(getattr(layer, "content_tag", None)) or "NONE"
    pos = getattr(layer, "position", None) or [0.0, 0.0]
    y_bin = int((pos[1] if len(pos) > 1 else 0) / bin_size)
    return (comp_id, tag, y_bin)


def group_centroids_from_manifest(
    manifest: Any,
) -> Dict[Tuple[Optional[int], str], Tuple[float, float]]:
    """Compute same-tag group centroids from layers in a manifest.

    Pure helper, extracted from scale_engine_narrow / style_proposals / recon
    to reduce duplication. Uses (comp_id, canon_tag) key.

    Returns dict of centroid (cx, cy). Skips structural / non-root layers.
    """
    from core.classify import is_layer_root  # local to avoid broad imports at top

    _group_positions: Dict[Tuple[Optional[int], str], list] = {}
    layers = getattr(manifest, "layers", []) or []
    layer_indices = {getattr(l, "index", -1): l for l in layers}  # for root check if needed

    for layer in layers:
        tag = canon_tag(getattr(layer, "content_tag", None))
        if not tag or tag in ("GUIDE", "PROTECT"):
            continue
        if is_structural(layer):
            continue
        parent_idx = getattr(layer, "parent_index", -1)
        comp_id = getattr(layer, "containing_comp_id", None)
        if not is_layer_root(parent_idx, layer_indices, comp_id):
            continue
        pos = getattr(layer, "position", None) or [0.0, 0.0, 0.0]
        _group_positions.setdefault((comp_id, tag), []).append((pos[0], pos[1]))

    centroids: Dict[Tuple[Optional[int], str], Tuple[float, float]] = {}
    for key, positions in _group_positions.items():
        n = len(positions)
        if n == 0:
            continue
        cx = sum(p[0] for p in positions) / n
        cy = sum(p[1] for p in positions) / n
        centroids[key] = (cx, cy)
    return centroids
