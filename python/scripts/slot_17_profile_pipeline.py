#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/scripts/slot_17_profile_pipeline.py

Slot 17 Phase 1 — in-process profiling driver for the Python conform pipeline.

Drives the same engines ConformWorker.run uses (scale → lerp → SOE →
write_conformed → slice → report), but in-process and without Qt, so phase
timings are captured deterministically against the local archived manifests.
Mirrors the Slot 11.5 baseline capture pattern, with phase_timer /
time.perf_counter markers around every phase boundary.

The "effects" phase (and its layers_with_effects / layers_with_styles
counters) was removed in DCE Phase 2a (2026-08-19) along with
core/effect_conformer.py, so this driver again matches the live conform
stage, whose effects block has been disabled since 2026-07-02. The phase
measured 0.01-0.1 ms (0.0-0.1% of python_total_ms) in every committed run;
docs/roadmap/slot-17-profiling-baseline.data.json retains those historical
numbers and is not regenerated.

This driver covers ONLY the Python-side pipeline. Scrape (JSX → manifest)
and inject (chunk manifest → AE) require After Effects and are profiled
separately by reading dimension.log entries from real AE runs.

Usage (from repo root):
    python3 python/scripts/slot_17_profile_pipeline.py

Outputs a per-run phase table to stdout and writes a JSON summary at
docs/roadmap/slot-17-profiling-baseline.data.json for the markdown
baseline doc to reference.

Targets covered (all conform-mode "Fit"):
  - 87N (99 layers, HD)            → TikTok 1080x1920  (SOE active)
  - 87N (99 layers, HD)            → DCP 4K Scope      (no SOE mask)
  - 87N (19 layers, post-Slot12.5) → TikTok 1080x1920  (SOE active)
  - Final Comp (195 layers)        → TikTok 1080x1920  (SOE if mask)
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PYTHON_ROOT = _HERE.parent
sys.path.insert(0, str(_PYTHON_ROOT))

from core.classify import (  # noqa: E402
    detect_3d_camera_scene,
    is_layer_root,
    should_scale_z,
)
from core.io_utils import atomic_write_json  # noqa: E402
from core.lerp_engine import LerpEngine  # noqa: E402
from core.logger import log, phase_timer  # noqa: E402
from core.property_router import merge_legacy_and_v5  # noqa: E402
from core.scale_engine import ScaleEngine  # noqa: E402
from logic.exporter import PayloadSlicer  # noqa: E402
from logic.report_generator import generate_report  # noqa: E402
from models.scrape_manifest import ScrapeManifest  # noqa: E402
from models.target import Target  # noqa: E402

REPO_ROOT = _PYTHON_ROOT.parent
ARCHIVE_ROOT = REPO_ROOT / "logs" / "archive"
OUTPUT_DIR = REPO_ROOT / "docs" / "roadmap"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Profiling target plan. (label, comp_name, expected_min_layers, target_dict)
# expected_min_layers lets us prefer archived sessions with the bigger scrape
# when multiple archives match the same comp name.
TARGETS: list[dict] = [
    {
        "label": "87N (99 layers) → TikTok",
        "comp_name": "87N_Reels_DEV_87Neon_Anima_HD_01_mc",
        "min_layers": 99,
        "target": {
            "id": "builtin:tiktok",
            "label": "TikTok",
            "subcategory": "tiktok",
            "category": "social",
            "width": 1080,
            "height": 1920,
            "aspect_label": "9:16",
            "source": "builtin",
        },
        "mode": "Fit",
        "bleed_pct": 5.0,
    },
    {
        "label": "87N (99 layers) → DCP 4K Scope",
        "comp_name": "87N_Reels_DEV_87Neon_Anima_HD_01_mc",
        "min_layers": 99,
        "target": {
            "id": "builtin:dcp4kscope",
            "label": "DCP 4K Scope",
            "subcategory": "dcp4kscope",
            "category": "digital_cinema",
            "width": 4096,
            "height": 1716,
            "aspect_label": "2.39:1",
            "source": "builtin",
        },
        "mode": "Fit",
        "bleed_pct": 0.0,
    },
    {
        "label": "87N (19 layers, recent) → TikTok",
        "comp_name": "87N_Reels_DEV_87Neon_Anima_HD_01_mc",
        "min_layers": 19,
        "max_layers": 30,
        "target": {
            "id": "builtin:tiktok",
            "label": "TikTok",
            "subcategory": "tiktok",
            "category": "social",
            "width": 1080,
            "height": 1920,
            "aspect_label": "9:16",
            "source": "builtin",
        },
        "mode": "Fit",
        "bleed_pct": 5.0,
    },
    {
        "label": "Final Comp (195 layers) → TikTok",
        "comp_name": "Final Comp",
        "min_layers": 195,
        "target": {
            "id": "builtin:tiktok",
            "label": "TikTok",
            "subcategory": "tiktok",
            "category": "social",
            "width": 1080,
            "height": 1920,
            "aspect_label": "9:16",
            "source": "builtin",
        },
        "mode": "Fit",
        "bleed_pct": 5.0,
    },
]


def find_archived_manifest(
    comp_name: str, min_layers: int, max_layers: int | None = None
) -> Path | None:
    if not ARCHIVE_ROOT.is_dir():
        return None
    candidates = sorted(
        ARCHIVE_ROOT.glob("Session_*/scrape_manifest.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for c in candidates:
        try:
            data = json.loads(c.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if (data.get("project_info") or {}).get("name") != comp_name:
            continue
        n = len(data.get("layers") or [])
        if n < min_layers:
            continue
        if max_layers is not None and n > max_layers:
            continue
        return c
    return None


def profile_one(target_spec: dict) -> dict:
    """Run the full Python-side conform pipeline for one target, capturing
    per-phase elapsed_ms. Returns a summary dict suitable for the JSON
    output + markdown table."""
    label = target_spec["label"]
    manifest_path = find_archived_manifest(
        target_spec["comp_name"],
        target_spec["min_layers"],
        target_spec.get("max_layers"),
    )
    if manifest_path is None:
        return {"label": label, "status": "NO_MANIFEST"}

    phases: dict[str, float] = {}

    # --- VALIDATE (raw load + Pydantic) ---
    t0 = time.perf_counter()
    raw_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    phases["validate.json_load"] = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    manifest = ScrapeManifest.model_validate(raw_data)
    phases["validate.pydantic"] = (time.perf_counter() - t0) * 1000.0

    layer_count = len(manifest.layers)
    src_w = manifest.project_info.width
    src_h = manifest.project_info.height

    # Build Target via Target.make (mirrors qt_controller path).
    t = target_spec["target"]
    target = Target.make(
        id=t["id"],
        label=t["label"],
        category=t["category"],
        subcategory=t["subcategory"],
        width=t["width"],
        height=t["height"],
        aspect_label=t["aspect_label"],
        source=t["source"],
    )
    scale_mode = target_spec["mode"]
    bleed_pct = target_spec["bleed_pct"]
    bleed_fraction = bleed_pct / 100.0

    # --- ACTIVE PROFILE LOAD ---
    t0 = time.perf_counter()
    try:
        from logic.studio_profile_registry import REGISTRY as _SPR
        active_profile = _SPR.get_active()
    except Exception:
        active_profile = None
    phases["profile.load"] = (time.perf_counter() - t0) * 1000.0

    # --- SCALE ---
    with phase_timer("conform.scale", label=label):
        t0 = time.perf_counter()
        engine = ScaleEngine(
            manifest, target.width, target.height,
            scale_mode, bleed_fraction, profile=active_profile,
        )
        final_dict = engine.conform()
        phases["scale"] = (time.perf_counter() - t0) * 1000.0

    s_factor = engine._calculate_base_scale() * (1.0 + bleed_fraction)
    layers_out = final_dict["layers"]
    is_3d_camera_scene = detect_3d_camera_scene(manifest.layers)
    layer_indices = {l.index for l in manifest.layers}
    k_factor = max(
        target.width / src_w if src_w > 0 else 1.0,
        target.height / src_h if src_h > 0 else 1.0,
    )
    k_for_keys = k_factor if is_3d_camera_scene else None

    # --- LERP (per-layer keyframe scaling) ---
    with phase_timer("conform.lerp", label=label):
        t0 = time.perf_counter()
        lerp = LerpEngine()
        animated = 0
        for i, layer_dict in enumerate(layers_out):
            src = manifest.layers[i] if i < len(manifest.layers) else None
            td = None
            if src:
                layer_kind = getattr(src, "layer_kind", "av") or "av"
                legacy_td = (
                    src.temporal_data.model_dump(exclude_none=True)
                    if src.temporal_data else {}
                )
                v5_props = getattr(src, "properties", None) or []
                td = merge_legacy_and_v5(
                    legacy_td, v5_props, layer_kind=layer_kind
                ) or None
            if td:
                parent_idx = getattr(src, "parent_index", -1)
                ct = layer_dict.get("conformed_transforms") or {}
                effective_is_root = ct.get(
                    "is_root",
                    is_layer_root(parent_idx, layer_indices),
                )
                ck = lerp.package_conformed_keys(
                    td, s_factor,
                    is_root=effective_is_root,
                    src_center=[src_w / 2.0, src_h / 2.0],
                    tgt_center=[target.width / 2.0, target.height / 2.0],
                    scale_z=should_scale_z(src, is_3d_camera_scene),
                    K=k_for_keys,
                    layer_kind=layer_kind,
                )
                layer_dict["conformed_keys"] = ck.model_dump(exclude_none=True)
                animated += 1
            else:
                layer_dict["conformed_keys"] = None
        phases["lerp"] = (time.perf_counter() - t0) * 1000.0

    # --- SOE (mask load + occlusion run) ---
    soe_corrections: list = []
    with phase_timer("conform.soe", label=label):
        t0 = time.perf_counter()
        try:
            from logic.safe_zone_resolver import resolve_mask_for_target
            preset_id = target.subcategory or target.id or ""
            mask_path = resolve_mask_for_target(target)
            if mask_path is None:
                phases["soe.mask_load"] = 0.0
                phases["soe.run"] = 0.0
                soe_skipped = True
            else:
                t1 = time.perf_counter()
                from core.occlusion_engine import OcclusionEngine, OcclusionMask
                mask = OcclusionMask(mask_path, target.width, target.height)
                phases["soe.mask_load"] = (time.perf_counter() - t1) * 1000.0

                t1 = time.perf_counter()
                engine_soe = OcclusionEngine(mask, preset_id)
                final_dict, soe_corrections = engine_soe.run(final_dict)
                phases["soe.run"] = (time.perf_counter() - t1) * 1000.0
                soe_skipped = False
        except Exception as e:
            log.warning("SOE_FAILED in profiler", extra={"error": str(e)})
            soe_skipped = True
        phases["soe.total"] = (time.perf_counter() - t0) * 1000.0

    # --- WRITE conformed_manifest.json + SOE corrections sidecar ---
    with phase_timer("conform.write", label=label):
        t0 = time.perf_counter()
        out_dir = REPO_ROOT / ".dimension" / "slot17_profile"
        out_dir.mkdir(parents=True, exist_ok=True)
        conformed_path = out_dir / "conformed_manifest.json"
        atomic_write_json(str(conformed_path), final_dict)
        # SOE corrections sidecar
        soe_path = out_dir / "soe_corrections.json"
        try:
            from core.occlusion_engine import corrections_to_jsonable as _c2j
            soe_path.write_text(json.dumps(_c2j(soe_corrections), indent=2), encoding="utf-8")
        except Exception:
            pass
        phases["write_conformed"] = (time.perf_counter() - t0) * 1000.0

    # --- SLICE (chunk export) ---
    with phase_timer("conform.slice", label=label):
        t0 = time.perf_counter()
        slicer = PayloadSlicer(output_dir=str(out_dir / "Chunks"))
        slicer.slice_and_export(
            final_dict["layers"],
            expected_comp_name=manifest.project_info.name,
            target_width=target.width,
            target_height=target.height,
            preset_label=target.label,
            mirror_tree=None,
            target_bin_path="",
            mirror_rewires=None,
        )
        phases["slice"] = (time.perf_counter() - t0) * 1000.0

    # --- REPORT (HTML generation) ---
    with phase_timer("conform.report", label=label):
        t0 = time.perf_counter()
        report_path = out_dir / "conform_report.html"
        try:
            generate_report(
                conformed_manifest=final_dict,
                scrape_manifest=raw_data,
                preset_label=target.label,
                target_w=target.width,
                target_h=target.height,
                scale_mode=scale_mode,
                uniform_scale=s_factor,
                session_id=uuid.uuid4().hex[:8].upper(),
                output_path=str(report_path),
            )
        except Exception as e:
            log.warning("report gen failed in profiler", extra={"error": str(e)})
        phases["report"] = (time.perf_counter() - t0) * 1000.0

    python_total_ms = sum(phases.values())

    return {
        "label": label,
        "status": "OK",
        "manifest_path": str(manifest_path.relative_to(REPO_ROOT)),
        "comp_name": manifest.project_info.name,
        "layer_count": layer_count,
        "source_resolution": f"{src_w}x{src_h}",
        "target_resolution": f"{target.width}x{target.height}",
        "scale_mode": scale_mode,
        "bleed_pct": bleed_pct,
        "soe_skipped": soe_skipped,
        "phases_ms": {k: round(v, 3) for k, v in phases.items()},
        "animated_layers": animated,
        "python_total_ms": round(python_total_ms, 3),
    }


def main():
    print(f"[slot-17] profiling start {time.strftime('%Y-%m-%dT%H:%M:%S')}")
    print()
    results = []
    for spec in TARGETS:
        print(f"--- {spec['label']} ---")
        r = profile_one(spec)
        if r["status"] != "OK":
            print(f"  {r['status']}")
            results.append(r)
            print()
            continue
        print(f"  manifest: {r['manifest_path']}")
        print(f"  layers={r['layer_count']}  "
              f"src={r['source_resolution']} → tgt={r['target_resolution']}  "
              f"mode={r['scale_mode']} bleed={r['bleed_pct']}%")
        print(f"  soe_skipped={r['soe_skipped']}")
        total = r['python_total_ms']
        print(f"  {'PHASE':<22} {'ms':>10} {'%':>6}")
        for ph, ms in r['phases_ms'].items():
            pct = (ms / total * 100.0) if total > 0 else 0.0
            print(f"  {ph:<22} {ms:>10.3f} {pct:>5.1f}%")
        print(f"  {'TOTAL':<22} {total:>10.3f}  100.0%")
        results.append(r)
        print()

    summary = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": results,
    }
    out_json = OUTPUT_DIR / "slot-17-profiling-baseline.data.json"
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[slot-17] summary written: {out_json.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
