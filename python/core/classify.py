# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/classify.py
Dimension Engine v5.0 — Layer Classification (Single Source of Truth)

The root/child invariant and 3D camera scene detection live here and
NOWHERE ELSE.  Every caller (ScaleEngine, LerpEngine, controller,
main_window, __main__) imports from this module.

ROOT / CHILD INVARIANT:
  ROOT = parent_index == -1 OR parent not present in the manifest.
  CHILD = parent exists in the manifest.
  Children NEVER get position/scale math.  Period.

3D CAMERA SCENE:
  A comp that contains at least one camera AND at least one non-camera
  3D layer.  When detected, Z positions scale by S (the Adobe
  "Scale Composition" trick) to preserve depth ratios.
"""

from typing import Set, Union


def _get_layer_attr(layer, key: str, default=None):
    """Safely extract a field from an object or dict, with fallback."""
    if isinstance(layer, dict):
        return layer.get(key, default)
    val = getattr(layer, key, None)
    if val is None:
        return default
    return val


def is_layer_root(parent_index, layer_indices: Set[Union[int, tuple]], containing_comp_id=None) -> bool:
    """Return True if the layer is a root (no parent or parent not in manifest).

    Handles edge cases:
      - parent_index is None, -1, or not an int → root
      - parent_index is a valid int but not in the manifest → root
    """
    if parent_index is None:
        return True
    if not isinstance(parent_index, (int, float)):
        return True
    idx = int(parent_index)
    if idx == -1:
        return True

    if layer_indices:
        elem = next(iter(layer_indices))
        if isinstance(elem, tuple):
            return (containing_comp_id, idx) not in layer_indices

    return idx not in layer_indices


def detect_3d_camera_scene(layers) -> bool:
    """Return True if the comp has a camera AND at least one non-camera 3D layer.

    Args:
        layers: iterable of layer objects with `layer_kind` and `threeD` attributes
                (ScrapeManifest.layers or equivalent dicts).
    """
    has_camera = False
    has_3d_layer = False

    for layer in layers:
        kind = _get_layer_attr(layer, "layer_kind", "av")
        is_3d = _get_layer_attr(layer, "threeD", False)

        if kind == "camera":
            has_camera = True
        elif is_3d:
            has_3d_layer = True

        if has_camera and has_3d_layer:
            return True  # early exit

    return has_camera and has_3d_layer


def detect_3d_camera_scenes_by_comp(layers) -> Set[Union[int, None]]:
    """Return a set of containing_comp_id for compositions that have a camera AND at least one non-camera 3D layer.

    Args:
        layers: iterable of layer objects with `layer_kind`, `threeD`, and `containing_comp_id` attributes.
    """
    from collections import defaultdict
    comp_has_camera = defaultdict(bool)
    comp_has_3d = defaultdict(bool)

    for layer in layers:
        comp_id = _get_layer_attr(layer, "containing_comp_id", None)
        kind = _get_layer_attr(layer, "layer_kind", "av")
        is_3d = _get_layer_attr(layer, "threeD", False)

        if kind == "camera":
            comp_has_camera[comp_id] = True
        elif is_3d:
            comp_has_3d[comp_id] = True

    return {
        comp_id
        for comp_id in comp_has_camera
        if comp_has_camera[comp_id] and comp_has_3d[comp_id]
    }


def should_scale_z(layer, is_3d_camera_scene: Union[bool, Set, dict]) -> bool:
    """Return True if this specific layer's Z axis must scale by S.

    Only 3D layers (or cameras) in a 3D camera scene get Z scaling.
    2D layers in the same comp keep Z untouched.
    """
    comp_id = _get_layer_attr(layer, "containing_comp_id", None)

    if isinstance(is_3d_camera_scene, set):
        active = comp_id in is_3d_camera_scene
    elif isinstance(is_3d_camera_scene, dict):
        active = is_3d_camera_scene.get(comp_id, False)
    else:
        active = bool(is_3d_camera_scene)

    if not active:
        return False

    kind = _get_layer_attr(layer, "layer_kind", "av")
    is_3d = _get_layer_attr(layer, "threeD", False)

    return bool(is_3d) or kind == "camera"


def calculate_depth_scalar_k(src_w: float, src_h: float, target_w: float, target_h: float) -> float:
    """Calculate the depth-axis scalar (K Factor) for aspect ratio adjustments.

    Formula: max(target_w / src_w, target_h / src_h) with safe division handling.
    """
    ratio_w = target_w / src_w if src_w > 0.0 else 1.0
    ratio_h = target_h / src_h if src_h > 0.0 else 1.0
    return max(ratio_w, ratio_h)


