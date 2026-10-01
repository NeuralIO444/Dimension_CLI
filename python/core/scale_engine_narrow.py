# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/scale_engine_narrow.py
Sprint 4 extraction — _apply_narrow_rule_set body.

This module contains only the extracted function body.  The public API
remains on ScaleEngine in scale_engine.py — nothing else should import
from here directly.
"""

from typing import Dict

from models.conformed_manifest import (
    ConformedTransforms,
    ConformedCameraProperties,
)
from core.logger import log
from core.matrix_math import (
    conform_layer_world_space,
    has_nonuniform_parent_scale,
    has_3d_rotated_parent,
)
from core.classify import is_layer_root, should_scale_z
from core.layer_utils import canon_tag as _canon


def apply_narrow_rule_set(
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
    """Extracted body of ScaleEngine._apply_narrow_rule_set.

    See scale_engine.py docstring for the full specification.
    ``engine`` is the ScaleEngine instance (provides self.* access).
    """
    conformed_layers = []
    centroids = []
    # Reset per-run accumulators (also initialized in __init__ for safety)
    engine.collapsed_layer_warnings = []
    engine.child_layers = []       # layers that passed through (has parent)
    engine.tag_passthrough = []    # GUIDE/PROTECT layers — no transform written

    # ── Group-aware gravity clusters (M1 → unit lookup, U2 Phase 3) ─────
    # Layers sharing a tag within the same comp form a "group". Without
    # this, every member of the group snaps to the SAME pinned anchor
    # point (gravity.py's "top"/"center"/"bottom"/etc. branches), so a
    # five-line synced text reveal spread 454px apart in HD collapses
    # onto a single pixel in the conform. Each group's cluster centroid
    # lets apply_gravity() add each member's offset back on top of the
    # pinned anchor — see gravity.py::apply_gravity docstring.
    # The clusters are computed ONCE in the engine's PlacementResolution
    # (gather → single-linkage → style-proposal adjust, all in
    # core.placement_units); this pass only LOOKS THEM UP. When the
    # resolution degraded (build_units logged + warned loudly), the same
    # shared pure helper recomputes inline — never a silent fork.
    # Scene-preserve unit membership (M2 + M3 → unit lookups, U2 Phase 4):
    #   _preserving_cids — comps whose layers keep composed geometry (tag
    #     gravity bypassed for everything except FILL; LGL bottom-pin
    #     guard suppressed).
    #   _sealed_cids — nested precomps whose internals pass through
    #     untouched: the root comp's wrapper layer scales the whole
    #     precomp as one object (Babysitter also keeps nested mirror
    #     comps at source dimensions via MirrorTreeEntry.keep_source_dims).
    # Both sets come from the engine's PlacementResolution.
    _resolution = getattr(engine, "placement_resolution", None)
    if _resolution is not None:
        _layer_centroids = _resolution.layer_centroids
        _layer_cluster_sizes = _resolution.layer_cluster_sizes
        _preserving_cids = _resolution.preserving_scene_cids
        _sealed_cids = _resolution.sealed_precomp_cids
    else:
        # Degraded fallback (build_units failed loudly): recompute the
        # clusters via the same shared pure helper and rebuild the legacy
        # GLOBAL scene sets inline — never a silent behavior fork. The
        # root comp id is the first non-None containing_comp_id — Stage
        # B's walker emits layers root-first (BFS contract).
        from core.placement_units import compute_group_clusters
        _layer_centroids, _layer_cluster_sizes, _ = compute_group_clusters(
            engine.manifest, orig_w=orig_w, orig_h=orig_h,
            rule_resolver=engine._resolve_gravity_rule,
            layer_indices=layer_indices)
        _scene_active_fb = getattr(engine, "scene_preserve_active", False)
        _all_cids_fb = {getattr(_l0, "containing_comp_id", None)
                        for _l0 in engine.manifest.layers}
        _root_cid_fb = next(
            (_c for _c in (getattr(_l0, "containing_comp_id", None)
                           for _l0 in engine.manifest.layers)
             if _c is not None), None)
        _preserving_cids = set(_all_cids_fb) if _scene_active_fb else set()
        _sealed_cids = (
            (_all_cids_fb - {None, _root_cid_fb})
            if (_scene_active_fb and _root_cid_fb is not None) else set())

    for layer in engine.manifest.layers:
        # Fix 1 — per-layer comp center. Layers in nested precomps with
        # different dimensions use the nested comp's own center as origin
        # for center-remap math; root-comp layers use (src_cx, src_cy).
        _comp_id = getattr(layer, "containing_comp_id", None)
        _lsrc_cx, _lsrc_cy = engine._cx_cy_for_comp(_comp_id, src_cx, src_cy)

        kind = getattr(layer, "layer_kind", "av") or "av"
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
        # Diagnostic only (PR #121 follow-up) — set when this layer goes
        # through the group-aware gravity branch below. None means the
        # layer never reached that branch (GUIDE/PROTECT/structural/no
        # gravity rule), distinct from a meaningful group size of 1.
        _gravity_group_size = None

        # ── ROOT vs CHILD (single source: core.classify) ─────────
        parent_idx = getattr(layer, "parent_index", -1)
        is_root = is_layer_root(parent_idx, layer_indices, getattr(layer, "containing_comp_id", None))

        # ── WORLD-SPACE OVERRIDE conditions ──────────────────────
        # Normally child layers pass through (parent cascade handles S).
        # Three situations require world-space matrix decomposition:
        #
        #  1. collapseTransformations=True ("sun icon"):
        #     The pre-comp's internal space is flattened into world space.
        #     Child's position/scale operate in world space despite having
        #     a parent → we must conform world-space AND multiply scale by S.
        #     Mark as root for injection so Babysitter writes both.
        #
        #  2. Non-uniform parent scale (e.g. parent squashed to 50%×100%):
        #     Simple local-space pass-through produces drift because the
        #     parent's non-uniform distortion pushes the child's world
        #     centroid off-center after the S remap.  We center-remap in
        #     world space then invert back — scale still passes through
        #     (parent cascade handles the visual footprint correctly).
        #
        #  3. 3D-rotated parent (non-zero rotation_x / rotation_y / orientation):
        #     Local position sits in a rotated coordinate frame.  Center-
        #     remap applied locally would skew the world-space trajectory.
        #     Same world-space treatment as case 2.
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

        log.debug(
            "scale.layer.start",
            extra={
                "layer_index": layer.index,
                "layer_name": layer.name,
                "layer_kind": kind,
                "parent_index": parent_idx,
                "is_root": is_root,
                "collapse_transform": collapse,
                "use_world_space": use_world_space,
                "src_position": p,
                "src_scale": s,
                "src_rotation_z": rz,
                "src_anchor": a,
            },
        )

        # ── TAG: GUIDE / PROTECT — structural pass-through ──────────
        # Safe-zone overlays, checkers, null rigs — never move or scale.
        # Force is_root=False so Babysitter.jsx skips writes entirely.
        # v5.10.1 — `content_tag_canon` resolves legacy aliases
        # (TYPE/KEYART/LEGALS/BACKGROUND) to canonical so the branches
        # below treat both vocabularies identically.
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
                    # fall through to gravity resolver below
                else:
                    a_conformed = list(a)
                    tf = ConformedTransforms(
                        is_root=False,
                        position=list(p),
                        scale=list(s),
                        rotation=rz,
                        anchor=a_conformed,
                        rotation_x=rx if rx != 0 else None,
                        rotation_y=ry if ry != 0 else None,
                        orientation=ori,
                    )
                    layer_dict = layer.model_dump()
                    layer_dict["conformed_transforms"] = tf.model_dump()
                    conformed_layers.append(layer_dict)
                    engine.tag_passthrough.append(layer.name)
                    log.debug("scale.layer.tag_passthrough",
                              extra={"layer": layer.name, "tag": content_tag})
                    continue  # skip position math, centroids, camera/light
            else:
                # PROTECT — always pass-through regardless of source
                a_conformed = list(a)
                tf = ConformedTransforms(
                    is_root=False,
                    position=list(p),
                    scale=list(s),
                    rotation=rz,
                    anchor=a_conformed,
                    rotation_x=rx if rx != 0 else None,
                    rotation_y=ry if ry != 0 else None,
                    orientation=ori,
                )
                layer_dict = layer.model_dump()
                layer_dict["conformed_transforms"] = tf.model_dump()
                conformed_layers.append(layer_dict)
                engine.tag_passthrough.append(layer.name)
                log.debug("scale.layer.tag_passthrough",
                          extra={"layer": layer.name, "tag": content_tag})
                continue  # skip position math, centroids, camera/light

        if not is_root and not use_world_space:
            engine.child_layers.append(layer.name)
        elif parent_chain_triggered:
            log.debug(
                "scale.layer.parent_chain_ws_triggered",
                extra={
                    "layer": layer.name,
                    "nonuniform": has_nonuniform_parent_scale(layer_dict_for_ws, layers_by_index),
                    "rotated_parent": has_3d_rotated_parent(layer_dict_for_ws, layers_by_index),
                },
            )

        if use_world_space:
            # World-space matrix decomposition: compose the full parent
            # chain, apply the conform in world space, then invert back to
            # local space.  Used for both collapse and parent-chain cases.
            p_conformed = conform_layer_world_space(
                layers_by_index[lookup_key],
                layers_by_index,
                [_lsrc_cx, _lsrc_cy],
                [tgt_cx, tgt_cy],
                S,
                scale_z=scale_z,
            )
            if collapse_triggered:
                # Collapse: parent cascade no longer applies — scale by S explicitly.
                s_conformed = [
                    s[0] * S,
                    s[1] * S,
                    (s[2] * S if scale_z else s[2]) if len(s) > 2 else 100.0,
                ]
                log.debug(
                    "scale.layer.world_space.collapse",
                    extra={"layer": layer.name, "layer_index": layer.index},
                )
            else:
                # Non-uniform/rotated parent: parent cascade still applies;
                # pass scale through unchanged.
                s_conformed = list(s)
                log.debug(
                    "scale.layer.world_space.parent_chain",
                    extra={"layer": layer.name, "layer_index": layer.index},
                )
            # Mark as root for injection so Babysitter writes position (and scale
            # for the collapse case).  The shatter guard skips is_root=True layers.
            is_root = True
        elif is_root:
            # ── TAG-AWARE ROOT OVERRIDES ─────────────────────────────
            # v5.2.2 — Studio Profile gravity. When the active profile
            # has a prefix rule for this layer's name, route through
            # apply_gravity() and apply per-tag type-overrides scale.
            # Profile gravity takes precedence over the legacy
            # LEGALS/BACKGROUND special cases below — the studio's
            # opinion wins.
            if kind in ("camera", "light"):
                # Cameras and lights are purely mathematical viewpoints/illuminators.
                # They should NEVER participate in 2D layout semantics (gravity, fill, bottom-pin)
                # regardless of whether a human or AI errantly tagged them.
                gravity_rule = None
                rule_source = None
                content_tag_canon = None
            else:
                gravity_rule, rule_source = engine._resolve_gravity_rule(layer)
            # Scene-preserve layout (2026-07-02): tag gravity is bypassed
            # for everything except FILL — backgrounds must still cover the
            # frame, but content layers keep their composed geometry via
            # the standard center-remap below instead of being pinned into
            # zones. See ScaleEngine.layout for the rationale.
            # U2 Phase 4: the decision is per-comp unit membership
            # (preserving_scene_cids), not a global engine flag.
            if (gravity_rule is not None
                    and getattr(layer, "containing_comp_id", None)
                    in _preserving_cids
                    and _canon(getattr(gravity_rule, "tag", "") or "") != "FILL"):
                log.debug("scale.layer.scene_preserve_bypass",
                          extra={"layer": layer.name,
                                 "bypassed_tag": getattr(gravity_rule, "tag", None)})
                gravity_rule = None
            # Scene-preserve / nested-seal: layers INSIDE sealed precomps pass through
            # untouched — the precomp is a sealed unit whose wrapper layer
            # in the root comp carries the whole transform. Babysitter
            # keeps the nested mirror at source dimensions (see
            # MirrorTreeEntry.keep_source_dims), so source values are the
            # correct inject values. U2 Phase 4: sealed-ness is unit
            # membership (sealed_precomp_cids).
            _is_nested_scene_unit = (
                getattr(layer, "containing_comp_id", None) in _sealed_cids)
            if _is_nested_scene_unit:
                p_conformed = list(p)
                s_conformed = list(s)
                log.debug("scale.layer.scene_preserve_nested_passthrough",
                          extra={"layer": layer.name,
                                 "containing_comp_id":
                                     getattr(layer, "containing_comp_id", None)})
            elif gravity_rule is not None:
                # v5.5 Tier A — both profile-matched and baseline-
                # fallback rules go through the same call path. The
                # `rule_source` field on `gravity_applied` entries
                # tells the audit report which rule fired ("profile"
                # vs "baseline"). Type-override is a profile-only
                # concept; baseline rules use 1.0.
                from core.gravity import apply_gravity
                _layer_key = (getattr(layer, "containing_comp_id", None), layer.index)
                _centroid = _layer_centroids.get(_layer_key)
                _gravity_group_size = _layer_cluster_sizes.get(_layer_key)
                # Issue #331 opt-in gate: baseline tags (_BaselineRule)
                # default use_artwork_boundary=True, preserving HP-01's
                # already-shipped unconditional behavior for the
                # untagged/baseline path. A custom studio profile rule
                # (ProfileRule) defaults False -- a client's hand-tuned
                # placement only shifts if that profile explicitly opts
                # this rule in. See ProfileRule.use_artwork_boundary's
                # docstring for the full reasoning.
                _bounds = (getattr(layer, "artwork_bounds", None)
                           if getattr(gravity_rule, "use_artwork_boundary", False)
                           else None)
                p_conformed, s_mul = apply_gravity(
                    gravity_rule,
                    p,
                    (_lsrc_cx, _lsrc_cy),
                    (engine.target_width, engine.target_height),
                    engine._safe_area_for_gravity(),
                    base_scale=S,
                    fill_scale=fill_S,
                    scale_z=scale_z,
                    group_centroid=_centroid,
                    artwork_bounds=_bounds,
                    layer_anchor=layer.anchor,
                    layer_scale=layer.scale,
                    layer_rotation=rz,
                )
                if rule_source == "profile" and engine.profile is not None:
                    type_mul = float(engine.profile.type_overrides.get(
                        gravity_rule.tag, 1.0
                    ))
                else:
                    type_mul = 1.0
                rule_scale = float(gravity_rule.scale)
                final_mul = s_mul * type_mul * rule_scale
                s_conformed = [
                    s[0] * final_mul,
                    s[1] * final_mul,
                    (s[2] * final_mul if scale_z else s[2]) if len(s) > 2 else 100.0,
                ]
                engine.gravity_applied.append(layer.name)
                log.debug(
                    "scale.layer.gravity",
                    extra={
                        "layer": layer.name,
                        "tag": gravity_rule.tag,
                        "gravity": gravity_rule.gravity,
                        "rule_source": rule_source,   # NEW: "profile" or "baseline"
                        "rule_scale": round(rule_scale, 3),
                        "type_mul": round(type_mul, 3),
                        "final_mul": round(final_mul, 3),
                    },
                )
            elif (content_tag_canon in ("LGL", "BOTTOM")
                    and getattr(layer, "containing_comp_id", None)
                    not in _preserving_cids):
                # U2 Phase 4: the bottom-pin guard is suppressed only when
                # the layer's own comp preserves as a scene unit.
                # Bottom-pin: preserve Y distance from comp bottom edge.
                # Legal bands sit at a fixed distance from the bottom —
                # center-remapping would float them toward the middle on
                # tall formats.  X is center-remapped normally.
                # Matches both canonical "LGL" and legacy alias "LEGALS".
                y_from_bottom = orig_h - p[1]
                p_conformed = [
                    ((p[0] - _lsrc_cx) * S) + tgt_cx,
                    engine.target_height - (y_from_bottom * S),
                    (p[2] * S if scale_z else p[2]) if len(p) > 2 else 0.0,
                ]
                s_conformed = [
                    s[0] * S,
                    s[1] * S,
                    (s[2] * S if scale_z else s[2]) if len(s) > 2 else 100.0,
                ]
                log.debug("scale.layer.legals_bottom_pin",
                          extra={"layer": layer.name,
                                 "y_from_bottom": round(y_from_bottom, 2),
                                 "y_conformed": round(p_conformed[1], 2)})

            elif content_tag_canon in ("BG", "FILL"):
                # Fill override: BG/FILL plates always cover the target frame.
                # Uses fill_S (max ratio) regardless of user's scale_mode
                # so the BG never letterboxes on social formats.
                # Matches both canonical "BG" and legacy alias "BACKGROUND".
                p_conformed = [
                    ((p[0] - _lsrc_cx) * fill_S) + tgt_cx,
                    ((p[1] - _lsrc_cy) * fill_S) + tgt_cy,
                    (p[2] * fill_S if scale_z else p[2]) if len(p) > 2 else 0.0,
                ]
                s_conformed = [
                    s[0] * fill_S,
                    s[1] * fill_S,
                    (s[2] * fill_S if scale_z else s[2]) if len(s) > 2 else 100.0,
                ]
                log.debug("scale.layer.background_fill_override",
                          extra={"layer": layer.name,
                                 "fill_S": round(fill_S, 6),
                                 "mode_S": round(S, 6)})

            elif content_tag_canon == "OVERLAY":
                # FX overlay: full-frame compositing layer (grain, vignette,
                # color grade). Scales by S (content scale), NOT fill_S.
                # BG uses fill_S to guarantee full coverage; OVERLAY uses S
                # because these elements are sized to match content, not fill.
                p_conformed = [
                    ((p[0] - _lsrc_cx) * S) + tgt_cx,
                    ((p[1] - _lsrc_cy) * S) + tgt_cy,
                    (p[2] * S if scale_z else p[2]) if len(p) > 2 else 0.0,
                ]
                s_conformed = [
                    s[0] * S,
                    s[1] * S,
                    (s[2] * S if scale_z else s[2]) if len(s) > 2 else 100.0,
                ]
                log.debug("scale.layer.overlay_center_remap",
                          extra={"layer": layer.name,
                                 "S": round(S, 6)})

            else:
                # Standard center-remap: position remaps from source comp
                # space to target comp space; scale multiplies by S.
                # Z-axis scales in 3D camera scenes only.
                # Camera Z scalar: K for animated cameras (Bug L, preserves
                # dolly intent), S for static cameras (uniform, matches Scale
                # Composition). U2 Phase 6: resolved per comp via
                # engine.camera_depth_mode_for — same accessor as the
                # static intrinsics and keyframe paths (Bug L/J parity).
                _cam_depth_k = (
                    K if engine.camera_depth_mode_for(_comp_id) == "K" else S)
                z_scale = _cam_depth_k if (kind == "camera" and scale_z) else (S if scale_z else 1.0)

                # Scene Preservation Guard: If comp is a preserving 3D camera scene,
                # ensure 3D scene content scales by the Fit scalar (fit_S) so it stays
                # within the visible frame rather than blowing up when Fill mode is
                # selected. Applies to Z-position for every layer with 3D depth
                # (scale_z), not just the camera — 2026-09-02 fix: non-camera content
                # was left on the raw (pre-guard) `S`, so a preserved scene's camera
                # and its own content silently disagreed about depth (a real 87N
                # conform showed camera Z at 0.59x vs. content Z at 1.87x — a 3.16x
                # split inside one supposedly-atomic unit). The K-mode camera
                # exception is kept for documentation/safety even though
                # `camera_depth_mode_for` always returns "S" for a comp in
                # `_preserving_cids` (scene-preserve forces K->S globally), so this
                # branch is currently unreachable for cameras.
                _active_S = S
                if _comp_id in _preserving_cids and str(engine.scale_mode).lower() == "fill":
                    _active_S = (min(tgt_cx * 2.0 / orig_w, tgt_cy * 2.0 / orig_h) if (orig_w > 0 and orig_h > 0) else S) * (1.0 + engine.bleed_pct)
                    if scale_z and not (kind == "camera" and engine.camera_depth_mode_for(_comp_id) == "K"):
                        z_scale = _active_S

                p_conformed = [
                    ((p[0] - _lsrc_cx) * _active_S) + tgt_cx,
                    ((p[1] - _lsrc_cy) * _active_S) + tgt_cy,
                    (p[2] * z_scale) if len(p) > 2 else 0.0,
                ]
                s_conformed = [
                    s[0] * _active_S,
                    s[1] * _active_S,
                    (s[2] * _active_S if scale_z else s[2]) if len(s) > 2 else 100.0,
                ]
        else:
            # CHILD: pass through both position and scale.
            # Position is in parent space — center-remap would corrupt it.
            # Scale inherits S from the parent chain automatically.
            p_conformed = list(p)
            s_conformed = list(s)

        # Anchor: always pass through.  Anchors live in layer-local
        # pixel space (a point on the source content).  Scaling them
        # shifts the pivot — the original "shattered" anchor bug.
        a_conformed = list(a)

        centroids.append(p_conformed)

        # Camera/light intrinsic properties always scale by S,
        # regardless of parent status.  Zoom determines FOV (comp-
        # width-dependent), radius/falloff are world-space distances.
        cam_conformed = None
        light_conformed = None
        if kind == "camera":
            # Bug M — only the hero camera gets full intrinsics conform.
            # Secondary cameras keep source zoom/POI/focus so a multi-
            # camera comp does not apply hero math to every camera.
            if engine._is_hero_camera(layer):
                cam_conformed = engine._conform_camera(
                    layer, _lsrc_cx, _lsrc_cy, tgt_cx, tgt_cy, S, K,
                    is_3d_camera_scene=is_3d_camera_scene,
                    is_root=is_root,
                )
            elif layer.camera is not None:
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
        elif kind == "light":
            light_conformed = engine._conform_light(layer, S)

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
        layer_dict["gravity_group_size"] = _gravity_group_size
        conformed_layers.append(layer_dict)

        log.debug(
            "scale.layer.done",
            extra={
                "layer_index": layer.index,
                "layer_name": layer.name,
                "layer_kind": kind,
                "is_root": is_root,
                "out_position": p_conformed,
                "out_scale": s_conformed,
                "out_anchor": a_conformed,
                "pos_delta_x": round(p_conformed[0] - p[0], 4),
                "pos_delta_y": round(p_conformed[1] - p[1], 4),
            },
        )

    engine._check_spatial_bounds(centroids)

    # ── SHATTER GUARD ─────────────────────────────────────────────
    # Post-loop assertion: verify the root/child invariant held.
    # If any child layer got its position or scale mutated, the
    # conform will shatter — abort immediately rather than injecting
    # corrupt data into AE.
    from core.scale_engine import SpatialBoundError
    log.debug("Running SHATTER GUARD invariant check",
              extra={"child_layers": len(engine.child_layers)})
    for layer, out_dict in zip(engine.manifest.layers, conformed_layers):
        tf = out_dict.get("conformed_transforms")
        if tf is None:
            continue
        if not tf.get("is_root", True):
            # Child layer: position and scale MUST be identical to source
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
