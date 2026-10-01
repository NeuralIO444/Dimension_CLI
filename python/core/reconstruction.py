# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Stage 4 — Reconstruction planner (pure).

Consumes analysis (groups from Stage 2, style from Stage 3) + arbitrary
TargetSpec to produce plan for new editable .aep structure.

Additive; falls back gracefully. Actual emission in JSX / chunks (future).
Uses shared layer_utils for consistency.

See Stage_4.md spec for full requirements (review integration, editability).
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

from core.layer_utils import group_centroids_from_manifest, is_structural, spatial_group_key

# Stage 4: Align with models.target.Target for consistency
# TargetSpec is lightweight for planner; can hydrate from full Target.


@dataclass
class TargetSpec:
    width: int
    height: int
    duration: Optional[float] = None
    fps: Optional[float] = None
    name: str = "Custom"
    safe_zones: Optional[dict] = None
    pixel_aspect: float = 1.0
    metadata: dict = field(default_factory=dict)
    # Can be extended from models.target.Target


@dataclass
class ReconstructionDecision:
    group_key: str
    suggested_anchor: str  # top/center etc or style-adjusted
    notes: str = ""
    source_layers: List[int] = field(default_factory=list)


@dataclass
class ReconstructionPlan:
    target: TargetSpec
    decisions: List[ReconstructionDecision] = field(default_factory=list)
    new_comps: List[Dict[str, Any]] = field(default_factory=list)  # suggested new comps
    # meta for injection / editable output
    editable_notes: str = "Reconstruction produces fresh comp(s) for editability."


def plan_reconstruction(manifest: Any,
                        target_spec: Optional[TargetSpec] = None) -> ReconstructionPlan:
    """
    Pure planner. Does not mutate manifest.
    Uses existing groups from manifest (or engine).
    """
    if target_spec is None:
        # fallback from meta or default
        target_spec = TargetSpec(
            width=getattr(getattr(manifest, 'project_info', None), 'width', 1080),
            height=getattr(getattr(manifest, 'project_info', None), 'height', 1920),
        )

    decisions = []

    layers = getattr(manifest, 'layers', []) or []
    # Use shared group centroid helper (now central in layer_utils)
    group_centroids = group_centroids_from_manifest(manifest)
    # Spatial bins for finer recon decisions (avoids lumping distant same-tag layers)
    spatial_bins: dict = {}
    for layer in layers:
        if is_structural(layer):
            continue
        sk = spatial_group_key(layer)
        spatial_bins.setdefault(sk, []).append(getattr(layer, "index", -1))

    if not group_centroids:
        # fallback: treat each layer individually if no groups
        for layer in layers:
            if is_structural(layer):  # use shared
                continue
            sk = spatial_group_key(layer)
            key = f"{sk[0]}:{sk[1]}:bin{sk[2]}"
            decisions.append(ReconstructionDecision(
                group_key=key,
                suggested_anchor="center",
                notes="layer (no group, spatial)",
                source_layers=[getattr(layer, "index", -1)]
            ))
    else:
        for (comp_id, tag), (cx, cy) in group_centroids.items():
            key = f"{comp_id}:{tag}"
            anchor = "center"
            note = "group centroid"
            bins_here = [k[2] for k in spatial_bins if k[0] == comp_id and k[1] == tag]
            if bins_here:
                note += f" (spatial bins {sorted(set(bins_here))})"
            if target_spec and getattr(target_spec, 'height', 0) > getattr(target_spec, 'width', 0) * 1.5:
                if "center" in str(anchor):
                    anchor = "top"
                    note += " (vertical target bias)"
            decisions.append(ReconstructionDecision(
                group_key=key,
                suggested_anchor=anchor,
                notes=note,
                source_layers=[]  # populated upstream if needed
            ))

    plan = ReconstructionPlan(
        target=target_spec,
        decisions=decisions,
        editable_notes="Stage 4 — reconstruction produces fresh comp(s) for editability."
    )

    # Enhanced: timing scaling (robust fallback; source duration may be in manifest or layer data)
    src_info = getattr(manifest, 'project_info', None) or {}
    src_dur = getattr(src_info, 'duration', None) or getattr(src_info, 'duration_seconds', None) or 30.0
    if isinstance(src_dur, dict):  # defensive
        src_dur = src_dur.get('value', 30.0)
    src_dur = float(src_dur) if src_dur else 30.0
    if getattr(target_spec, 'duration', None) and src_dur > 0:
        target_fps = getattr(target_spec, 'fps', None)
        if target_fps:
            # #416 -- frame-accurate NTSC-rational snapping via
            # FramerateEngine, replacing the naive duration ratio below.
            # plan_conformance() snaps the SOURCE's own real-time duration
            # to the target framerate's frame raster -- it deliberately
            # ignores target_spec.duration, per ADR-02's invariant that
            # layer time-stretch is NEVER applied (animation always plays
            # at natural real-world speed). Only engages when a target fps
            # is actually specified; the untouched primary 265-target
            # catalog path (which sets neither fps nor duration) never
            # reaches this branch.
            from core.framerate_engine import FramerateEngine
            src_fps = getattr(src_info, 'fps', None) or 30.0
            fr_plan = FramerateEngine.plan_conformance(
                source_fps=float(src_fps),
                source_duration_s=src_dur,
                target_fps=float(target_fps),
            )
            scale = fr_plan.target_duration_s / src_dur
            plan.editable_notes += (
                f" | Duration scaled {scale:.2f}x (src~{src_dur}s)"
                f" — frame-snapped to {fr_plan.target_fps:.3f}fps"
                f" ({fr_plan.target_frame_count} frames"
                f"{', drop-frame' if fr_plan.is_drop_frame else ''})"
            )
        else:
            # No target fps specified -- unchanged legacy ratio.
            scale = target_spec.duration / src_dur
            plan.editable_notes += f" | Duration scaled {scale:.2f}x (src~{src_dur}s)"
        for d in decisions:
            d.notes += f" (timing x{scale:.2f})"

    # Always ensure at least a root new comp suggestion for editable output (Stage 4)
    if not plan.new_comps:
        suggested = []
        try:
            suggested = [getattr(l, 'index', -1) for l in (getattr(manifest, 'layers', []) or [])[:5]]
        except Exception:
            pass
        plan.new_comps.append({
            "name": f"Recon_{getattr(target_spec, 'name', 'Target')}_{target_spec.width}x{target_spec.height}",
            "width": target_spec.width,
            "height": target_spec.height,
            "duration": getattr(target_spec, 'duration', None),
            "fps": getattr(target_spec, 'fps', None),
            "suggested_layers": suggested,
            "notes": "Base target comp for reconstruction (edit and refine in AE)"
        })

    return plan


def emit_reconstruction_plan_as_sidecar(manifest_path: str, plan: ReconstructionPlan) -> str:
    """Write plan JSON sidecar next to manifest (for injection / review / JSX)."""
    import json
    import os
    base, _ = os.path.splitext(manifest_path)
    out = base + "_recon_plan.json"
    data = {
        "target": plan.target.__dict__ if hasattr(plan.target, "__dict__") else vars(plan.target),
        "decisions": [d.__dict__ for d in plan.decisions],
        "new_comps": getattr(plan, "new_comps", []),
        "notes": getattr(plan, "editable_notes", ""),
    }
    with open(out, "w") as f:
        json.dump(data, f, indent=2, default=str)
    return out


