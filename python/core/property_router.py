# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/property_router.py
Dimension Engine — v5 PropertyRecord → LerpEngine adapter

The v5 scraper emits a flat `properties` list per layer (PropertyRecord
objects) instead of the legacy hard-coded temporal_data dict.  This
module bridges those two representations so the rest of the conform
pipeline — LerpEngine.package_conformed_keys, ScaleEngine.apply_rule —
can remain unchanged.

Responsibilities:
  1. Map matchName → temporal_key using a canonicaltable (no guessing).
  2. Convert v5 KeyStream (string interp names, optional ease/tangent
     arrays) back to the legacy AE integer interp codes that Babysitter
     and ConformedKeyData expect.
  3. Surface un-mappable properties as DEBUG log entries so engineers
     can spot registry gaps without the conform silently dropping data.
  4. Handle the separated-dimensions case: when a property record has
     `separated=True`, its authoritative data lives in `per_axis` child
     records keyed by matchName (ADBE Position_0/1/2). The router
     expands those into separate temporal streams (position_x/y/z).

All output is in the legacy temporal_data dict shape:
  {
    "position": { "times": [...], "values": [...],
                  "keyInInterpolationType": [...],
                  "keyOutInterpolationType": [...] },
    "camera_zoom": { ... },
    ...
  }

This is intentionally a one-way adapter (v5 → legacy), not a
general-purpose bidirectional mapper.  The legacy format is the
single lingua franca for the conform pipeline.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ── AE keyframe interpolation type integers ────────────────────────────
# These are the stable numeric values After Effects has used since CS5.
# SovCore_Value.jsx converts them to strings; we convert back here so
# that Babysitter.jsx receives the values it expects.
_AE_INTERP = {
    "linear":  6612,
    "bezier":  6613,
    "hold":    6614,
    "unknown": 6613,   # treat unknown as bezier (safe default)
}


def _interp_to_ae_int(name: str) -> int:
    return _AE_INTERP.get(str(name).lower(), 6613)


def _convert_interp_list(names: Optional[List[str]]) -> Optional[List[int]]:
    if not names:
        return None
    return [_interp_to_ae_int(n) for n in names]


# ── matchName → temporal_key canonical table ───────────────────────────
# Only properties that the conform pipeline currently handles need to
# be listed here.  Anything outside this table is logged and skipped.
# Keep in sync with property_registry.REGISTRY (temporal_key fields).
#
# Format: { "AE_MATCH_NAME": "temporal_key" }

MATCH_NAME_TO_TEMPORAL_KEY: Dict[str, str] = {
    # Transform — position (unified, not separated)
    "ADBE Position":                    "position",
    # Transform — position separated axes
    "ADBE Position_0":                  "position_x",
    "ADBE Position_1":                  "position_y",
    "ADBE Position_2":                  "position_z",
    # Transform — scale, anchor, rotation, opacity
    "ADBE Scale":                       "scale",
    # ADBE Anchor Point is AMBIGUOUS:
    #   - On AV/text/shape/light layers it is the pivot point → "anchor" (PASS_THROUGH)
    #   - On camera layers it is the Point of Interest → "camera_pointOfInterest"
    #     (CENTER_REMAP_XY_SCALE_Z). The router handles this via the layer_kind
    #     argument; the fallback here is the AV interpretation.
    "ADBE Anchor Point":                "anchor",
    "ADBE Rotate Z":                    "rotation",
    "ADBE Opacity":                     "opacity",
    # Transform — 3D only
    "ADBE Rotate X":                    None,   # reserved (no temporal_key yet)
    "ADBE Rotate Y":                    None,
    "ADBE Orientation":                 None,
    # Camera option group
    # matchNames confirmed via AE ExtendScript DOM reference (CS6+).
    "ADBE Camera Zoom":                 "camera_zoom",
    "ADBE Camera Focus Distance":       "camera_focusDistance",
    # Camera Aperture / Blur Level are static render properties — no keyframe route.
    "ADBE Camera Aperture":             None,
    "ADBE Camera Blur Level":           None,
    "ADBE Camera DOF":                  None,
    # Light option group
    "ADBE Light Intensity":             None,   # reserved (static only for now)
    "ADBE Light Color":                 None,
    "ADBE Light Cone Angle":            None,
    "ADBE Light Cone Feather":          None,
    "ADBE Light Falloff Distance":      None,
    "ADBE Light Radius":                None,
    "ADBE Light Casts Shadows":         None,
}

# ADBE Anchor Point → camera_pointOfInterest only when layer_kind == "camera".
# Stored separately so the hot-path dict lookup stays O(1) for the common case.
_CAMERA_ANCHOR_POINT_KEY = "camera_pointOfInterest"


def _to_dict(obj: Any) -> Optional[Dict[str, Any]]:
    """
    Coerce a value to a plain dict regardless of whether it is already a
    dict or a Pydantic BaseModel.  Returns None if obj is None.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    # Pydantic v1 fallback
    if hasattr(obj, "dict"):
        return obj.dict()
    return None


def _keystream_to_legacy(keys: Any) -> Optional[Dict[str, Any]]:  # noqa: E501
    """
    Convert a v5 KeyStream (dict or Pydantic model) to the legacy temporal_data entry format.

    v5 format:
      { times, values, in_interp (strings), out_interp (strings), ... }

    Legacy format:
      { times, values, keyInInterpolationType (ints), keyOutInterpolationType (ints) }

    Returns None if the keystream has no times.
    """
    keys = _to_dict(keys)
    if not keys:
        return None
    times = keys.get("times") or []
    if not times:
        return None

    return {
        "times":                   times,
        "values":                  keys.get("values") or [],
        "keyInInterpolationType":  _convert_interp_list(keys.get("in_interp")),
        "keyOutInterpolationType": _convert_interp_list(keys.get("out_interp")),
    }


def _route_separated(record: Dict[str, Any], out: Dict[str, Any], layer_kind: str = "av") -> None:
    """
    Handle a property record that has `separated=True`.

    When Separate Dimensions is enabled, `per_axis` holds child records
    keyed by their matchName (ADBE Position_0, _1, _2).  We expand each
    axis into its own temporal stream.
    """
    per_axis = record.get("per_axis") or {}
    if hasattr(per_axis, "items"):
        items = per_axis.items()
    else:
        items = {}
    for axis_mn, axis_rec in items:
        axis_rec = _to_dict(axis_rec)
        if not axis_rec:
            continue
        tk = MATCH_NAME_TO_TEMPORAL_KEY.get(axis_mn)
        if tk is None:
            log.debug(
                "property_router.axis.unmapped",
                extra={"match_name": axis_mn, "action": "skip"},
            )
            continue
        keys = axis_rec.get("keys")
        if not keys:
            continue
        legacy = _keystream_to_legacy(keys)
        if legacy:
            out[tk] = legacy


def properties_to_temporal_data(
    properties,
    layer_kind: str = "av",
) -> Dict[str, Any]:
    """
    Convert a v5 `properties` list into a legacy temporal_data dict.

    Args:
        properties: list of PropertyRecord dicts (or Pydantic models) from a
                    v5 LayerModel.  Each record has at minimum
                    { match_name, keys, separated }.
        layer_kind: AE layer kind string ("av" | "camera" | "light" | ...).
                    Required for ADBE Anchor Point disambiguation: on camera
                    layers it is the Point of Interest (CENTER_REMAP_XY_SCALE_Z),
                    on all other layers it is the pivot anchor (PASS_THROUGH).

    Returns:
        dict shaped like the legacy `temporal_data` produced by
        Sovereign_Core.jsx's legacy scraper, ready for
        LerpEngine.package_conformed_keys().

    This function is non-destructive and does not mutate the input.
    """
    out: Dict[str, Any] = {}
    is_camera = (layer_kind == "camera")

    for raw_record in (properties or []):
        record = _to_dict(raw_record)
        if not record:
            continue

        mn = record.get("match_name") or ""

        # Separated position: expand per-axis streams.
        if record.get("separated"):
            _route_separated(record, out, layer_kind=layer_kind)
            continue

        # ADBE Anchor Point disambiguation: camera POI vs AV anchor.
        if mn == "ADBE Anchor Point" and is_camera:
            tk = _CAMERA_ANCHOR_POINT_KEY
        else:
            tk = MATCH_NAME_TO_TEMPORAL_KEY.get(mn)

        if tk is None:
            # Not in the canonical table — log at DEBUG so engineers can
            # spot gaps without cluttering prod logs.
            if mn and mn not in MATCH_NAME_TO_TEMPORAL_KEY:
                log.debug(
                    "property_router.property.unmapped",
                    extra={"match_name": mn, "layer_kind": layer_kind, "action": "skip"},
                )
            continue

        # Registered but explicitly reserved (temporal_key = None above).
        # Nothing to emit.

        keys = record.get("keys")
        if not keys:
            continue

        legacy = _keystream_to_legacy(keys)
        if legacy:
            out[tk] = legacy

    return out


def merge_legacy_and_v5(
    legacy_temporal_data,
    v5_properties,
    layer_kind: str = "av",
) -> Dict[str, Any]:
    """
    Produce the best available temporal_data dict for a layer.

    Strategy (most-specific wins):
      1. Start from the legacy temporal_data (present in all manifests).
         Accepts either a plain dict or a TemporalDataDict Pydantic model —
         the Pydantic model is coerced via model_dump(exclude_none=True).
      2. Convert v5 properties[] and overlay any keys that v5 provides.
         v5 data wins over legacy because it includes full spatial tangents,
         temporal ease, roving flags, and separation-aware axis streams.
      3. Return the merged result.

    Either argument may be None/empty — the function always returns a dict.
    """
    merged: Dict[str, Any] = {}

    # Seed from legacy (baseline — may include camera keys, etc.)
    if legacy_temporal_data is not None:
        # Coerce Pydantic TemporalDataDict → plain dict, dropping None fields
        # so we don't stomp v5 data with None values from the model defaults.
        if hasattr(legacy_temporal_data, "model_dump"):
            legacy_dict = legacy_temporal_data.model_dump(exclude_none=True)
        elif hasattr(legacy_temporal_data, "dict"):
            legacy_dict = {k: v for k, v in legacy_temporal_data.dict().items() if v is not None}
        elif isinstance(legacy_temporal_data, dict):
            legacy_dict = {k: v for k, v in legacy_temporal_data.items() if v is not None}
        else:
            legacy_dict = {}
        merged.update(legacy_dict)

    # Overlay v5 where available (richer keystream data).
    if v5_properties:
        v5 = properties_to_temporal_data(v5_properties, layer_kind=layer_kind)
        merged.update(v5)

    return merged
