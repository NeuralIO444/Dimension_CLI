# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/verify_safe_zone_compliance.py — does the conformed manifest
actually clear the platform chrome? (partial #420)

WHAT THIS PROVES, AND WHAT IT DOES NOT
--------------------------------------
The Spatial Occlusion Engine computes per-layer corrections and writes them
to `soe_corrections.json`. The HTML report renders that file. But that file
is the engine's *plan*, recorded at the moment SOE ran — several passes
before the manifest is finalised. Nothing checks that the plan survived.

This tool re-derives each layer's world bounds from its FINAL conformed
position and re-classifies them against the same mask SOE used. It answers:

    "Does the delivered manifest actually place this layer clear of the
     caption bar, or did a later pass quietly undo what SOE decided?"

That is a real and currently-unasked question. It is NOT, however, the
headline claim in #420. Two verifications exist and only the second proves
a deliverable:

  1. MANIFEST-LEVEL (this tool) — pure Python, headless, runs in CI. Proves
     the manifest is right. Cannot prove After Effects received it.
  2. POST-INJECT (#420 proper) — needs a live AE session and a working
     Auditor. Proves the delivered file is right.

(2) is blocked: `Auditor.runFull` has no caller at all (#427), so adding
checks there today would ship dead code. This tool is the honest half that
can be built and verified now, and it is deliberately labelled as the
weaker claim rather than sold as compliance proof.

WHY IT IS A TOOL, NOT A PIPELINE PASS
-------------------------------------
Adding a verification pass inside `stages/conform.py` would be a
conform-pipeline contract change — a CLAUDE.md stop condition needing
explicit approval. Shipping it as read-only tooling over the artifacts the
pipeline already writes delivers the same signal with none of that risk,
and can be promoted into the pipeline later if it earns its place.

USAGE
-----
    python -m tools.verify_safe_zone_compliance \\
        --conformed .dimension/conformed_manifest.json \\
        --corrections .dimension/soe_corrections.json \\
        --preset builtin:sm_tiktok_video

Exit code is 0 when every layer's final position agrees with its recorded
plan, 1 when a discrepancy is found (so it can gate a script if wanted).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.occlusion.mask_solver import OcclusionMask, compute_world_bounds  # noqa: E402

# Zone names the mask classifier returns, worst-first.
_SEVERITY = {"CUTOFF": 3, "NUDGE": 2, "GO": 1, "CLEAR": 1, "UNKNOWN": 0}


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _resolve_target_dims(conformed: Dict[str, Any]) -> tuple:
    """Target comp dimensions, which the mask must be sized against.

    Checked in order of how directly each names the CONFORM TARGET. A
    conformed manifest can still carry the SOURCE comp's dimensions in
    `project_info` (source carryover — see CLAUDE.md's "parallel views"
    sharp edge), so target-specific keys are tried first and
    `project_info` is the last resort, not the first.
    """
    for wk, hk in (
        ("target_width", "target_height"),
        ("comp_width", "comp_height"),
    ):
        w, h = conformed.get(wk), conformed.get(hk)
        if w and h:
            return int(w), int(h)
    tgt = conformed.get("target") or {}
    if tgt.get("width") and tgt.get("height"):
        return int(tgt["width"]), int(tgt["height"])
    info = conformed.get("project_info") or {}
    return int(info.get("width") or 0), int(info.get("height") or 0)


def _iter_layers(conformed: Dict[str, Any]) -> List[Dict[str, Any]]:
    layers = conformed.get("layers")
    return layers if isinstance(layers, list) else []


def _final_bounds(layer: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """World bounds from the FINAL conformed transform.

    Deliberately reads `conformed_transforms`, never the source-carryover
    fields sitting beside them. CLAUDE.md's "parallel views" sharp edge
    documents that reading the wrong view produces confident, wrong
    conclusions — it has already caused two false-alarm bug reports.
    """
    ct = layer.get("conformed_transforms") or {}
    position = ct.get("position")
    anchor = ct.get("anchor") or layer.get("anchor")
    scale = ct.get("scale")
    source_rect = layer.get("source_rect")
    rotation = float(ct.get("rotation_z") or layer.get("rotation_z") or 0.0)
    if not (position and anchor and scale and source_rect):
        return None
    return compute_world_bounds(position, anchor, scale, source_rect, rotation_deg=rotation)


def verify(
    conformed: Dict[str, Any],
    corrections: List[Dict[str, Any]],
    mask: OcclusionMask,
) -> Dict[str, Any]:
    """Re-classify each corrected layer's final bounds against the mask."""
    by_uid: Dict[str, Dict[str, Any]] = {}
    by_index: Dict[int, Dict[str, Any]] = {}
    for c in corrections:
        if c.get("layer_uid"):
            by_uid[str(c["layer_uid"])] = c
        if c.get("layer_index") is not None:
            by_index[int(c["layer_index"])] = c

    checked = 0
    skipped: List[Dict[str, Any]] = []
    disagreements: List[Dict[str, Any]] = []

    for layer in _iter_layers(conformed):
        uid = layer.get("uid")
        idx = layer.get("index")
        rec = None
        if uid and str(uid) in by_uid:
            rec = by_uid[str(uid)]
        elif idx is not None and int(idx) in by_index:
            rec = by_index[int(idx)]
        if rec is None:
            continue  # SOE never touched this layer; nothing was promised

        planned = str(rec.get("zone_hit") or "UNKNOWN").upper()
        # SKIPPED_* strategies promise nothing — SOE explicitly declined.
        strategy = str(rec.get("strategy") or "")
        if strategy.startswith("SKIPPED") or strategy == "SOE_FAILED":
            skipped.append({
                "layer": layer.get("name"),
                "strategy": strategy,
                "reason": "SOE declined this layer; no placement was promised",
            })
            continue

        bounds = _final_bounds(layer)
        if bounds is None:
            skipped.append({
                "layer": layer.get("name"),
                "strategy": strategy,
                "reason": "insufficient transform data to derive world bounds",
            })
            continue

        actual_raw = mask.classify_aabb(bounds["l"], bounds["t"], bounds["r"], bounds["b"])
        actual = str(actual_raw.get("centroid_zone") or "UNKNOWN").upper()
        checked += 1

        # A disagreement that matters is one where reality is WORSE than
        # the plan. Reality better than planned is a bonus, not a defect.
        if _SEVERITY.get(actual, 0) > _SEVERITY.get(planned, 0):
            disagreements.append({
                "layer": layer.get("name"),
                "uid": uid,
                "content_tag": layer.get("content_tag"),
                "planned_zone": planned,
                "actual_zone": actual,
                "strategy": strategy,
                "final_bounds": {k: round(v, 2) for k, v in bounds.items()},
                "cutoff_px": actual_raw.get("overlap_cutoff_px"),
            })

    return {
        "layers_with_soe_records": len(corrections),
        "layers_checked": checked,
        "layers_skipped": skipped,
        "disagreements": disagreements,
        "compliant": not disagreements,
    }


def _render_human(report: Dict[str, Any]) -> str:
    out: List[str] = []
    out.append("Safe-zone manifest compliance (partial #420)")
    out.append("=" * 60)
    out.append(f"  SOE records        : {report['layers_with_soe_records']:>4}")
    out.append(f"  layers re-checked  : {report['layers_checked']:>4}")
    out.append(f"  skipped by SOE     : {len(report['layers_skipped']):>4}")
    out.append("")

    if report["compliant"]:
        out.append("  PASS — every re-checked layer's final position is no worse")
        out.append("  than the zone SOE recorded for it.")
    else:
        out.append(f"  FAIL — {len(report['disagreements'])} layer(s) end up in a WORSE zone")
        out.append("  than SOE's plan recorded. A later pass moved them back in.")
        out.append("")
        for d in report["disagreements"]:
            out.append(f"    {d['layer']}")
            out.append(
                f"      planned {d['planned_zone']} -> actual {d['actual_zone']}"
                f"   (strategy: {d['strategy']}, tag: {d['content_tag']})"
            )
    out.append("")
    out.append("  SCOPE: this proves the MANIFEST is right. It cannot prove After")
    out.append("  Effects received it — that needs post-inject audit (#420/#427).")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="verify_safe_zone_compliance")
    ap.add_argument("--conformed", required=True, help="path to conformed_manifest.json")
    ap.add_argument("--corrections", required=True, help="path to soe_corrections.json")
    ap.add_argument("--mask", required=True, help="path to the safe-zone mask PNG")
    ap.add_argument("--comp-width", type=int, default=None,
                    help="override target width (default: read from the manifest)")
    ap.add_argument("--comp-height", type=int, default=None,
                    help="override target height (default: read from the manifest)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args(argv)

    conformed = _load_json(Path(args.conformed))
    corr_raw = _load_json(Path(args.corrections))
    corrections = corr_raw.get("corrections", corr_raw) if isinstance(corr_raw, dict) else corr_raw
    if not isinstance(corrections, list):
        corrections = []

    comp_w, comp_h = _resolve_target_dims(conformed)
    # Explicit flags win over anything inferred from the manifest.
    if args.comp_width:
        comp_w = args.comp_width
    if args.comp_height:
        comp_h = args.comp_height
    if comp_w <= 0 or comp_h <= 0:
        print(
            "error: could not determine target dimensions from the conformed "
            "manifest (looked at target_width/target_height, comp_width/"
            "comp_height, target.width/height, then project_info). "
            "Pass --comp-width/--comp-height explicitly.",
            file=sys.stderr,
        )
        return 2

    mask = OcclusionMask(Path(args.mask), comp_w, comp_h)

    report = verify(conformed, corrections, mask)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_render_human(report))
    return 0 if report["compliant"] else 1


if __name__ == "__main__":
    sys.exit(main())
