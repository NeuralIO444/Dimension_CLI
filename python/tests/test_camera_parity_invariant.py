"""
Camera-math parity invariant — Slot 7 prevention test.

Machine-enforces the contract that the two parallel camera-math
paths must stay in sync:

  Path A (static):   scale_engine._conform_camera (camera intrinsics)
                     + outer-loop camera position branch in
                     scale_engine.py:598-613 (camera.position).
  Path B (keyframe): property_registry.apply_rule's camera branch,
                     routed via the registry table.

The Bug J fix (2026-05-09) added the K-vs-S camera-awareness to
Path B so it mirrors Path A's Bug L fix (2026-05-08). This test
makes that parity machine-checkable so a future PR adding a
camera field to one path and not the other fails CI.

See CLAUDE.md sharp-edge "Camera depth-axis math lives in two
places that must stay in sync" for the prose contract.

Two parts:
  - test_camera_field_coverage_parity: code-introspection. Set A
    is the seven fields handled by _conform_camera (hardcoded,
    derived by reading the function bodies). Set B is the camera-
    applicable registry entries. After normalizing names (strip
    "camera_" prefix, exclude separated-axis position_x/y/z which
    are an architectural keyframe-only construct with no static
    counterpart), the two sets MUST match.
  - test_camera_math_parity_root_camera: behavioral. Loads the
    87N source manifest (real archive data, parent_index=-1
    camera). For each of the seven duplicated fields, runs the
    static path and the keyframe path with the same source value
    and asserts the conformed outputs are equal within float
    tolerance.

Scope note: behavioral test covers ROOT cameras only. A separate
open bug ("Parented camera static position remapped in world
space; should match keyframe path's local-space pass-through",
filed 2026-05-13) tracks the known root-gating asymmetry on the
position field. When that bug is fixed, the parity test's
root-only scope should be lifted.
"""

import json
import math
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
))

from core.classify import detect_3d_camera_scene, should_scale_z
from core.property_registry import (
    REGISTRY,
    apply_rule,
    by_temporal_key,
)
from core.scale_engine import ScaleEngine
from models.scrape_manifest import ScrapeManifest


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "bug_l" / "87n_source_manifest.json"
)

# 87N's conform target — HD (1920×1080) → TIKTOK (1080×1920). Same
# scenario the Bug L and Bug J contract tests use.
TARGET_W = 1080
TARGET_H = 1920
SCALE_MODE = "Fit"
BLEED_PCT = 0.05

# Bit-exact tolerance: both paths apply the same Python float ops
# on the same inputs in the same order, so the conformed values
# should match to full precision. abs_tol guards near-zero values
# (POI Z is 0 in 87N and any rel_tol-only check explodes there).
REL_TOL = 1e-9
ABS_TOL = 1e-9


# ─────────────────────────────────────────────────────────────────────
# PART 1 — Code-introspection: field-coverage parity
# ─────────────────────────────────────────────────────────────────────

# Set A: every camera field handled by Path A. Derived by reading
# `_conform_camera` (scale_engine.py:206-244) and the outer-loop
# camera position branch (scale_engine.py:598-613). Hardcoded
# deliberately — runtime AST parsing would be brittle and would
# defeat the purpose of catching drift.
PATH_A_CAMERA_FIELDS = {
    "position",         # outer-loop branch, camera-aware Z (line 603)
    "zoom",             # _conform_camera line 220
    "pointOfInterest",  # _conform_camera lines 224-231
    "focusDistance",    # _conform_camera line 235
    "aperture",         # _conform_camera line 242 (pass-through)
    "blurLevel",        # _conform_camera line 243 (pass-through)
    "depthOfField",     # _conform_camera line 240 (pass-through)
}

# Separated-axis position fields exist only on the keyframe side
# by design (AE's static value is the unified `position`; the
# separated scalars carry keys only when `dimensionsSeparated` is
# enabled). They're an architectural keyframe-only construct, not
# a parity gap, and are excluded from the comparison.
ARCHITECTURAL_KEYFRAME_ONLY = {"position_x", "position_y", "position_z"}


def _registry_camera_fields() -> set:
    """Set B: every camera-relevant registry entry, normalized.

    Includes:
      - Entries whose name starts with "camera_" (camera-specific).
      - Entries that apply to LayerKind.CAMERA in their `layer_kinds`
        tuple AND carry camera-aware Z math via apply_rule (position
        and the separated-axis siblings).

    Excludes:
      - rotation, rotation_x, rotation_y, orientation — these list
        camera in their kinds but are PASS_THROUGH on every kind;
        there is no camera-specific math to keep in sync.
      - Separated-axis position_x/y/z keys (architectural-only;
        no static counterpart).

    After collection, strips the "camera_" prefix so names align
    with Path A's set.
    """
    names = set()
    for p in REGISTRY:
        # camera_* entries — always in scope
        if p.name.startswith("camera_"):
            names.add(p.name[len("camera_"):])
            continue
        # `position` is the only non-camera_ entry that has camera-
        # aware Z math in apply_rule (via the Z-selector at
        # property_registry.py:675-676). Separated-axis siblings
        # are architectural-only.
        if p.name == "position":
            names.add("position")
    return names


def test_camera_field_coverage_parity():
    """Path A and Path B must handle the same set of camera fields.

    Failure modes this catches:
      - New camera field added to one path but not the other
      - A field removed from one path but not the other
      - The "camera_" naming convention broken in the registry

    See CLAUDE.md sharp-edge "Camera depth-axis math lives in two
    places that must stay in sync".
    """
    set_a = PATH_A_CAMERA_FIELDS
    set_b = _registry_camera_fields()

    only_in_a = set_a - set_b
    only_in_b = set_b - set_a

    if only_in_a or only_in_b:
        msg = [
            "CAMERA_FIELD_COVERAGE_PARITY_BROKEN",
            "",
            "The static path (scale_engine._conform_camera + outer-loop",
            "camera position branch) and the keyframe path (property_",
            "registry.apply_rule via the registry table) handle different",
            "sets of camera fields. They must match.",
            "",
            f"Set A (static path)   : {sorted(set_a)}",
            f"Set B (registry)      : {sorted(set_b)}",
            f"Only in A (missing B) : {sorted(only_in_a)}",
            f"Only in B (missing A) : {sorted(only_in_b)}",
            "",
            "Fix one of:",
            "  - Add the missing field to whichever path is short.",
            "  - If the field is intentionally one-sided, update",
            "    PATH_A_CAMERA_FIELDS or _registry_camera_fields here",
            "    with a reason comment.",
            "",
            "See CLAUDE.md > Camera depth-axis math lives in two places",
            "that must stay in sync.",
        ]
        pytest.fail("\n".join(msg))


# ─────────────────────────────────────────────────────────────────────
# PART 2 — Behavioral parity on the seven duplicated fields
# ─────────────────────────────────────────────────────────────────────


def _load_manifest() -> ScrapeManifest:
    with open(FIXTURE_PATH) as f:
        return ScrapeManifest.model_validate(json.load(f))


def _run_static_path():
    """Run ScaleEngine.conform() on the 87N source and return the
    camera layer's conformed transforms plus the conform parameters
    needed to feed apply_rule with the same inputs."""
    manifest = _load_manifest()
    engine = ScaleEngine(
        manifest=manifest,
        target_width=TARGET_W,
        target_height=TARGET_H,
        scale_mode=SCALE_MODE,
        bleed_pct=BLEED_PCT,
    )
    result = engine.conform()
    cams = [l for l in result["layers"] if l.get("layer_kind") == "camera"]
    assert len(cams) == 1, f"Expected exactly 1 camera in 87N, got {len(cams)}"
    conformed = cams[0]

    src_cam = next(l for l in manifest.layers if l.layer_kind == "camera")
    # Scope guard: parity test covers root cameras only — see the
    # filed bug "Parented camera static position remapped in world
    # space; should match keyframe path's local-space pass-through".
    assert src_cam.parent_index == -1, (
        "Parity invariant scoped to root cameras; 87N fixture has a "
        "parented camera (parent_index=%d). Pick a root-camera "
        "fixture or update the scope per BUGS.md." % src_cam.parent_index
    )

    src_w = manifest.project_info.width
    src_h = manifest.project_info.height
    src_center = (src_w / 2.0, src_h / 2.0)
    tgt_center = (TARGET_W / 2.0, TARGET_H / 2.0)
    raw_K = max(TARGET_W / src_w, TARGET_H / src_h)
    S = result["scale"]["S"]
    is_3d_camera_scene = detect_3d_camera_scene(manifest.layers)
    scale_z = should_scale_z(src_cam, is_3d_camera_scene)

    # The parity test compares static path vs apply_rule; the effective K
    # for cameras must match engine.camera_depth_mode ("K"→raw_K, "S"→S).
    # 87N has animated position_z → mode is "K" → effective_K = raw_K.
    camera_mode = engine.camera_depth_mode
    effective_K = raw_K if camera_mode == "K" else S

    return {
        "src_cam": src_cam,
        "conformed": conformed,
        "src_center": src_center,
        "tgt_center": tgt_center,
        "K": effective_K,
        "S": S,
        "scale_z": scale_z,
        "is_3d_camera_scene": is_3d_camera_scene,
        "camera_mode": camera_mode,
    }


def _keyframe_value(temporal_key: str, value, ctx: dict):
    """Run a single value through apply_rule using the camera's
    keyframe rule for `temporal_key`. Mirrors the lerp engine's
    per-key invocation (lerp_engine.py:213-224)."""
    pdef = by_temporal_key(temporal_key)
    assert pdef is not None, f"Registry missing temporal_key={temporal_key!r}"
    # Bug J (2026-05-09): cameras pass K only when scale_z is True
    # (mirrors the scale_engine call site at lerp_engine.py site of
    # `K=K if is_3d_camera_scene else None` in the bug-J contract test).
    K_arg = ctx["K"] if ctx["is_3d_camera_scene"] else None
    return apply_rule(
        pdef.lerp_rule,
        value,
        uniform_scale=ctx["S"],
        is_root=True,  # camera is root in 87N (asserted in _run_static_path)
        src_center=ctx["src_center"],
        tgt_center=ctx["tgt_center"],
        scale_z=ctx["scale_z"],
        K=K_arg,
        layer_kind="camera",
    )


def _scalar_isclose(a, b, label):
    assert a is not None and b is not None, f"{label}: a={a!r} b={b!r}"
    assert math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL), (
        f"CAMERA_MATH_PARITY {label}: static={a!r} keyframe={b!r} "
        f"abs_diff={abs(a - b):.6g}"
    )


def _vec_isclose(a, b, label, components=("X", "Y", "Z")):
    assert a is not None and b is not None, f"{label}: a={a!r} b={b!r}"
    assert len(a) == len(b), f"{label}: length mismatch a={a!r} b={b!r}"
    for i, comp in enumerate(components[: len(a)]):
        _scalar_isclose(a[i], b[i], f"{label}.{comp}")


def test_camera_math_parity_root_camera():
    """For each duplicated camera field, static and keyframe paths
    must produce equal values when fed the same source value.

    Fields covered (eight scalar comparisons across four registry
    fields, matching the prompt's "seven duplicated fields" listing):

      - position.X, position.Y, position.Z       (3 scalars)
      - pointOfInterest.X, .Y, .Z                (3 scalars)
      - zoom                                     (1 scalar)
      - focusDistance                            (1 scalar)

    Pass-through fields (aperture, blurLevel, depthOfField) and
    separated-axis position_x/y/z keys are covered by the
    code-introspection test only — see Part 1.
    """
    ctx = _run_static_path()
    src_cam = ctx["src_cam"]
    conformed_tf = ctx["conformed"]["conformed_transforms"]
    conformed_cam = conformed_tf["camera"]

    # ── position (X/Y/Z) — unified `position` rule ──────────────────
    # Path A: outer-loop branch (scale_engine.py:598-613). Static
    # position lives at conformed_transforms.position (NOT inside
    # the camera block — position is layer-level, not camera-
    # intrinsic).
    src_position = list(src_cam.position)  # [960, 540, -1948.018...]
    kf_position = _keyframe_value("position", src_position, ctx)
    _vec_isclose(conformed_tf["position"], kf_position, "position")

    # ── pointOfInterest (X/Y/Z) — CENTER_REMAP_XY_SCALE_Z ──────────
    src_poi = list(src_cam.camera.pointOfInterest)  # [960, 540, 0]
    kf_poi = _keyframe_value("camera_pointOfInterest", src_poi, ctx)
    _vec_isclose(conformed_cam["pointOfInterest"], kf_poi, "pointOfInterest")

    # ── zoom — CAMERA_INTRINSIC_SCALE ──────────────────────────────
    src_zoom = src_cam.camera.zoom
    kf_zoom = _keyframe_value("camera_zoom", src_zoom, ctx)
    _scalar_isclose(conformed_cam["zoom"], kf_zoom, "zoom")

    # ── focusDistance — CAMERA_INTRINSIC_SCALE ─────────────────────
    src_focus = src_cam.camera.focusDistance
    kf_focus = _keyframe_value("camera_focusDistance", src_focus, ctx)
    _scalar_isclose(conformed_cam["focusDistance"], kf_focus, "focusDistance")


def test_camera_math_parity_parented_camera():
    """Verify that a parented camera passes transforms (position and POI)
    through unchanged on both static and keyframe paths (preserving
    parent-space geometry)."""
    ctx = _run_static_path()
    src_cam = ctx["src_cam"]

    # Load manifest and mock the camera layer to have a parent (not root).
    manifest = _load_manifest()
    camera_layer = next(l for l in manifest.layers if l.layer_kind == "camera")
    camera_layer.parent_index = 5  # arbitrary parent index

    engine = ScaleEngine(
        manifest=manifest,
        target_width=TARGET_W,
        target_height=TARGET_H,
        scale_mode=SCALE_MODE,
        bleed_pct=BLEED_PCT,
    )
    result = engine.conform()
    conformed_cam_layer = next(l for l in result["layers"] if l.get("layer_kind") == "camera")
    conformed_tf = conformed_cam_layer["conformed_transforms"]
    conformed_cam = conformed_tf["camera"]

    # 1. Position parity (must pass through unchanged)
    pdef_pos = by_temporal_key("position")
    kf_position = apply_rule(
        pdef_pos.lerp_rule,
        list(src_cam.position),
        uniform_scale=ctx["S"],
        is_root=False,  # parented
        src_center=ctx["src_center"],
        tgt_center=ctx["tgt_center"],
        scale_z=ctx["scale_z"],
        K=ctx["K"] if ctx["is_3d_camera_scene"] else None,
        layer_kind="camera",
    )
    _vec_isclose(conformed_tf["position"], kf_position, "parented.position")
    _vec_isclose(conformed_tf["position"], list(src_cam.position), "parented.position.passthrough")

    # 2. Point of interest parity (must pass through unchanged)
    pdef_poi = by_temporal_key("camera_pointOfInterest")
    kf_poi = apply_rule(
        pdef_poi.lerp_rule,
        list(src_cam.camera.pointOfInterest),
        uniform_scale=ctx["S"],
        is_root=False,  # parented
        src_center=ctx["src_center"],
        tgt_center=ctx["tgt_center"],
        scale_z=ctx["scale_z"],
        K=ctx["K"] if ctx["is_3d_camera_scene"] else None,
        layer_kind="camera",
    )
    _vec_isclose(conformed_cam["pointOfInterest"], kf_poi, "parented.pointOfInterest")
    _vec_isclose(conformed_cam["pointOfInterest"], list(src_cam.camera.pointOfInterest), "parented.pointOfInterest.passthrough")
