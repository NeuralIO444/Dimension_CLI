#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/scripts/slot_11_5_capture_baselines.py

Slot 11.5 Phase 1 — Conform-output baseline capture for 87N.

Runs the conform pipeline analytically against the latest archived 87N
scrape manifest for each of four target specs (narrow, widen, preserve,
equal_different_resolution). Writes per-rule-set artifacts into
docs/roadmap/slot-11-5-baselines/87n/<rule_set>/.

Bypasses the preset library, the orchestrator UI, the file-bridge, and
After Effects entirely — all four conforms run in-process,
deterministically, against the same scrape input. Output is byte-stable
across runs (same scrape in → same four manifests out).

Mirrors the headless pipeline assembly — scale → lerp → write manifest →
write report. The slice step is omitted because the AE chunk manifests
aren't needed for judgment-doc population.

The effect/layer-style conform stage was removed in DCE Phase 2a
(2026-08-19) along with core/effect_conformer.py. The four committed
baselines under docs/roadmap/slot-11-5-baselines/ were captured with
with_conformed_effects=0, so they are unaffected. A re-run against a
newer archived scrape will now omit conformed_effects /
conformed_layer_styles — that is intended: those values were computed by
math CLAUDE.md documents as wrong (effects live in LAYER space and are
already scaled by the layer transform), and the live conform stage has
had its effects block disabled since 2026-07-02. Dropping the stage makes
this capture match the pipeline it claims to mirror.

Usage (from repo root):
    python3 python/scripts/slot_11_5_capture_baselines.py
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

# Make python/ importable when run from anywhere.
_HERE = Path(__file__).resolve().parent
_PYTHON_ROOT = _HERE.parent
sys.path.insert(0, str(_PYTHON_ROOT))

from core.classify import (  # noqa: E402
    detect_3d_camera_scene,
    is_layer_root,
    should_scale_z,
)
from core.io_utils import atomic_write_json, verify_manifest_hash  # noqa: E402
from core.lerp_engine import LerpEngine  # noqa: E402
from core.logger import log  # noqa: E402
from core.property_router import merge_legacy_and_v5  # noqa: E402
from core.scale_engine import ScaleEngine  # noqa: E402
from logic.report_generator import generate_report  # noqa: E402
from models.scrape_manifest import ScrapeManifest  # noqa: E402


REPO_ROOT = _PYTHON_ROOT.parent
BASELINES_ROOT = REPO_ROOT / "docs" / "roadmap" / "slot-11-5-baselines" / "87n"
DIMENSION_LOG = REPO_ROOT / "logs" / "dimension.log"
ARCHIVE_ROOT = REPO_ROOT / "logs" / "archive"

EXPECTED_COMP = "87N_Reels_DEV_87Neon_Anima_HD_01_mc"

# Four target specs covering each rule set the conform pipeline classifies.
# Bleed mirrors real preset behavior (TikTok ships with 5%; the other three
# presets ship with 0%). Per Slot 11.5 Phase 1 scope: document what real
# presets exercise, not synthetic uniform-bleed conditions.
TARGET_SPECS = [
    {"rule_set": "narrow",
     "width": 1080, "height": 1920, "mode": "Fit", "bleed_pct": 5.0},
    {"rule_set": "widen",
     "width": 4096, "height": 2160, "mode": "Fit", "bleed_pct": 0.0},
    {"rule_set": "preserve",
     "width": 1920, "height": 1080, "mode": "Fit", "bleed_pct": 0.0},
    {"rule_set": "equal_different_resolution",
     "width": 3840, "height": 2160, "mode": "Fit", "bleed_pct": 0.0},
]


def find_latest_scrape_for_comp(comp_name: str) -> Path:
    """Return the most-recent archived scrape_manifest.json whose
    project_info.name matches comp_name. Mtime-sorted across all sessions."""
    if not ARCHIVE_ROOT.is_dir():
        raise FileNotFoundError(f"No archive dir at {ARCHIVE_ROOT}")
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
        if (data.get("project_info") or {}).get("name") == comp_name:
            return c
    raise FileNotFoundError(
        f"No archived scrape manifest matches comp {comp_name!r}"
    )


def read_active_profile():
    """Return (profile_obj, profile_id_str). profile_id_str is None when
    no profile is active or the registry fails to load. Mirrors the
    pattern in python/__main__.py — do NOT force-set a profile; surface
    whatever's active so the captures reflect real conditions."""
    try:
        from logic.studio_profile_registry import REGISTRY as _SPR
        prof = _SPR.get_active()
        if prof is None:
            return None, None
        pid = (
            getattr(prof, "id", None)
            or getattr(prof, "display_name", None)
        )
        return prof, pid
    except Exception as e:  # noqa: BLE001 — registry failures must not
                            # block the capture run; surface the issue
                            # and proceed with heuristic-only conform.
        log.warning(
            "Slot 11.5 baseline capture: could not read active profile",
            extra={"err": str(e)},
        )
        return None, None


def run_conform_for_target(
    manifest: ScrapeManifest,
    target: dict,
    active_profile,
) -> tuple[dict, float]:
    """Run the full Python conform pipeline for one target spec.

    Returns (result_dict, uniform_scale_S). The result dict is the
    ScaleEngine output augmented with per-layer conformed_keys,
    conformed_effects, and conformed_layer_styles — same shape the
    UI/CLI pipeline produces before slicing.
    """
    target_w = target["width"]
    target_h = target["height"]
    scale_mode = target["mode"]
    bleed_pct = target["bleed_pct"]

    # Stage 1 — Scale + gravity
    engine = ScaleEngine(
        manifest, target_w, target_h, scale_mode,
        bleed_pct / 100.0, profile=active_profile,
    )
    result = engine.conform()

    # Stage 2 — Keyframe LERP (mirrors python/__main__.py)
    lerp = LerpEngine()
    s_factor = result["scale"]["S"]
    layer_indices = {l.index for l in manifest.layers}
    is_3d_camera_scene = detect_3d_camera_scene(manifest.layers)

    src_center = [
        manifest.project_info.width / 2.0,
        manifest.project_info.height / 2.0,
    ]
    tgt_center = [target_w / 2.0, target_h / 2.0]

    src_w = manifest.project_info.width
    src_h = manifest.project_info.height
    k_factor = max(
        target_w / src_w if src_w > 0 else 1.0,
        target_h / src_h if src_h > 0 else 1.0,
    )
    k_for_keys = k_factor if is_3d_camera_scene else None

    for i, layer_dict in enumerate(result["layers"]):
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
                src_center=src_center, tgt_center=tgt_center,
                scale_z=should_scale_z(src, is_3d_camera_scene),
                K=k_for_keys,
                layer_kind=layer_kind,
            )
            layer_dict["conformed_keys"] = ck.model_dump(exclude_none=True)
        else:
            layer_dict["conformed_keys"] = None

    return result, s_factor


def capture_log_slice(start_size: int, max_lines: int = 100) -> str:
    """Read everything appended to dimension.log since byte offset
    start_size; return the last max_lines lines of it (or all if fewer)."""
    if not DIMENSION_LOG.is_file():
        return ""
    try:
        with open(DIMENSION_LOG, "rb") as f:
            f.seek(start_size)
            new_bytes = f.read()
        new_text = new_bytes.decode("utf-8", errors="replace")
        lines = new_text.splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        return "\n".join(lines)
    except OSError as e:
        log.warning(
            "Slot 11.5: could not read dimension.log slice",
            extra={"err": str(e)},
        )
        return ""


def clean_baseline_dir(rule_set: str) -> Path:
    """Idempotency: remove prior capture artifacts before re-writing.
    Preserves README.md (scaffolded earlier) and any user-dropped
    screenshot files."""
    d = BASELINES_ROOT / rule_set
    d.mkdir(parents=True, exist_ok=True)
    for fname in (
        "conformed_manifest.json",
        "conform_report.html",
        "log-slice.txt",
        "capture_metadata.json",
    ):
        p = d / fname
        if p.is_file():
            p.unlink()
    return d


def main():
    print(f"[slot-11.5] capture starting at "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Resolve source scrape
    try:
        scrape_path = find_latest_scrape_for_comp(EXPECTED_COMP)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    rel_scrape = scrape_path.relative_to(REPO_ROOT)
    print(f"Source scrape:  {rel_scrape}")

    # Hash sidecar (informational)
    try:
        verify_manifest_hash(str(scrape_path))
        print("Hash sidecar:   verified")
    except FileNotFoundError:
        print("Hash sidecar:   absent (AE-panel scrape — expected)")
    except ValueError as ve:
        print(f"Hash sidecar:   MISMATCH — {ve}", file=sys.stderr)
        sys.exit(3)

    # Load + validate
    raw_data = json.loads(scrape_path.read_text(encoding="utf-8"))
    manifest = ScrapeManifest.model_validate(raw_data)
    print(f"Comp:           {manifest.project_info.name}")
    print(f"Source res:     "
          f"{manifest.project_info.width}x{manifest.project_info.height}")
    print(f"Layer count:    {len(manifest.layers)}")

    # Active profile (do not force-set; surface whatever's active)
    active_profile, profile_id = read_active_profile()
    print(f"Active profile: {profile_id or '(none — heuristic-only)'}")
    print()

    # Run each target
    for target in TARGET_SPECS:
        rule_set = target["rule_set"]
        target_w = target["width"]
        target_h = target["height"]
        scale_mode = target["mode"]
        bleed_pct = target["bleed_pct"]

        baseline_dir = clean_baseline_dir(rule_set)
        print(f"=== {rule_set} "
              f"({target_w}x{target_h} {scale_mode} bleed={bleed_pct}%) ===")

        # Snapshot log size BEFORE the pipeline runs so the slice captures
        # only this target's emissions.
        log_start = (
            DIMENSION_LOG.stat().st_size if DIMENSION_LOG.is_file() else 0
        )

        result, s_factor = run_conform_for_target(
            manifest, target, active_profile
        )

        # Write conformed_manifest.json
        conformed_path = baseline_dir / "conformed_manifest.json"
        atomic_write_json(str(conformed_path), result)

        # Write conform_report.html
        report_path = baseline_dir / "conform_report.html"
        session_id = uuid.uuid4().hex[:8].upper()
        generate_report(
            conformed_manifest=result,
            scrape_manifest=raw_data,
            preset_label=f"baseline-{rule_set}",
            target_w=target_w,
            target_h=target_h,
            scale_mode=scale_mode,
            uniform_scale=s_factor,
            session_id=session_id,
            output_path=str(report_path),
        )

        # Capture log slice
        log_slice = capture_log_slice(log_start, max_lines=100)
        (baseline_dir / "log-slice.txt").write_text(
            log_slice, encoding="utf-8"
        )

        # Provenance metadata — not in the conformed_manifest itself
        metadata = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source_manifest": str(rel_scrape),
            "comp": manifest.project_info.name,
            "source_width": manifest.project_info.width,
            "source_height": manifest.project_info.height,
            "target": {
                "rule_set": rule_set,
                "width": target_w,
                "height": target_h,
                "scale_mode": scale_mode,
                "bleed_pct": bleed_pct,
            },
            "active_profile": profile_id,
            "session_id": session_id,
            "uniform_scale_S": s_factor,
            "aspect_strategy": result.get("aspect_strategy"),
            "aspect_ar_delta_pct": result.get("aspect_ar_delta_pct"),
            "screenshot": "pending — manual capture in AE",
        }
        atomic_write_json(
            str(baseline_dir / "capture_metadata.json"), metadata
        )

        print(f"  → conformed_manifest.json    "
              f"({conformed_path.stat().st_size} bytes)")
        print(f"  → conform_report.html        "
              f"({report_path.stat().st_size} bytes)")
        print(f"  → log-slice.txt              "
              f"({len(log_slice.splitlines())} lines)")
        print("  → capture_metadata.json")
        print(f"  aspect_strategy:  {result.get('aspect_strategy')}")
        print(f"  uniform scale S:  {s_factor:.4f}")
        print()

    print("[slot-11.5] capture complete.")


if __name__ == "__main__":
    main()
