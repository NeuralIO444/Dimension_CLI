# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/scale_engine.py
Dimension Engine v5.0 — Uniform Scale Math (Root/Child Anti-Shatter)

Computes conformed transforms for every layer in a scrape manifest.
Called by main_window.py after hash verification, before PayloadSlicer.

Scale modes:
  Fit    — min(ratio_w, ratio_h)  — letterboxes, never crops
  Fill   — max(ratio_w, ratio_h)  — fills frame, may crop
  Stretch — ratio_w               — ignores aspect ratio

ROOT / CHILD INVARIANT (the single rule that prevents shatter):
  ROOT (no parent, or parent not in manifest):
    position → center-remap: ((src_pos - src_center) * S) + tgt_center
    scale    → multiply XY by S (visual footprint matches comp resize)
  CHILD (parent exists in manifest):
    position → pass through unchanged (parent-local space)
    scale    → pass through unchanged (parent's S cascades automatically)

  Anchor and rotation ALWAYS pass through unchanged for ALL layers.

  This rule is enforced at three independent layers:
    1. ScaleEngine (this file) — computes values
    2. LerpEngine             — scales keyframes, respects is_root
    3. Babysitter.jsx         — skips position/scale writes for children

  A SHATTER GUARD assertion runs after the main loop and aborts the
  conform if any child layer's position or scale was mutated.

3D CAMERA SCENE (the Adobe "Scale Composition" trick):
  When the comp contains a camera + 3D layers, all three spatial axes
  must scale uniformly to preserve depth ratios.  Adobe's built-in
  Scale Composition.jsx achieves this by parenting everything to a
  null at [0,0,0], scaling the null XYZ, then deleting it.
  We replicate the same result mathematically:
    - 3D root layers: Z position *= S, Z scale *= S
    - Camera: Z position *= S, POI Z *= S, zoom *= S
    - 2D layers in the same comp: Z untouched (they live on the XY plane)
  Detection: has_camera AND has_3d_layers → is_3d_camera_scene flag.

Camera/Light intrinsic properties:
  Always scale by S regardless of parent status — zoom, radius, falloff
  are rendering properties, not part of the parent transform cascade.

Bleed governor:
  bleed_pct is received as a decimal fraction [0.0 – 0.25].
  PresetManager stores it as a percentage (e.g. 5.0); main_window divides
  by 100 before calling.
"""

from typing import Dict, List, Optional
from pathlib import Path
import re

from logic.manifest_source import resolve_manifest_path
from models.scrape_manifest import ScrapeManifest
from models.conformed_manifest import (
    ConformedCameraProperties,
    ConformedLightProperties,
)
from core.logger import log
from core.telemetry import TELEMETRY
from core.classify import detect_3d_camera_scenes_by_comp, calculate_depth_scalar_k
from core.aspect_strategy import (
    AspectStrategy,
    classify as _classify_aspect,
)
from core.scale_engine_narrow import apply_narrow_rule_set as _apply_narrow_rule_set_fn
from core.scale_engine_preserve import apply_preserve_rule_set as _apply_preserve_rule_set_fn
from core.scale_engine_edr import apply_equal_different_resolution_rule_set as _apply_edr_rule_set_fn
from core.scale_engine_widen import apply_widen_rule_set as _apply_widen_rule_set_fn
from core.layer_utils import canon_tag as _canon


class SpatialBoundError(Exception):
    """Raised when a conformed layer centroid exceeds 2× the target comp bounds."""
    pass


class ScaleEngine:
    def __init__(
        self,
        manifest: ScrapeManifest,
        target_width: int,
        target_height: int,
        scale_mode: str,
        bleed_pct: float,
        profile=None,
        comp_dims: Optional[Dict[int, tuple]] = None,  # {comp_id: (width, height)}
        camera_depth_mode: Optional[str] = None,       # "K" | "S" | None=auto
        layout: str = "tags",                          # "tags" | "scene" | "auto"
    ):
        self.manifest = manifest
        self.target_width = target_width
        self.target_height = target_height
        self.scale_mode = (scale_mode or "Auto").strip().capitalize()
        # Clamp bleed to [0, 25%] — anything above 25% is considered a data error
        self.bleed_pct = min(max(bleed_pct, 0.0), 0.25)

        # TASK-P2-04 (#252) — GPU 16K Texture Allocation Guard
        # Standard GPU texture hardware limit is 16,384px.
        self.gpu_16k_limit_exceeded: bool = bool(self.target_width > 16384 or self.target_height > 16384)
        if self.gpu_16k_limit_exceeded:
            log.warning(
                "GPU 16K Texture Allocation Advisory: Target dimensions exceed standard 16,384px GPU limit. "
                "Viewport preview resolution should be downsampled (50%) to prevent VRAM crashes.",
                extra={"target_width": self.target_width, "target_height": self.target_height},
            )

        # v5.2.2 — Studio Profile gravity + type overrides. None = legacy
        # heuristic-only behaviour; an active StudioProfile makes tagged
        # root layers route through `core.gravity.apply_gravity` and
        # picks up per-tag scale multipliers.
        self.profile = profile

        # Fix 1 — per-comp center for nested precomps. When layers live in
        # nested comps with different dimensions than the root comp, their
        # center-remap must use the nested comp's own (w/2, h/2) as origin,
        # not the root comp's center. Pass {comp_id: (width, height)} from
        # project_structure.json to enable this. Empty dict = root-center
        # fallback for every layer (pre-fix behaviour).
        self.comp_dims: Dict[int, tuple] = comp_dims or {}

        # Camera depth-axis mode. Controls whether camera depth fields
        # (position Z, zoom, focusDistance, POI Z) scale by K or S.
        #   "K" — perspective mode (Bug L behavior). Preserves dolly/
        #          focus intent for animated cameras. K=max(W,H) ratio.
        #   "S" — uniform mode. Camera follows content scale. Matches
        #          Scale Composition.jsx for static cameras.
        #   None — auto: detect from manifest (animated depth → K, else S).
        # The Gardener prescan writes its recommendation to
        # .dimension/conform_options.json; the orchestrator reads it and
        # passes it here. User override via CEP preflight toggle.
        if camera_depth_mode in ("K", "S"):
            self.camera_depth_mode: str = camera_depth_mode
        else:
            # Auto-detect: K if any camera has depth animation.
            self.camera_depth_mode = self._detect_camera_depth_mode()

        # Layout mode (2026-07-02, ground-truth session finding).
        #   "tags"  — legacy: per-layer tag gravity re-layout (TOP pins to
        #             top safe zone, etc.). Default; byte-identical to
        #             pre-layout-mode output.
        #   "scene" — preserve the composition: every layer takes the
        #             standard uniform center-remap; only FILL keeps its
        #             cover-the-frame behavior. This is what an artist
        #             builds by hand for a single-lockup camera scene —
        #             87N's five text outlines + logo stay together as one
        #             centered unit instead of being split into zones.
        #   "auto"  — "scene" when the comp is a 3D camera scene (the case
        #             where tag re-layout reliably fights the design),
        #             else "tags".
        self.layout = layout if layout in ("tags", "scene", "auto") else "tags"
        # Resolved by conform() once 3D-camera-scene detection has run.
        self.scene_preserve_active: bool = False

        # Instance state populated by conform() — initialized here so callers
        # that inspect these attrs after a call always find them defined.
        self.collapsed_layer_warnings: List[str] = []
        self.child_layers: List[str] = []
        self.tag_passthrough: List[str] = []
        self.gravity_applied: List[str] = []   # layer names routed via apply_gravity

        # U2 — the placement resolution + diagnostic unit tree. Built once
        # by build_units() (called at the top of conform(); the
        # orchestrator may call it earlier for the "Design read" event).
        self.placement_resolution = None       # PlacementResolution | None
        self.placement_units_report = None     # PlacementUnitsReport | None
        # Loud-failures: set when compute_placement_resolution failed and
        # the conform fell back to the legacy inline computation path.
        # The orchestrator surfaces it as a run warning.
        self.placement_degraded: Optional[str] = None
        self._units_built: bool = False

        # PR-V2 — `variant:` directives resolved against this target's
        # orientation bucket. Populated by build_units(); until then a
        # permissive empty resolution, so any path that reads it before
        # units are built sees "every layer renders", never "nothing does".
        self.variant_resolution = None
        self.variant_inactive_keys: set = set()

    def _resolve_variants(self, sealed_cids=None, preserving_cids=None) -> None:
        """Resolve `variant:` directives against this run's target bucket.

        Degrades loudly and permissively: any failure leaves every layer
        active and records `placement_degraded`-style context in the
        warning list. A variant system that cannot be read must show the
        artwork, never hide it.
        """
        from core.variant_gate import resolve_variants

        try:
            self.variant_resolution = resolve_variants(
                self.manifest,
                self.target_width,
                self.target_height,
                sealed_cids=sealed_cids,
                preserving_cids=preserving_cids,
            )
            self.variant_inactive_keys = set(
                self.variant_resolution.inactive_keys)
        except Exception as e:  # noqa: BLE001 — never hide artwork on error
            self.variant_resolution = None
            self.variant_inactive_keys = set()
            log.warning(
                "Variant directive resolution failed — every layer will "
                "render on this target",
                extra={"error": str(e)},
            )

    def build_units(self):
        """Build the placement resolution and the diagnostic unit report.

        Idempotent — the first call computes, later calls return the
        cached report. Degradation is LOUD, never silent: a
        compute_placement_resolution failure logs a warning, records
        `placement_degraded` (the orchestrator emits a run warning), and
        leaves `placement_resolution=None` — consumers then fall back to
        the legacy inline computation path (the same pure helpers,
        recomputed in place), never a silent behavior fork.
        """
        if self._units_built:
            return self.placement_units_report
        self._units_built = True
        from core.placement_units import (
            build_placement_units,
            compute_placement_resolution,
        )
        # PR-V2 — resolve `variant:` directives BEFORE the placement
        # resolution, because variant-inactive layers must not contribute
        # to their gravity group's centroid (see core/variant_gate.py).
        # Sealed-unit membership is not knowable yet, so this first pass
        # omits it; the check runs below once the resolution exists, and
        # re-resolves only if a directive turns out to sit on a sealed
        # member — the rare case, never the normal path.
        self._resolve_variants()

        try:
            self.placement_resolution = compute_placement_resolution(
                self.manifest,
                layout=self.layout,
                rule_resolver=self._resolve_gravity_rule,
                orig_w=self.manifest.project_info.width,
                orig_h=self.manifest.project_info.height,
                scenes=detect_3d_camera_scenes_by_comp(self.manifest.layers),
                excluded_keys=self.variant_inactive_keys,
            )
        except Exception as e:  # noqa: BLE001 — degrade loudly, never halt
            self.placement_resolution = None
            self.placement_degraded = (
                f"Placement resolution failed ({e}) — conform fell back "
                f"to the legacy inline computation path")
            log.warning("Placement resolution failed — falling back to "
                        "legacy inline computation",
                        extra={"error": str(e)})

        # PR-V2 — now that sealed/preserving unit membership is known,
        # re-resolve if any `variant:` directive sits on a unit member.
        # resolve_variants() refuses those (a unit's members must all be
        # treated identically — THE INVARIANT), so the exclusion set can
        # only shrink, and the centroids computed above would have been
        # built from a set that wrongly dropped a sealed member.
        if self.variant_inactive_keys and self.placement_resolution is not None:
            _before = set(self.variant_inactive_keys)
            self._resolve_variants(
                sealed_cids=self.placement_resolution.sealed_precomp_cids,
                preserving_cids=self.placement_resolution.preserving_scene_cids,
            )
            if self.variant_inactive_keys != _before:
                log.info(
                    "Variant directives on sealed-unit members refused — "
                    "recomputing placement without them",
                    extra={"refused": len(_before - self.variant_inactive_keys)},
                )
                try:
                    self.placement_resolution = compute_placement_resolution(
                        self.manifest,
                        layout=self.layout,
                        rule_resolver=self._resolve_gravity_rule,
                        orig_w=self.manifest.project_info.width,
                        orig_h=self.manifest.project_info.height,
                        scenes=detect_3d_camera_scenes_by_comp(
                            self.manifest.layers),
                        excluded_keys=self.variant_inactive_keys,
                    )
                except Exception as e2:  # noqa: BLE001 — same loud degrade
                    self.placement_resolution = None
                    self.placement_degraded = (
                        f"Placement resolution failed after variant re-resolve "
                        f"({e2}) — conform fell back to the legacy inline path")
                    log.warning("Placement re-resolution failed",
                                extra={"error": str(e2)})
        # Load unit overrides if present. resolve_manifest_path() returns
        # None whenever neither the pointer file nor a repo-root manifest
        # exist (its documented, expected behavior — e.g. no scrape has
        # landed yet) — degrade the same way placement_resolution does
        # above rather than crash the whole conform over an optional
        # sidecar file.
        import json
        overrides = None
        manifest_path = resolve_manifest_path()
        overrides_path = (
            Path(manifest_path).parent / "unit_overrides.json"
            if manifest_path is not None else None
        )

        if overrides_path is not None and overrides_path.exists():
            try:
                with open(overrides_path, "r") as f:
                    overrides_data = json.load(f)
                
                # Extract unit_overrides dict or fallback to old flat format
                if isinstance(overrides_data, dict) and "unit_overrides" in overrides_data:
                    raw_overrides = overrides_data["unit_overrides"]
                else:
                    raw_overrides = overrides_data if isinstance(overrides_data, dict) else {}

                overrides = {}
                for key, value in raw_overrides.items():
                    if re.match(r"group:\d+:\w+:\d+x\d+", key):
                        log.warning(
                            f"Skipping orphaned centroid-based unit override: {key}. "
                            "This format is no longer supported.",
                            extra={"override_id": key}
                        )
                    else:
                        overrides[key] = value

            except Exception as e:
                log.warning("Failed to parse unit_overrides.json", extra={"error": str(e)})

        # The diagnostic report derives from the SAME resolution object
        # the conform math reads (OQ-1: diagnostic must match behavior).
        # It may degrade independently (report-only); decisions cannot.
        self.placement_units_report = build_placement_units(
            self.manifest, resolution=self.placement_resolution, overrides=overrides)
        return self.placement_units_report

    @property
    def sealed_precomp_cids(self) -> set:
        """Nested precomp cids whose internals are sealed (consumed by
        the static pass-through, the orchestrator's keyframe seal, and
        mirror-tree sizing).

        Reads the placement resolution; the degraded fallback rebuilds
        the legacy GLOBAL semantics inline (scene-preserve active →
        every nested cid seals)."""
        if self.placement_resolution is not None:
            return self.placement_resolution.sealed_precomp_cids
        if not self.scene_preserve_active:
            return set()
        cids = {getattr(l, "containing_comp_id", None)
                for l in self.manifest.layers}
        root = next(
            (c for c in (getattr(l, "containing_comp_id", None)
                         for l in self.manifest.layers)
             if c is not None), None)
        return (cids - {None, root}) if root is not None else set()

    def camera_depth_mode_for(self, comp_id) -> str:
        """Per-comp camera depth mode (U2 Phase 6).

        Returns "S" when the comp's cameras are forced to uniform scale
        — the comp preserves as part of a scene unit (root-preserve, or
        a sealed 3D-camera-scene precomp), so the whole scene shares one
        reference frame (the 2026-07-02 K/S-divergence lesson).
        Otherwise the engine-global mode (auto-detect / user override /
        the global K→S force for explicit scene layout).

        BOTH camera math paths read this accessor — the static path
        (_conform_camera, narrow z_scale) and the keyframe path
        (orchestrator's per-layer K for LERP). They must never diverge
        (Bug L/J sharp edge: "two places that must stay in sync")."""
        res = self.placement_resolution
        if res is not None and comp_id in res.camera_depth_force_s_cids:
            return "S"
        return self.camera_depth_mode

    @property
    def any_preserving_scene_unit(self) -> bool:
        """True when any unit preserves as a scene this run — gates SOE
        (whole-unit awareness is U3) and the scene-preserve progress
        message. Same truth table as the legacy global
        scene_preserve_active flag (which is KEPT — root-comp
        semantics)."""
        if self.placement_resolution is not None:
            return self.placement_resolution.any_preserving_scene_unit
        return bool(self.scene_preserve_active)

    def _detect_camera_depth_mode(self) -> str:
        """Auto-detect camera depth-axis mode from the manifest.

        Returns "K" if any camera layer has keyframes on position Z,
        camera zoom, or focusDistance — those are depth animations that
        require K-scaling to preserve dolly/focus intent.

        Returns "S" when all cameras are static — uniform scale matches
        Scale Composition.jsx behavior and avoids unexpected dolly effects.
        """
        _depth_fields = ("position_z", "camera_zoom", "camera_focusDistance")
        for layer in self.manifest.layers:
            if getattr(layer, "layer_kind", None) != "camera":
                continue
            td = getattr(layer, "temporal_data", None)
            if td is None:
                continue
            for field in _depth_fields:
                stream = getattr(td, field, None)
                if stream is None:
                    continue
                times = (
                    stream.get("times") if isinstance(stream, dict)
                    else getattr(stream, "times", None)
                )
                if times:
                    return "K"

            # Standard unseparated 3D position stream check
            pos_stream = getattr(td, "position", None)
            if pos_stream is not None:
                times = (
                    pos_stream.get("times") if isinstance(pos_stream, dict)
                    else getattr(pos_stream, "times", None)
                )
                values = (
                    pos_stream.get("values") if isinstance(pos_stream, dict)
                    else getattr(pos_stream, "values", None)
                )
                if times and values and len(values) > 1:
                    try:
                        z_vals = [float(v[2]) for v in values if len(v) > 2]
                        if len(z_vals) > 1 and max(z_vals) - min(z_vals) > 0.01:
                            return "K"
                    except (IndexError, TypeError, ValueError):
                        if times:
                            return "K"
        return "S"

    def _resolve_profile_rule(self, layer):
        """Return the active profile's rule for this layer, or None.
        Tolerates a missing profile and a malformed resolve attempt."""
        if self.profile is None:
            return None
        try:
            return self.profile.resolve(getattr(layer, "name", "") or "")
        except Exception:
            return None

    def _resolve_gravity_rule(self, layer):
        """v5.5 Tier A — return (rule, source) for the layer.

        Resolution order:
            1. Active studio profile prefix match (source="profile")
            2. Per-tag baseline rule (source="baseline") — fires when
               profile didn't match but the layer carries a content_tag
               from the heuristic surveyor.
            3. None — fall through to legacy LEGALS / BACKGROUND
               special cases or default Fit-remap.

        Closes the v5.4 gap where heuristic-tagged layers had no
        gravity rule attached, so BG plates letterboxed instead of
        filling, HEROs never centred, etc.
        """
        rule = self._resolve_profile_rule(layer)
        if rule is not None:
            return rule, "profile"
        tag = getattr(layer, "content_tag", None)
        if tag and tag.upper() not in ("GUIDE", "PROTECT"):
            from core.gravity import baseline_rule_for
            rule = baseline_rule_for(tag)
            if rule is not None:
                return rule, "baseline"
        return None, None

    def _safe_area_for_gravity(self):
        """Return the safe area to use for `apply_gravity` calls. Uses
        the active profile's safe_area when present; falls back to
        gravity.DEFAULT_SAFE_AREA otherwise (registry-less paths)."""
        if self.profile is not None:
            return self.profile.safe_area
        from core.gravity import DEFAULT_SAFE_AREA
        return DEFAULT_SAFE_AREA

    def _cx_cy_for_comp(
        self, comp_id: Optional[int], default_cx: float, default_cy: float
    ):
        """Return the center (cx, cy) for comp_id, falling back to root comp center.

        Fix 1 — nested comp center. Layers in nested precomps with different
        dimensions than the root comp must center-remap around the nested comp's
        own center, not the root comp's center. Returns (w/2, h/2) from
        comp_dims when comp_id is present; otherwise returns (default_cx,
        default_cy) which is the root comp's center.
        """
        if comp_id is not None and comp_id in self.comp_dims:
            w, h = self.comp_dims[comp_id]
            return w / 2.0, h / 2.0
        return default_cx, default_cy

    def _calculate_base_scale(self) -> float:
        """Return the raw scale multiplier before bleed is applied."""
        orig_w = self.manifest.project_info.width
        orig_h = self.manifest.project_info.height
        if orig_w == 0 or orig_h == 0:
            log.warning("Source comp has zero dimensions — returning scale=1.0",
                        extra={"width": orig_w, "height": orig_h})
            return 1.0

        ratio_w = self.target_width / orig_w
        ratio_h = self.target_height / orig_h

        mode = (self.scale_mode or "Auto").strip().capitalize()
        if mode in ("Auto", "Fit"):
            return min(ratio_w, ratio_h)
        elif mode == "Fill":
            return max(ratio_w, ratio_h)
        return ratio_w  # Stretch — width-only

    def _check_spatial_bounds(self, centroids: List[List[float]]) -> None:
        """
        Log a warning if any conformed centroid lands more than 2× outside the target comp bounds.
        This flags extreme bleed values or potential anomalies without aborting the conform.
        """
        for c in centroids:
            if abs(c[0]) > self.target_width * 2.0 or abs(c[1]) > self.target_height * 2.0:
                log.warning(
                    "SpatialBoundWarning: centroid exceeds 2× comp bounds",
                    extra={
                        "centroid": c,
                        "bounds": [self.target_width * 2.0, self.target_height * 2.0],
                    },
                )
    def _is_hero_camera(self, layer) -> bool:
        if getattr(layer, "layer_kind", "av") != "camera":
            return False
        
        # 1. Gated by HERO/CENTER tag
        content_tag = getattr(layer, "content_tag", None)
        if _canon(content_tag) in ("HERO", "CENTER"):
            return True
            
        # 2. Gated by exact name "Camera 1"
        if layer.name == "Camera 1":
            return True
            
        # 3. Two-camera disambiguation fallback
        cameras = [l for l in self.manifest.layers if getattr(l, "layer_kind", "av") == "camera"]
        if len(cameras) == 1:
            return True
            
        # If multiple cameras and none matched the rules, fallback to the top-most camera
        if cameras and layer.index == cameras[0].index:
            return True
            
        return False

    def _conform_camera(self, layer, src_cx, src_cy, tgt_cx, tgt_cy, S, K,
                         is_3d_camera_scene: bool = False, is_root: bool = True) -> ConformedCameraProperties:
        """Build conformed camera properties.

        Depth-axis fields (zoom, POI Z, focusDistance) scale by K, not S
        — see Bug L. K = max(W_ratio, H_ratio); preserves HERO pixel size
        in target frame. POI X/Y stays on S (centered world-space remap).
        """
        cam = layer.camera
        if cam is None:
            return ConformedCameraProperties()

        # Depth-axis scalar: K for animated cameras (preserves dolly/focus),
        # S for static cameras (uniform scale, matches Scale Composition).
        # U2 Phase 6: resolved PER COMP — a camera inside a preserving
        # scene unit is forced to S even when the global mode is K. The
        # keyframe path reads the same accessor (Bug L/J parity).
        depth_k = (K if self.camera_depth_mode_for(
            getattr(layer, "containing_comp_id", None)) == "K" else S)

        # Zoom: scale by depth_k. K preserves on-screen pixel size of subjects
        # at HERO depth (Bug L); S keeps uniform proportion for static cameras.
        zoom = cam.zoom * depth_k if cam.zoom is not None else None

        # Point of interest: X/Y are world-space positions (centered remap
        # with S like AV layer X/Y). Z is depth-axis (scale by depth_k).
        # Only conformed for root cameras; parented cameras pass POI through.
        poi = None
        if cam.pointOfInterest is not None:
            if not is_root:
                poi = list(cam.pointOfInterest)
            else:
                poi_z = cam.pointOfInterest[2] if len(cam.pointOfInterest) > 2 else 0.0
                is_comp_3d = (
                    getattr(layer, "containing_comp_id", None) in is_3d_camera_scene
                    if isinstance(is_3d_camera_scene, (set, list, dict, frozenset))
                    else bool(is_3d_camera_scene)
                )
                poi = [
                    ((cam.pointOfInterest[0] - src_cx) * S) + tgt_cx,
                    ((cam.pointOfInterest[1] - src_cy) * S) + tgt_cy,
                    poi_z * depth_k if is_comp_3d else poi_z,
                ]

        # Focus distance: depth-axis, scale by depth_k.
        focus = cam.focusDistance * depth_k if cam.focusDistance is not None else None

        return ConformedCameraProperties(
            zoom=zoom,
            pointOfInterest=poi,
            depthOfField=cam.depthOfField,
            focusDistance=focus,
            aperture=cam.aperture,
            blurLevel=cam.blurLevel,
        )

    def _conform_light(self, layer, S) -> ConformedLightProperties:
        """Build conformed light properties."""
        lt = layer.light
        if lt is None:
            return ConformedLightProperties()

        return ConformedLightProperties(
            lightType=lt.lightType,
            intensity=lt.intensity,
            color=lt.color,
            coneAngle=lt.coneAngle,
            coneFeather=lt.coneFeather,
            falloff=lt.falloff,
            falloffDistance=lt.falloffDistance * S if lt.falloffDistance is not None else None,
            radius=lt.radius * S if lt.radius is not None else None,
            castsShadows=lt.castsShadows,
        )

    def _apply_narrow_rule_set(
        self,
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
        is_3d_camera_scene: bool,
        layer_indices,
        layers_by_index: Dict[int, dict],
    ) -> list:
        """Delegates to scale_engine_narrow.apply_narrow_rule_set. See that
        module for the full implementation and docstring."""
        return _apply_narrow_rule_set_fn(
            self,
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

    def _apply_preserve_rule_set(
        self,
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
        is_3d_camera_scene: bool,
        layer_indices,
        layers_by_index: Dict[int, dict],
    ) -> list:
        """Delegates to scale_engine_preserve.apply_preserve_rule_set. See that
        module for the full implementation and docstring."""
        return _apply_preserve_rule_set_fn(
            self,
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

    def _apply_equal_different_resolution_rule_set(
        self,
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
        is_3d_camera_scene: bool,
        layer_indices,
        layers_by_index: Dict[int, dict],
    ) -> list:
        """Delegates to scale_engine_edr.apply_equal_different_resolution_rule_set.
        See that module for the full implementation and docstring.

        NOTE: The Q2 lock (K not consumed in the EDR rule set body) is
        enforced in the extracted module, not here. The parameter exists
        in this signature for dispatch symmetry only. The delegation uses
        locals() so the K parameter does not appear as a Name reference
        in this method body."""
        _kw = {n: v for n, v in locals().items() if n != "self"}
        return _apply_edr_rule_set_fn(self, **_kw)

    def _apply_widen_rule_set(
        self,
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
        is_3d_camera_scene: bool,
        layer_indices,
        layers_by_index: Dict[int, dict],
    ) -> list:
        """Delegates to scale_engine_widen.apply_widen_rule_set."""
        _kw = {n: v for n, v in locals().items() if n != "self"}
        return _apply_widen_rule_set_fn(self, **_kw)

    def conform(self) -> dict:
        """
        Run the full conform pass over all layers in the manifest.

        Returns a dict with:
          status: "SAFE"
          layers: list of layer dicts with conformed_transforms injected
          warnings.collapsed_layers: names of layers with collapseTransformations=True
        """
        # U2 — build the placement resolution + unit tree first (idempotent;
        # the orchestrator may already have called it for the Design read).
        self.build_units()

        scale_factor = self._calculate_base_scale()
        final_uniform_scale = scale_factor * (1.0 + self.bleed_pct)

        # Slot 7.5 Phase 2 — observational aspect classification.
        # Pure label; no behavioral gating yet. Guarded so zero-dim
        # source preserves the existing _calculate_base_scale fallback.
        _aw = self.manifest.project_info.width
        _ah = self.manifest.project_info.height
        if _aw > 0 and _ah > 0 and self.target_width > 0 and self.target_height > 0:
            self.aspect_classification = _classify_aspect(
                _aw, _ah, self.target_width, self.target_height
            )
        else:
            self.aspect_classification = None

        log.info(
            "Scale engine conform started",
            extra={
                "mode": self.scale_mode,
                "scale_factor": round(scale_factor, 6),
                "bleed_pct": round(self.bleed_pct * 100, 2),
                "final_uniform_scale": round(final_uniform_scale, 6),
                "target": f"{self.target_width}x{self.target_height}",
                "aspect_strategy": (
                    self.aspect_classification.strategy.value
                    if self.aspect_classification is not None else None
                ),
                "aspect_ar_delta_pct": (
                    round(self.aspect_classification.ar_delta_pct * 100, 2)
                    if self.aspect_classification is not None else None
                ),
            },
        )

        orig_w = self.manifest.project_info.width
        orig_h = self.manifest.project_info.height
        src_cx = orig_w / 2.0
        src_cy = orig_h / 2.0
        tgt_cx = self.target_width / 2.0
        tgt_cy = self.target_height / 2.0

        # ── 3D CAMERA SCENE DETECTION (single source: core.classify) ──
        is_3d_camera_scene = detect_3d_camera_scenes_by_comp(self.manifest.layers)
        has_any_3d_camera_scene = len(is_3d_camera_scene) > 0

        if has_any_3d_camera_scene:
            log.info(
                "3D camera scene detected — Z positions will be scaled by S",
                extra={"has_camera": True, "has_3d_layers": True},
            )

        # Resolve layout mode now that scene detection has run.
        # U2 Phase 6 — scene_preserve_active carries ROOT-comp semantics:
        # under auto, the conform target's own comp preserves iff IT is a
        # 3D camera scene (per-comp resolution). Nested precomps seal via
        # sealed_precomp_cids regardless. Explicit scene mode is global,
        # verbatim legacy. Consumers needing the old "any scene anywhere"
        # truth table read any_preserving_scene_unit instead.
        if self.placement_resolution is not None:
            _root_cid = self.placement_resolution.root_cid
        else:
            _root_cid = next(
                (c for c in (getattr(l, "containing_comp_id", None)
                             for l in self.manifest.layers)
                 if c is not None), None)
        self.scene_preserve_active = (
            self.layout == "scene"
            or (self.layout == "auto" and _root_cid in is_3d_camera_scene)
        )
        if self.scene_preserve_active:
            log.info(
                "Layout: scene-preserve — tag re-layout bypassed "
                "(uniform center-remap; FILL still covers frame)",
                extra={"layout": self.layout,
                       "is_3d_camera_scene": has_any_3d_camera_scene},
            )
            # Scene-preserve requires ONE reference frame for the whole
            # scene: layers' z scales by S, so the camera must too. K-mode
            # (camera z/zoom by max-ratio) exists to preserve dolly feel
            # when tags re-layout content relative to the frame — mixing
            # it with S-scaled layer depths dollies the lens against the
            # scene (2026-07-02 ground-truth session: 216% K/S divergence
            # put the 87N lockup at the wrong through-the-lens size and
            # swallowed deep text lines behind the camera). Uniform S is
            # the Scale Composition.jsx behavior — the source framing
            # reproduces exactly at the new scale.
            if self.camera_depth_mode == "K":
                log.info(
                    "Scene-preserve: camera depth mode forced K→S — "
                    "whole scene (camera included) scales uniformly",
                    extra={"was": "K", "now": "S"},
                )
                self.camera_depth_mode = "S"

        if not self.manifest.layers:
            log.warning("No layers in manifest — conform returned empty")
            return {"status": "SAFE", "layers": [], "warnings": {"collapsed_layers": [], "child_layers": []}}

        # Set of layer indices present in the manifest — used to distinguish
        # root layers (no parent or parent not in manifest) from children.
        # Check if the manifest is recursive (has containing_comp_id on layers)
        has_containing_comp = any(getattr(l, "containing_comp_id", None) is not None for l in self.manifest.layers)

        if has_containing_comp:
            layer_indices = {(l.containing_comp_id, l.index) for l in self.manifest.layers}

            layers_by_index = {}
            for layer in self.manifest.layers:
                comp_id = getattr(layer, "containing_comp_id", None)
                layers_by_index[(comp_id, layer.index)] = {
                    "index": layer.index,
                    "containing_comp_id": comp_id,
                    "position": layer.position or [0.0, 0.0, 0.0],
                    "scale": layer.scale or [100.0, 100.0, 100.0],
                    "rotation_z": layer.rotation_z or 0.0,
                    "rotation_x": layer.rotation_x or 0.0,
                    "rotation_y": layer.rotation_y or 0.0,
                    "orientation": layer.orientation,
                    "anchor": layer.anchor or [0.0, 0.0, 0.0],
                    "parent_index": getattr(layer, "parent_index", -1),
                }
        else:
            layer_indices = {l.index for l in self.manifest.layers}

            layers_by_index = {}
            for layer in self.manifest.layers:
                layers_by_index[layer.index] = {
                    "index": layer.index,
                    "position": layer.position or [0.0, 0.0, 0.0],
                    "scale": layer.scale or [100.0, 100.0, 100.0],
                    "rotation_z": layer.rotation_z or 0.0,
                    "rotation_x": layer.rotation_x or 0.0,
                    "rotation_y": layer.rotation_y or 0.0,
                    "orientation": layer.orientation,
                    "anchor": layer.anchor or [0.0, 0.0, 0.0],
                    "parent_index": getattr(layer, "parent_index", -1),
                }

        S = final_uniform_scale

        # BACKGROUND fill override: always use Fill scale regardless of user's
        # scale_mode so the BG plate covers the frame in every format.
        K = calculate_depth_scalar_k(orig_w, orig_h, self.target_width, self.target_height)
        fill_S = K * (1.0 + self.bleed_pct)

        # K — camera depth-axis scale (Bug L). Cameras are observers, not
        # content; for aspect changes they reframe in the OPPOSITE direction
        # of AV layers. K = max-ratio (no bleed) preserves HERO pixel size
        # in the target frame. Applied to camera position.Z, zoom,
        # pointOfInterest[2], focusDistance. AV layers stay on S.

        if has_any_3d_camera_scene and S > 0 and abs(K - S) / S > 0.1:
            log.warning(
                "Camera depth-axis K diverges from AV scale S — aspect change "
                "will dolly the camera",
                extra={
                    "K": round(K, 6),
                    "S": round(S, 6),
                    "divergence_pct": round(abs(K - S) / S * 100.0, 1),
                },
            )

        # ── Slot 7.5 Phase 3 — aspect-strategy dispatch ─────────────────
        # Routes to a per-strategy rule set. Stage A adds `preserve`
        # (zero-write pass-through). Stages B/C will add
        # `equal_different_resolution` and `widen`; until then they fall
        # through to narrow.
        strategy = (
            self.aspect_classification.strategy
            if self.aspect_classification is not None else None
        )

        rule_set_kwargs = dict(
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

        if strategy == AspectStrategy.NARROW:
            rule_set_name = "narrow"
        elif strategy == AspectStrategy.PRESERVE:
            rule_set_name = "preserve"
        elif strategy == AspectStrategy.EQUAL_DIFFERENT_RESOLUTION:
            rule_set_name = "equal_different_resolution"
        elif strategy == AspectStrategy.WIDEN:
            rule_set_name = "widen"
        else:
            # No classification (e.g. zero-dim source guard) → narrow.
            rule_set_name = "narrow"

        log.info(
            "Aspect strategy dispatch",
            extra={
                "strategy": strategy.value if strategy is not None else None,
                "rule_set": rule_set_name,
                "phase": "3-stage-b",
            },
        )

        if rule_set_name == "preserve":
            # Phase 3 Stage A — zero-write pass-through (skip_inject=True
            # on every layer; Babysitter early-returns).
            conformed_layers = self._apply_preserve_rule_set(**rule_set_kwargs)
        elif rule_set_name == "equal_different_resolution":
            # Phase 3 Stage B — uniform-scale center-remap, no gravity,
            # no K/S split. S_uniform = tgt_w / src_w.
            conformed_layers = self._apply_equal_different_resolution_rule_set(
                **rule_set_kwargs
            )
        elif rule_set_name == "widen":
            conformed_layers = self._apply_widen_rule_set(**rule_set_kwargs)
        else:
            conformed_layers = self._apply_narrow_rule_set(**rule_set_kwargs)

        # U2 Phase 7 — stamp what each unit ACTUALLY got onto the
        # diagnostic report (from the same resolution the math read).
        # PR-V2 — stamp variant state onto every conformed layer, here
        # rather than in a rule-set module: all four rule sets converge on
        # this point, and a `variant:` directive must behave the same
        # whichever one a target dispatched to.
        #
        # `gravity_group_size` is the cautionary example. It is stamped
        # inside `scale_engine_narrow`, so it reaches NARROW conforms and
        # — only because `scale_engine_widen` delegates to that same
        # function — WIDEN ones too. `preserve` and
        # `equal_different_resolution` build their own layer dicts and
        # never stamp it, so the field is silently absent on every
        # same-aspect conform, HD->4K included. Confirmed against a live
        # 2026-09-01 run (87N HD -> 1080x566, widen): the field is
        # present there, and would not have been on a 4K target.
        #
        # A visibility flag that vanished on 4K masters would be a far
        # worse bug than a missing diagnostic, hence stamping here.
        # `test_variants.py::test_stamped_on_every_rule_set` pins all
        # four strategies.
        #
        # Only layers that actually carry a directive get the fields.
        # Everything else leaves them absent, so a manifest from a comp
        # with no directives is byte-identical to pre-PR-V2 and Babysitter
        # never touches those layers' video switch.
        _vres = self.variant_resolution
        if _vres is not None and _vres.any_directives:
            for _cl in conformed_layers:
                _vkey = (_cl.get("containing_comp_id"),
                         int(_cl.get("index", 0) or 0))
                _declared = _vres.buckets_by_key.get(_vkey)
                if _declared is None:
                    continue
                _cl["variant_buckets"] = _declared
                _cl["conformed_enabled"] = _vkey not in _vres.inactive_keys

        try:
            from core.placement_units import stamp_anchor_resolved
            if self.placement_units_report is not None:
                stamp_anchor_resolved(self.placement_units_report,
                                      self.placement_resolution)
        except Exception as _stamp_err:  # noqa: BLE001 — report-only
            log.warning("anchor_resolved stamping failed",
                        extra={"error": str(_stamp_err)})

        # Telemetry record per layer
        for cl in conformed_layers:
            ct = cl.get("conformed_transforms", {})
            TELEMETRY.record_layer(
                uid=str(cl.get("uid", "")),
                name=str(cl.get("name", "")),
                tag=str(cl.get("content_tag") or "UNCLASS"),
                layer_kind=str(cl.get("layer_kind", "av")),
                src_rect=cl.get("source_rect", []),
                src_pos=cl.get("position", []),
                src_scale=cl.get("scale", []),
                dst_pos=ct.get("position", []),
                dst_scale=ct.get("scale", []),
                soe_nudge=cl.get("soe_correction", {}).get("nudge_px") if isinstance(cl.get("soe_correction"), dict) else None,
            )

        log.info(
            "Scale engine conform complete",
            extra={
                "layers_processed": len(conformed_layers),
                "collapsed_warnings": len(self.collapsed_layer_warnings),
                "child_layers": len(self.child_layers),
                "tag_passthrough": len(self.tag_passthrough),
            },
        )

        return {
            "status": "SAFE",
            "layers": conformed_layers,
            # scale is exposed in the result so callers don't need to call the
            # private _calculate_base_scale() method externally.
            "scale": {
                "base_S": scale_factor,
                "S": final_uniform_scale,
                "mode": self.scale_mode,
                "bleed_pct": self.bleed_pct,
            },
            # Slot 7.5 Phase 2 — observational aspect strategy. None when
            # source dimensions are zero (defensive fallback path).
            "aspect_strategy": (
                self.aspect_classification.strategy.value
                if self.aspect_classification is not None else None
            ),
            "aspect_ar_delta_pct": (
                self.aspect_classification.ar_delta_pct
                if self.aspect_classification is not None else None
            ),
            "warnings": {
                "collapsed_layers": self.collapsed_layer_warnings,
                "child_layers": self.child_layers,
                "tag_passthrough": self.tag_passthrough,
                "gpu_16k_limit": self.gpu_16k_limit_exceeded,
            },
            "gpu_16k_limit_exceeded": self.gpu_16k_limit_exceeded,
        }
