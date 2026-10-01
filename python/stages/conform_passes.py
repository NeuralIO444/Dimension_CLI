# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Conform sub-passes extracted from stages/conform.py.

Each function mutates `result` in place (LERP, SOE) or returns mirror specs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from core.classify import (
    calculate_depth_scalar_k,
    detect_3d_camera_scenes_by_comp,
    is_layer_root,
    should_scale_z,
)
from core.lerp_engine import LerpEngine
from core.logger import log
from core.property_router import merge_legacy_and_v5
from core.scale_engine import ScaleEngine
from logic.preset_manager import PresetManager
from models.scrape_manifest import ScrapeManifest


@dataclass
class MirrorTreeSpec:
    mirror_tree: list
    mirror_rewires: list
    target_bin_path: str


def apply_lerp_pass(
    manifest: ScrapeManifest,
    result: dict[str, Any],
    engine: ScaleEngine,
    *,
    target_w: int,
    target_h: int,
    comp_dims: dict[int, tuple[int, int]],
) -> int:
    """Attach conformed_keys to animated layers.  Returns animated layer count."""
    lerp = LerpEngine()
    s_factor = result["scale"]["S"]
    has_containing_comp = any(
        getattr(layer, "containing_comp_id", None) is not None
        for layer in manifest.layers
    )
    sealed_cids: set = engine.sealed_precomp_cids
    if sealed_cids:
        log.info(
            "Scene-preserve: sealed nested comps — keyframe/"
            "effect conform skipped for their layers",
            extra={"sealed_comp_count": len(sealed_cids)},
        )
    if has_containing_comp:
        layer_indices = {(l.containing_comp_id, l.index) for l in manifest.layers}
    else:
        layer_indices = {l.index for l in manifest.layers}
    is_3d_camera_scenes = detect_3d_camera_scenes_by_comp(manifest.layers)

    src_center = [manifest.project_info.width / 2.0, manifest.project_info.height / 2.0]
    tgt_center = [target_w / 2.0, target_h / 2.0]
    k_factor = calculate_depth_scalar_k(
        manifest.project_info.width,
        manifest.project_info.height,
        target_w,
        target_h,
    )

    animated = 0
    for i, layer_dict in enumerate(result["layers"]):
        src = manifest.layers[i] if i < len(manifest.layers) else None
        layer_kind = getattr(src, "layer_kind", "av") if src else "av"
        if (
            src is not None
            and layer_kind not in ("camera", "light")
            and getattr(src, "containing_comp_id", None) in sealed_cids
        ):
            layer_dict["conformed_keys"] = None
            continue

        td = None
        if src:
            legacy_td = (
                src.temporal_data.model_dump(exclude_none=True)
                if src.temporal_data
                else {}
            )
            v5_props = getattr(src, "properties", None) or []
            td = merge_legacy_and_v5(legacy_td, v5_props, layer_kind=layer_kind) or None

        if td:
            parent_idx = getattr(src, "parent_index", -1)
            comp_id = getattr(src, "containing_comp_id", None)
            ct = layer_dict.get("conformed_transforms") or {}
            effective_is_root = ct.get(
                "is_root", is_layer_root(parent_idx, layer_indices, comp_id)
            )
            is_comp_3d_scene = (
                comp_id in is_3d_camera_scenes
                if has_containing_comp
                else (len(is_3d_camera_scenes) > 0)
            )
            if is_comp_3d_scene:
                k_for_keys = (
                    k_factor
                    if engine.camera_depth_mode_for(comp_id) == "K"
                    else s_factor
                )
            else:
                k_for_keys = None

            lerp_comp_id = getattr(src, "containing_comp_id", None)
            if comp_dims and lerp_comp_id in comp_dims:
                cw, ch = comp_dims[lerp_comp_id]
                layer_src_center = [cw / 2.0, ch / 2.0]
            else:
                layer_src_center = src_center

            ck = lerp.package_conformed_keys(
                td,
                s_factor,
                is_root=effective_is_root,
                src_center=layer_src_center,
                tgt_center=tgt_center,
                scale_z=should_scale_z(src, is_3d_camera_scenes),
                K=k_for_keys,
                layer_kind=layer_kind,
            )
            layer_dict["conformed_keys"] = ck.model_dump(exclude_none=True)
            animated += 1
        else:
            layer_dict["conformed_keys"] = None

    log.info("LERP pass complete", extra={"animated_layers": animated})
    return animated


def apply_effect_stroke_clamp_pass(
    manifest: ScrapeManifest,
    result: dict[str, Any],
) -> int:
    """#391 -- attach conformed_layer_styles to BOTTOM/LEGALS layers whose
    Stroke layer style would otherwise drop below visibility after this
    conform's scale. Returns the number of layers the clamp intervened for.

    Deliberately narrow: never emits anything for any other tag, effect,
    or layer style. See core.effect_stroke_clamp's module docstring for
    why this is safe where effect_conformer.py's general premise was not.
    """
    from core.effect_stroke_clamp import compute_stroke_clamp_layer_style, layer_scale_factor

    clamped = 0
    for i, layer_dict in enumerate(result["layers"]):
        src = manifest.layers[i] if i < len(manifest.layers) else None
        if src is None:
            continue

        ct = layer_dict.get("conformed_transforms") or {}
        factor = layer_scale_factor(getattr(src, "scale", None), ct.get("scale"))
        if factor is None:
            continue

        style = compute_stroke_clamp_layer_style(src, factor)
        if style is not None:
            layer_dict["conformed_layer_styles"] = [style.model_dump(exclude_none=True)]
            clamped += 1

    if clamped:
        log.info("Effect stroke-clamp pass complete", extra={"layers_clamped": clamped})
    return clamped


def apply_soe_pass(
    result: dict[str, Any],
    engine: ScaleEngine,
    *,
    preset_slug: str,
    target_w: int,
    target_h: int,
    run_warnings: list[str],
    emit_warning: Callable[[str, list[str]], None],
) -> Optional[list]:
    """Run Spatial Occlusion Engine when enabled.  Returns corrections or None."""
    try:
        from logic.preferences_state import preferences as prefs

        soe_enabled = bool(prefs.enable_soe)
    except Exception:
        soe_enabled = True

    if not soe_enabled:
        log.info("SOE disabled by preference", extra={"preference": "enable_soe=False"})
        return None

    try:
        from logic.safe_zone_resolver import resolve_mask_for_target, resolve_mask_path
        from core.occlusion_engine import OcclusionEngine, OcclusionMask

        mask_path = None
        if preset_slug:
            soe_target = PresetManager().get_target(preset_slug)
            if soe_target is not None:
                mask_path = resolve_mask_for_target(soe_target)
            if mask_path is None:
                mask_path = resolve_mask_path(preset_slug)

        if mask_path is None:
            emit_warning(
                f"SOE skipped — no safe-zone mask for preset "
                f"'{preset_slug or '(none)'}' (MASK_MISSING). Text/legals "
                f"were NOT nudged away from platform UI chrome.",
                run_warnings,
            )
            return None

        mask_arg = str(mask_path) if isinstance(mask_path, Path) else mask_path
        mask = OcclusionMask(mask_arg, target_w, target_h)
        soe_engine = OcclusionEngine(
            mask, preset_slug, resolution=engine.placement_resolution,
            inactive_keys=getattr(engine, "variant_inactive_keys", None)
        )
        updated, corrections = soe_engine.run(result)
        result.clear()
        result.update(updated)  # in-place so conform.py keeps the same reference
        for scene_warn in (result.get("warnings", {}).get("soe_scene_preserve_warnings") or []):
            emit_warning(scene_warn, run_warnings)
        log.info(
            "SOE pass complete",
            extra={"preset": preset_slug, "correction_count": len(corrections or [])},
        )
        print(
            json.dumps({"type": "progress", "pct": 60, "msg": "SOE pass complete"}),
            flush=True,
        )
        return corrections
    except Exception as e:
        emit_warning(
            f"SOE failed — safe-zone pass skipped, layout was NOT "
            f"adjusted for platform UI chrome ({e})",
            run_warnings,
        )
        return None


def build_mirror_tree_spec(
    source_path: str,
    manifest: ScrapeManifest,
    engine: ScaleEngine,
    *,
    preset: Optional[str],
    preset_label: str,
    target_w: int,
    target_h: int,
    scale_mode: str,
    bleed_pct: float,
    run_warnings: Optional[list[str]] = None,
    emit_warning: Optional[Callable[[str, list[str]], None]] = None,
) -> MirrorTreeSpec:
    """Slot 12.5 mirror tree + rewires for chunk manifest (best-effort)."""
    empty = MirrorTreeSpec([], [], "")
    structure_path = Path(source_path).parent / "project_structure.json"
    if not structure_path.is_file():
        log.info(
            "cli.mirror_tree.skipped: no project_structure.json",
            extra={"structure_path": str(structure_path)},
        )
        # E10: loud warning when precomp wrappers exist but project_structure.json is missing
        from core.layer_utils import canon_tag as _canon
        _EXCLUDED_TAGS = {"GUIDE", "PROTECT"}
        wrapper_count = sum(
            1
            for layer in manifest.layers
            if layer.source_item is not None
            and getattr(layer.source_item, "kind", None) == "comp"
            and _canon(layer.content_tag) not in _EXCLUDED_TAGS
        )
        if wrapper_count > 0 and emit_warning is not None and run_warnings is not None:
            emit_warning(
                f"Mirror tree skipped — project_structure.json missing for comp with "
                f"{wrapper_count} precomp wrapper(s). Nested precomps were NOT mirrored or rewired.",
                run_warnings,
            )
        return empty

    try:
        from datetime import datetime
        from core.output_naming import (
            build_mirror_rewires_spec,
            build_mirror_tree_spec as _build_mirror_tree,
            session_bin_path,
        )
        from logic.preset_manager import Preset, PresetManager
        from models.project_structure import ProjectStructure

        structure = ProjectStructure.model_validate_json(structure_path.read_text())
        if preset:
            preset_obj = PresetManager().presets.get(preset)
        else:
            preset_obj = Preset(
                id="manual",
                label=preset_label,
                width=target_w,
                height=target_h,
                scale_mode=scale_mode,
                bleed_pct=bleed_pct,
            )
        if preset_obj is None:
            return empty

        mirror_tree = _build_mirror_tree(
            manifest=manifest,
            project_structure=structure,
            preset=preset_obj,
            preserve_nested_dims=bool(engine.sealed_precomp_cids),
            sealed_cids=engine.sealed_precomp_cids,
        )
        mirror_rewires = build_mirror_rewires_spec(
            manifest=manifest,
            project_structure=structure,
        )
        target_bin_path = session_bin_path(
            preset_obj.id, datetime.now().strftime("%Y-%m-%d_%H%M%S")
        )
        log.info(
            "cli.mirror_tree.built",
            extra={
                "mirror_comp_count": len(mirror_tree),
                "mirror_rewire_count": len(mirror_rewires),
                "target_bin_path": target_bin_path,
            },
        )
        return MirrorTreeSpec(mirror_tree, mirror_rewires, target_bin_path)
    except Exception as err:
        log.warning("cli.mirror_tree.build_failed", extra={"error": str(err)})
        if emit_warning is not None and run_warnings is not None:
            emit_warning(
                f"Mirror tree build failed ({err}). Nested precomps were NOT mirrored or rewired.",
                run_warnings,
            )
        return empty