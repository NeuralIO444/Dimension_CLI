# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/stages/conform.py
Stage 3 — conform math pipeline (scale → lerp → SOE → slice → report).

Pure orchestration of the Python conform layer.  AE scrape (stage 1)
and inject (stage 4) live elsewhere; this module owns Stage B only.

Called by `orchestrator.py` (thin CLI dispatcher) and importable for
tests / future batch runners.
"""

from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from core.io_utils import atomic_write_json
from core.conform_options import get_camera_depth_mode
from core.scale_engine import ScaleEngine, SpatialBoundError
from core.logger import log
from logic.exporter import PayloadSlicer
from stages.expression import scale_expressions
from logic.report_generator import generate_report
from stages.conform_passes import (
    apply_effect_stroke_clamp_pass,
    apply_lerp_pass,
    apply_soe_pass,
    build_mirror_tree_spec,
)


class ConformError(Exception):
    """Base conform pipeline failure — carries CLI exit code."""

    exit_code: int = 1


class SourceNotFoundError(ConformError):
    exit_code = 2


class IntegrityCheckError(ConformError):
    exit_code = 3


@dataclass(frozen=True)
class ConformConfig:
    """Inputs for a single conform run — no side effects until `run_conform`."""

    source: str
    preset: Optional[str] = None
    profile: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    fps: Optional[float] = None
    mode: str = "Fit"
    layout: str = "tags"
    bleed: float = 0.0
    output: str = "Chunks"
    no_report: bool = False
    allow_state_hash_bypass: bool = False


@dataclass
class ConformResult:
    """Outputs from a successful conform run."""

    chunk_manifest_path: str
    report_path: str
    conformed_path: str
    run_warnings: list[str]
    chunk_count: int = 0


def _creation_names(chunk_manifest_path: str) -> list[str]:
    """Output comp names the slicer emitted. Empty if the manifest
    cannot be read — provenance must not invent names."""
    try:
        with open(chunk_manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    names: list[str] = []
    output = data.get("output_comp_name")
    if isinstance(output, str) and output.strip():
        names.append(output.strip())
    for entry in data.get("mirror_tree") or []:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("comp_name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return names


def _record_conform_provenance(
    config: ConformConfig,
    *,
    status: str,
    session: str = "",
    chunk_manifest_path: str = "",
    names: Optional[list[str]] = None,
    detail: Optional[dict] = None,
) -> None:
    """Write the project SQLite store. Failures are logged, never raised.

    Successful runs record one creation per emitted comp name. Failed
    runs record the run only — no invented creations. Does not write
    duplication_log.json.
    """
    try:
        from core.dimension_db import open_project_db, record_creation, record_run
        project_dir = os.path.dirname(os.path.abspath(config.source))
        conn = open_project_db(project_dir)
        try:
            record_run(
                conn,
                session=session,
                status=status,
                detail={
                    **(detail or {}),
                    "chunk_manifest": chunk_manifest_path,
                },
            )
            if status == "ok":
                for name in names or []:
                    record_creation(
                        conn,
                        name=name,
                        source=os.path.basename(config.source),
                        session=session,
                        operation="conform",
                    )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — provenance must not fail the conform
        log.warning("Provenance write failed", extra={"error": str(exc)})


def build_layer_payloads(manifest, conformed_result, plan):
    """Build per-comp layer_payloads lists and attach them to each new_comps entry.

    Pure function — no I/O, no imports from reconstruction.py (keeps the
    planner decoupled from the conform pipeline per the zero-regression
    invariant tested in TestZeroRegressionProof).

    Args:
        manifest:          ScrapeManifest (Pydantic model). Used for
                           project_info.name (source comp name for JSX lookup).
        conformed_result:  dict from ScaleEngine.conform() — has a "layers"
                           list where each entry is a layer dict with
                           "conformed_transforms", "uid", "index", "layer_kind",
                           "parent_index", "containing_comp_id" etc.
        plan:              ReconstructionPlan. plan.new_comps is the list of
                           new comp specs to enrich. Each spec receives a
                           "layer_payloads" key.

    Returns:
        dict keyed by comp name (matching plan.new_comps[i]["name"]) whose
        values are lists of layer-payload dicts. Suitable for passing to
        SovereignBridge.execute_reconstruction_plan(layer_payloads_by_comp=...).

    Phase 1 scope:
        - All layers from the manifest's root comp are included (layers whose
          containing_comp_id is None map to the root comp).
        - Child layers (parent_index >= 1) are included with skip_transforms=True
          so copyToComp copies them and the parent cascade handles placement.
        - Root layers (parent_index == -1 or 0) get skip_transforms=False.
        - position/scale/anchor/rotation_z come from conformed_transforms.
        - Keyframe data is NOT included (Phase 1 = static transforms only).
    """
    if not conformed_result or not plan:
        return {}

    layers = conformed_result.get("layers") or []
    if not manifest:
        return {}

    source_comp_name = getattr(getattr(manifest, "project_info", None), "name", "") or ""

    # Group layers by containing_comp_id.
    # containing_comp_id=None means the layer belongs to the root comp.
    layers_by_comp_id = {}
    for layer_dict in layers:
        comp_id = layer_dict.get("containing_comp_id")
        if comp_id not in layers_by_comp_id:
            layers_by_comp_id[comp_id] = []
        layers_by_comp_id[comp_id].append(layer_dict)

    # Phase 1: all new_comps get the root-comp layers (containing_comp_id=None).
    # Future phases can route layers to specific comps by comp_id when
    # multi-comp reconstruction is supported.
    root_layers = layers_by_comp_id.get(None, [])

    payloads = []
    for layer_dict in root_layers:
        ct = layer_dict.get("conformed_transforms") or {}

        uid = layer_dict.get("uid") or ""
        source_index = layer_dict.get("index") or 0
        layer_kind = layer_dict.get("layer_kind") or "av"

        parent_index = layer_dict.get("parent_index")
        if parent_index is None:
            parent_index = -1

        # A layer is root when it has no parent (parent_index == -1 or 0).
        # Also trust conformed_transforms.is_root which ScaleEngine computes
        # (it accounts for collapse transformations and non-uniform parents).
        is_root_ct = ct.get("is_root")
        if is_root_ct is not None:
            is_root = bool(is_root_ct)
        else:
            is_root = (int(parent_index) < 1)

        # Children get skip_transforms=True — parent cascade handles placement.
        skip_transforms = not is_root

        # Conformed position/scale/anchor/rotation from ScaleEngine output.
        position = ct.get("position") or [0.0, 0.0, 0.0]
        scale = ct.get("scale") or [100.0, 100.0, 100.0]
        anchor = ct.get("anchor") or [0.0, 0.0, 0.0]
        # conformed_transforms uses key "rotation" for Z rotation.
        rotation_z = ct.get("rotation") or 0.0

        payload = {
            "uid":              uid,
            "source_index":     source_index,
            "source_comp_name": source_comp_name,
            "layer_kind":       layer_kind,
            "is_root":          is_root,
            "skip_transforms":  skip_transforms,
            "position":         list(position),
            "scale":            list(scale),
            "anchor":           list(anchor),
            "rotation_z":       float(rotation_z),
        }
        payloads.append(payload)

    # Return a dict keyed by comp name for all new_comps entries.
    # Phase 1: all comps get the same root-layer payload list.
    result = {}
    for spec in (plan.new_comps if hasattr(plan, "new_comps") else []):
        comp_name = spec.get("name") if isinstance(spec, dict) else getattr(spec, "name", "")
        if comp_name:
            result[comp_name] = payloads

    return result


# Back-compat aliases — tests and reconstruction helpers import these names.
_build_layer_payloads = build_layer_payloads


def plan_ooh_render_queue_options(target: Any) -> Optional[dict]:
    """#417 -- automatic, spec-driven render-queue configuration for OOH
    targets. Strictly gated: only OOH-channel targets whose metadata
    actually carries a codec/audio spec (from
    `data.target_catalog._load_ooh_targets`) produce a non-None result.
    Every other channel (social/broadcast/DCP/custom, or an OOH target
    with no metadata) returns None, so `slice_and_export`'s
    `render_queue_options` param stays None and Babysitter's own opt-in
    guard (`if (_rqOptions && ...)`) means behavior is byte-identical to
    before this function existed.

    Returns a dict with the snake_case keys `exporter.py`'s
    `render_queue_options` param and Babysitter_src/50_workspace.jsx's
    manifest reader already expect (`template_name`, `file_extension`,
    `disable_audio`) -- NOT `RenderQueueConfig.to_extendscript_options()`'s
    camelCase shape, which is JSX-facing, not manifest-facing.
    """
    if getattr(target, "channel", None) != "ooh":
        return None
    metadata = getattr(target, "metadata", None) or {}
    if not metadata.get("video_codec") and metadata.get("audio") is None:
        return None

    from core.render_queue import RenderQueuePlanner
    rq_config = RenderQueuePlanner.plan_for_spec(metadata)
    return {
        "template_name": rq_config.template_name,
        "file_extension": rq_config.file_extension,
        "disable_audio": rq_config.disable_audio,
    }


def plan_panel_slicing(target: Any) -> Optional[dict]:
    """Issue #346 (narrow slice) — spec-driven multi-panel OOH slicing
    plan, for the 2 catalog targets (transit_triptychs, e.g. OOH-064's
    Market 15 Liveboard) that carry a `multi_panel` spec on their
    metadata. Every other target (263 of 265 in the catalog) has no
    `multi_panel` key and returns None here, so `slice_and_export`'s
    `panel_slicing_plan` param stays None and the manifest field is
    absent — byte-identical to before this function existed. Mirrors
    `plan_ooh_render_queue_options`'s gating shape exactly (#417).

    Returns the `PanelSlicingPlan` as a plain dict (Pydantic
    `model_dump()`) — manifest-facing, not an object Babysitter reads
    yet. `core/panel_slicer.py`'s own docstring is explicit that the
    JSX half (materializing this plan into real AE comps) is separate,
    not-yet-implemented work; this function only produces the data
    contract that side will eventually consume. No Babysitter/JSX code
    anywhere checks for this manifest key today, so its mere presence
    cannot change any AE behavior — safer than `render_queue_options`,
    which Babysitter does act on.
    """
    metadata = getattr(target, "metadata", None) or {}
    multi_panel = metadata.get("multi_panel")
    if not multi_panel:
        return None

    from core.panel_slicer import PanelSlicingPlanner
    base_name = getattr(target, "id", None) or getattr(target, "label", None) or "PANEL"
    planner = PanelSlicingPlanner.from_metadata(multi_panel, base_name)
    return planner.plan().model_dump()


def validate_no_nan_or_inf(data: Any, path: str = "") -> None:
    """Recursively search for NaN or Infinity float values in the conform payload.

    If found, raises a ValueError.
    """
    if isinstance(data, dict):
        for k, v in data.items():
            validate_no_nan_or_inf(v, f"{path}.{k}" if path else str(k))
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            validate_no_nan_or_inf(item, f"{path}[{idx}]")
    elif isinstance(data, float):
        if math.isnan(data) or math.isinf(data):
            raise ValueError(f"Invalid coordinate/float '{data}' found at path '{path}'")


def _emit_engine_event(
    event: dict[str, Any],
    on_engine_event: Optional[Callable[[dict[str, Any]], None]] = None,
) -> None:
    """Emit a structured engine event to stdout and optional in-process consumers."""
    try:
        print(json.dumps(event), flush=True)
    except Exception:  # noqa: BLE001 — a broken stdout must not kill the run
        pass
    if on_engine_event is not None:
        try:
            on_engine_event(event)
        except Exception:  # noqa: BLE001 — consumer failures must not kill the run
            pass


def emit_run_warning(
    msg: str,
    sink: list,
    *,
    on_engine_event: Optional[Callable[[dict[str, Any]], None]] = None,
) -> None:
    """Phase 1 (loud failures) — surface one degradation on all three
    channels at once: the structured log, the panel's JSON event stream
    (`{"type": "warning"}`), and — via `sink` — the HTML report's Run
    Warnings section. Degradations stay non-fatal; they just stop being
    invisible."""
    log.warning(msg)
    sink.append(msg)
    _emit_engine_event({"type": "warning", "msg": msg}, on_engine_event)


_emit_run_warning = emit_run_warning


def run_conform(
    config: ConformConfig,
    *,
    on_engine_event: Optional[Callable[[dict[str, Any]], None]] = None,
) -> ConformResult:
    """Execute the conform pipeline.  Raises ConformError subclasses on failure."""

    # Phase 1 (loud failures) — every degraded-but-continuing path in this
    # run appends here; the list feeds the report's Run Warnings section.
    run_warnings: list = []

    def _warn(msg: str, _list: list | None = None) -> None:
        # conform_passes' emit_warning callbacks pass (msg, run_warnings);
        # that list is the same run_warnings captured here, so it's ignored.
        emit_run_warning(msg, run_warnings, on_engine_event=on_engine_event)

    # --- 1–3. Manifest load, integrity, target resolution (conform_io) ---
    from stages.conform_io import load_scrape_manifest, resolve_conform_target

    source = os.path.abspath(config.source)
    manifest, raw_data = load_scrape_manifest(source)

    log.info("Manifest loaded", extra={
        "comp": manifest.project_info.name,
        "resolution": f"{manifest.project_info.width}x{manifest.project_info.height}",
        "layers": len(manifest.layers),
    })
    _emit_engine_event(
        {"type": "progress", "pct": 10,
         "msg": f"Manifest loaded — {len(manifest.layers)} layers"},
        on_engine_event,
    )

    target = resolve_conform_target(config)
    target_w, target_h = target.width, target.height
    target_dur = target.duration
    target_fps = target.fps
    scale_mode = target.scale_mode
    bleed_pct = target.bleed_pct

    log.info("Conform target", extra={
        "target": f"{target_w}x{target_h}",
        "mode": scale_mode,
        "bleed_pct": bleed_pct,
    })

    # --- 4. Pipeline: scale → lerp → export → report ---
    # Wrapped in a single top-level try-except so failures produce a clean
    # exit code instead of an unhandled traceback.
    try:
        # Scale engine
        # v5.2.2 — Studio Profile drives Gravity / per-tag scale.
        try:
            from logic.studio_profile_registry import REGISTRY as _SPR
            if config.profile:
                _active_profile = _SPR.get_profile(config.profile)
            else:
                _active_profile = _SPR.get_active()
        except Exception:
            _active_profile = None

        # Fix 1 — load per-comp dimensions from project_structure.json so
        # layers in nested precomps use the correct comp center, not the
        # root comp's center. Wrapped in try/except so a missing or malformed
        # structure file never blocks a flat-comp conform.
        _comp_dims: dict = {}
        # Audit fix (2026-08-27) — real existing-comp-names source for
        # slice_and_export's output_comp_name collision check (same
        # project_structure.json the mirror-tree path already reads
        # for this exact purpose). Empty set (not None) when the
        # structure file is missing/malformed — slice_and_export still
        # resolves a name, just without collision protection, same as
        # its behavior before this fix.
        _existing_comp_names: set = set()
        from pathlib import Path as _Path
        _structure_path = _Path(source).parent / "project_structure.json"
        if _structure_path.exists():
            try:
                from models.project_structure import ProjectStructure as _PS
                with open(_structure_path, "r", encoding="utf-8") as _f:
                    _ps = _PS.model_validate(json.load(_f))
                _comp_dims = {c.id: (c.width, c.height) for c in _ps.comps}
                _existing_comp_names = {c.name for c in _ps.comps}
                log.info("comp_dims loaded from project_structure",
                         extra={"comp_count": len(_comp_dims)})
            except Exception as _e:
                log.warning("Could not load project_structure for comp dims",
                            extra={"error": str(_e)})

        # Read camera depth mode from conform_options.json (Gardener
        # recommendation or user override from the preflight toggle).
        # None = ScaleEngine auto-detects from the manifest.
        from pathlib import Path as _OPath
        # source is the path to scrape_manifest.json; it lives inside
        # .dimension/ — so its parent IS the .dimension dir.
        _dimension_dir = _OPath(source).parent
        _camera_depth_mode = get_camera_depth_mode(_dimension_dir)

        engine = ScaleEngine(manifest, target_w, target_h, scale_mode,
                             bleed_pct / 100.0, profile=_active_profile,
                             comp_dims=_comp_dims,
                             camera_depth_mode=_camera_depth_mode,
                             layout=config.layout)

        # U2 — the engine builds the placement resolution + unit tree
        # (idempotent; conform() would build it anyway — calling it here
        # surfaces the "Design read" event before the math runs). The
        # report card renders the SAME tree the conform math reads.
        _placement_units = None
        try:
            _pu_report = engine.build_units()
            _placement_units = _pu_report.model_dump()
            _emit_engine_event(
                {"type": "progress", "pct": 22,
                 "msg": f"Design read: {_pu_report.summary}"},
                on_engine_event,
            )
            if engine.placement_degraded:
                _warn(engine.placement_degraded)
            if _pu_report.error:
                _warn(f"Placement-unit analysis degraded ({_pu_report.error})")
            # PR-V2 follow-up (issue #372) -- `variant_gate.py::resolve_variants`
            # refuses a `variant:` directive found on a sealed-precomp or
            # preserving-scene member and records why, but nothing downstream
            # ever read `.warnings` off the resolution before this. Surface
            # them the same way every other build_units() degradation is
            # surfaced, so the artist sees why their directive silently did
            # nothing on that layer.
            if engine.variant_resolution is not None:
                for _vw in engine.variant_resolution.warnings:
                    _warn(_vw)
        except Exception as _pu_err:
            _warn(f"Placement-unit analysis unavailable ({_pu_err})")

        result = engine.conform()
        # U2 Phase 7 — refresh the report payload post-conform so the
        # rendered card carries anchor_resolved (what each unit actually
        # got), stamped by the engine from the same resolution object.
        try:
            if engine.placement_units_report is not None:
                _placement_units = engine.placement_units_report.model_dump()
        except Exception:  # noqa: BLE001 — report-only refresh
            pass
        # U2 Phase 5: keyed on any-preserving-scene-unit (same truth table
        # as the legacy global scene_preserve_active flag, generalized for
        # per-comp resolution).
        if engine.any_preserving_scene_unit:
            _emit_engine_event(
                {
                    "type": "progress", "pct": 35,
                    "msg": "Layout: scene-preserve (3D camera scene) — "
                           "composition kept intact, tags not re-laying-out",
                },
                on_engine_event,
            )
        _emit_engine_event(
            {"type": "progress", "pct": 40,
             "msg": f"Scale complete — {len(result['layers'])} layers conformed"},
            on_engine_event,
        )

        if result["warnings"]["collapsed_layers"]:
            log.warning("Collapse Transformations detected — world-space math applied",
                        extra={"layers": result["warnings"]["collapsed_layers"]})

        s_factor = result["scale"]["S"]
        apply_lerp_pass(
            manifest,
            result,
            engine,
            target_w=target_w,
            target_h=target_h,
            comp_dims=_comp_dims,
        )

        # Effect / layer style parameter scaling — DISABLED 2026-07-02.
        #
        # Effect points, sizes, and style offsets live in the LAYER'S OWN
        # pixel space, and AE applies effects before the layer transform.
        # This conform scales layers via their transform, which scales the
        # rendered effect along with the content — so conforming the
        # params too DOUBLE-transforms them. Matt's AE ground-truth run
        # caught it on 87N's EFX layer: CC Lens Center 936,500 was written
        # as 526.5,888.9 (x×S, y×fill_S) on a layer already scaled to
        # 177.78%, warping the lens far off the design. Leaving params at
        # their source values renders the effect exactly as designed.
        #
        # History: effect_conformer's output was silently dropped for a
        # year (no Babysitter writer — see the "computed but never
        # consumed" sharp edge). A recent session wired the writer, and
        # the first real AE test exposed that the producer's premise
        # ("Drop Shadow distance is a compositor pixel offset that needs
        # S") was wrong for transform-based conforms.
        #
        # `effect_conformer.py` was DELETED 2026-08-19 (DCE Phase 2a) —
        # it was unreachable, and its math was wrong for every mode the
        # product actually ships. Recover with
        # `git log --diff-filter=D -- python/core/effect_conformer.py`
        # if a future comp-resize-WITHOUT-transform mode ever needs it,
        # but re-derive the math against real AE first; do not restore
        # it as-is.
        #
        # #391 (2026-09-05) — Babysitter's effect/layer-style writer was
        # never actually dead; it's called unconditionally every inject
        # and simply no-ops when conformed_effects/conformed_layer_styles
        # are absent, which they always were once effect_conformer.py's
        # emission was disabled. The pass below is the first live producer
        # since then — deliberately NOT a restoration of the general
        # "scale effect params by S" premise above. It only ever calls
        # typographic_micro_guard's inverse floor-clamp for one matchName
        # (Stroke layer style Size) on BOTTOM/LEGALS layers, and only
        # emits anything when that clamp actually needs to intervene.
        # Every other effect/layer-style param on every layer stays
        # completely untouched, so the double-transform class of bug this
        # comment describes cannot recur through this path.
        apply_effect_stroke_clamp_pass(manifest, result)

        # Spatial Occlusion Engine — post-conform pass that nudges
        # TYPE/LEGALS layers off platform UI chrome. Gated by the
        # `enable_soe` user preference (default True). Skips
        # gracefully when no safe-zone mask is found for this preset
        # — the deleted PySide6 qt_controller had this exact wiring;
        # restored here so the CEP Preferences toggle actually has
        # an effect on output.
        soe_corrections = apply_soe_pass(
            result,
            engine,
            preset_slug=config.preset or "",
            target_w=target_w,
            target_h=target_h,
            run_warnings=run_warnings,
            emit_warning=_warn,
        )

        # Expression Scaling Pass
        try:
            result = scale_expressions(result, engine)
            log.info("Expression scaling pass complete.")
        except Exception as e:
            _warn(f"Expression scaling pass failed: {e}")


        # NaN / Infinity Firewall
        try:
            validate_no_nan_or_inf(result)
        except ValueError as firewall_err:
            log.error("FATAL: NaN/Infinity firewall triggered", extra={"error": str(firewall_err)})
            raise ConformError(str(firewall_err)) from firewall_err

        # Write conformed manifest
        out_dir = os.path.abspath(config.output)
        conformed_path = os.path.join(os.path.dirname(out_dir), "conformed_manifest.json")
        atomic_write_json(conformed_path, result)


        # SOE corrections sidecar (only when SOE ran).
        if soe_corrections:
            try:
                from core.occlusion_engine import corrections_to_jsonable
                _soe_sidecar = os.path.join(os.path.dirname(out_dir), "soe_corrections.json")
                with open(_soe_sidecar, "w", encoding="utf-8") as _f:
                    json.dump(corrections_to_jsonable(soe_corrections), _f, indent=2)
                log.info("SOE corrections sidecar written",
                         extra={"path": _soe_sidecar})
            except Exception as e:
                log.warning("Failed to write SOE corrections sidecar",
                            extra={"error": str(e)})

        # Slice and export chunks (pass metadata for Babysitter comp identity)
        preset_label = config.preset or f"{target_w}x{target_h}"

        # Slot 12.5 Stage D Items 2+3 — CLI parity. Looks for
        # `project_structure.json` next to the source manifest; when
        # absent (legacy / 87N flat), the lists stay empty and
        # Babysitter takes the legacy single-comp setupWorkspace
        # path. Wrapped in try/except so a Stage-D build failure
        # never blocks a flat-comp CLI conform.
        _mirror = build_mirror_tree_spec(
            source,
            manifest,
            engine,
            preset=config.preset,
            preset_label=preset_label,
            target_w=target_w,
            target_h=target_h,
            scale_mode=scale_mode,
            bleed_pct=bleed_pct,
            run_warnings=run_warnings,
            emit_warning=_warn,
        )
        _cli_mirror_tree = _mirror.mirror_tree
        _cli_mirror_rewires = _mirror.mirror_rewires
        _cli_target_bin_path = _mirror.target_bin_path

        # #417 -- exporter.py's render_queue_options param has existed
        # since #348's narrow slice (2026-09-03) but had no caller that
        # set it -- this is that caller. `target` here is the slim
        # stages.conform_io.ConformTarget (width/height/scale_mode/etc.)
        # -- it has no channel/metadata. Those live only on the catalog's
        # models.target.Target, resolved via PresetManager().get_target(),
        # same pattern apply_soe_pass already uses above for its own
        # Target-only field (subcategory/channel) lookup. See
        # plan_ooh_render_queue_options' docstring for the gating contract.
        try:
            from logic.preset_manager import PresetManager
            catalog_target = (
                PresetManager().get_target(config.preset) if config.preset else None
            )
            render_queue_options = plan_ooh_render_queue_options(catalog_target)
            if render_queue_options:
                log.info("OOH render queue configuration planned", extra=render_queue_options)
        except Exception as e:
            render_queue_options = None
            _warn(f"OOH render queue planning failed: {e}")

        try:
            panel_slicing_plan = plan_panel_slicing(catalog_target)
            if panel_slicing_plan:
                log.info("Multi-panel OOH slicing plan computed",
                         extra={"panel_count": panel_slicing_plan.get("panel_count"),
                                "master_comp_name": panel_slicing_plan.get("master_comp_name")})
        except Exception as e:
            panel_slicing_plan = None
            _warn(f"Multi-panel OOH slicing planning failed: {e}")

        # #346 Part C — plan is written onto the chunk manifest only.
        # AE dispatch is bridge.panel_slicing.maybe_apply_panel_slicing_plan
        # (no-op when plan is None so the other 263 catalog targets stay
        # byte-identical). Not called from run_conform: live AE must be
        # present, and applyPanelSlicingPlan geometry is still unverified.

        slicer = PayloadSlicer(output_dir=out_dir)
        chunk_manifest_path = slicer.slice_and_export(
            result["layers"],
            expected_comp_name=manifest.project_info.name,
            target_width=target_w,
            target_height=target_h,
            preset_label=preset_label,
            output_name_template=target.output_name_template,
            existing_comp_names=_existing_comp_names,
            mirror_tree=_cli_mirror_tree if _cli_mirror_tree else None,
            target_bin_path=_cli_target_bin_path,
            mirror_rewires=_cli_mirror_rewires if _cli_mirror_rewires else None,
            allow_state_hash_bypass=config.allow_state_hash_bypass,
            render_queue_options=render_queue_options,
            panel_slicing_plan=panel_slicing_plan,
        )
        log.info("Chunks written", extra={"manifest": chunk_manifest_path})
        # Count chunks from the manifest file written by slicer
        try:
            import pathlib as _pathlib
            _chunk_dir = _pathlib.Path(chunk_manifest_path).parent
            _chunk_count = len(list(_chunk_dir.glob("chunk_*.json")))
        except Exception:
            _chunk_count = 0
        _emit_engine_event(
            {"type": "progress", "pct": 80,
             "msg": f"Chunks written — {_chunk_count} chunks"},
            on_engine_event,
        )

        # HTML report
        # Slot 9 — CLI doesn't run inject (it's headless/scripted: scrape →
        # conform → slice → exit, with the chunk manifest left for an
        # external Babysitter caller to pick up). So the post-inject defer
        # that the Qt and Tk paths use doesn't apply here. Reports from the
        # CLI never contain a duplication audit section (no inject means
        # no duplication_log.json on disk and no _pending_dup_plan to pass
        # in). Acceptable: CLI is for automation, not artist review.
        _report_path = ""  # populated below if report is written
        if not config.no_report:
            session_id = str(uuid.uuid4())[:8].upper()
            # Per-target unique report filename so multi-target batch
            # runs don't overwrite each other. Slug from preset id or
            # WxH for the manual path. Format:
            #   conform_report__<slug>__<WxH>.html
            # Bare `conform_report.html` is also written as a copy of
            # the latest target so legacy "open the report" affordances
            # keep working.
            import re as _re_cli
            _slug = _re_cli.sub(r'[^A-Za-z0-9_-]+', '_',
                                (config.preset or 'manual')).strip('_')
            _report_name = f"conform_report__{_slug}__{target_w}x{target_h}.html"
            _report_dir = os.path.dirname(out_dir)
            _report_path = os.path.join(_report_dir, _report_name)
            report_path = generate_report(
                conformed_manifest=result,
                scrape_manifest=raw_data,
                preset_label=preset_label,
                target_w=target_w,
                target_h=target_h,
                scale_mode=scale_mode,
                uniform_scale=s_factor,
                session_id=session_id,
                output_path=_report_path,
                run_warnings=run_warnings,
                placement_units=_placement_units,
            )
            # Also mirror the latest target as `conform_report.html`
            # for backwards-compatible "open the report" callers.
            try:
                _legacy_path = os.path.join(_report_dir, "conform_report.html")
                with open(_report_path, "rb") as _rf:
                    _bytes = _rf.read()
                with open(_legacy_path, "wb") as _wf:
                    _wf.write(_bytes)
            except Exception as _le:
                log.warning("Could not mirror latest report to conform_report.html",
                            extra={"error": str(_le)})
            # session_id in extra lets check_session / session_correlate
            # match this line directly instead of falling back to the
            # timestamp window (RECOMMENDATIONS M8).
            log.info("Report written",
                     extra={"path": report_path, "session_id": session_id})

        log.info("Pipeline complete", extra={"chunk_manifest": chunk_manifest_path})
        _emit_engine_event(
            {"type": "done", "report_path": str(_report_path)},
            on_engine_event,
        )
        log.info("Chunk manifest path", extra={"path": chunk_manifest_path})
        _record_conform_provenance(
            config,
            status="ok",
            session=session_id if not config.no_report else "",
            chunk_manifest_path=chunk_manifest_path,
            names=_creation_names(chunk_manifest_path),
            detail={
                "preset": config.preset,
                "chunk_count": _chunk_count,
                "conformed_path": conformed_path,
            },
        )

        # Stage 4: emit reconstruction plan for arbitrary/custom targets (sidecar for later injection/review)
        try:
            from core.reconstruction import plan_reconstruction, TargetSpec, emit_reconstruction_plan_as_sidecar
            is_arbitrary = bool(config.duration or config.fps or target_dur or target_fps or getattr(manifest, 'target_spec', None))
            if is_arbitrary:
                dur = target_dur or 30.0
                fpsv = target_fps or 24.0
                # TASK-P2-02 — snap the arbitrary-target duration/fps to an
                # exact integer-frame boundary before it reaches the
                # sidecar JSON that Babysitter reads (plan.target.fps /
                # plan.target.duration). Without this, fractional NTSC
                # rates (23.976, 29.97, 59.94) and non-frame-aligned
                # durations flow straight through to the injector.
                from core.framerate_engine import FramerateEngine
                fpsv = FramerateEngine.normalize_fps(fpsv)
                dur, _target_frames = FramerateEngine.snap_duration(dur, fpsv)
                t = TargetSpec(width=target_w or 1080, height=target_h or 1920, duration=dur, fps=fpsv)
                manifest.target_spec = getattr(manifest, 'target_spec', None) or t.__dict__
                rplan = plan_reconstruction(manifest, target_spec=t)
                emit_reconstruction_plan_as_sidecar(source, rplan)
                log.info("Stage 4 reconstruction plan emitted")
        except Exception as _re:
            # Phase 1 (loud failures) — this used to be a DEBUG-level
            # "skipped" that hid real Stage 4 failures. The run already
            # completed by this point, so the report can't carry it —
            # the warning event + log are the surfaces.
            _warn(
                f"Stage 4 reconstruction plan NOT emitted — arbitrary-target "
                f"sidecar missing for this run ({_re})",
            )

        return ConformResult(
            chunk_manifest_path=chunk_manifest_path,
            report_path=str(_report_path),
            conformed_path=conformed_path,
            run_warnings=run_warnings,
            chunk_count=_chunk_count,
        )

    except SpatialBoundError as e:
        log.error("Spatial bound exceeded", extra={"error": str(e)})
        _emit_engine_event({"type": "error", "msg": str(e)}, on_engine_event)
        _record_conform_provenance(config, status="failed", detail={"error": str(e)})
        raise ConformError(str(e)) from e
    except ConformError as e:
        _record_conform_provenance(config, status="failed", detail={"error": str(e)})
        raise
    except (ValueError, OSError) as e:
        log.error("Pipeline error", extra={"error": str(e)})
        _emit_engine_event({"type": "error", "msg": str(e)}, on_engine_event)
        _record_conform_provenance(config, status="failed", detail={"error": str(e)})
        raise ConformError(str(e)) from e
    except Exception as e:
        log.error("Unexpected pipeline failure", extra={"error": str(e), "type": type(e).__name__})
        _emit_engine_event({"type": "error", "msg": str(e)}, on_engine_event)
        _record_conform_provenance(config, status="failed", detail={"error": str(e)})
        raise ConformError(str(e)) from e
