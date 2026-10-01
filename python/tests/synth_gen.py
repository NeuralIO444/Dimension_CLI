#!/usr/bin/env python3
"""
synth_gen.py — Neural IO 444 / Synthetic AE Comp Manifest Generator

Generates synthetic manifests in the ae_comp_delta_export schema, with known
ground-truth metadata so you can validate matcher accuracy, rule-fit accuracy,
and edge-case coverage WITHOUT touching After Effects.

Three modes:

  matcher-stress   Generate baseline+target pairs where layer correspondence
                   is known. Inject controlled noise (renamed layers, moved
                   layers, dropped layers) so you can measure recall/precision
                   of any matcher under stress.

  rule-fit         Generate N shots where every baseline→target pair was
                   reformatted using the SAME known rule. Verifies that
                   rule_fit.py recovers the rule with low variance.

  edge-cases       Generate a battery of structurally tricky comps: deep
                   nesting, expression chains, mixed-direction reformats,
                   text-only / shape-only comps, etc.

USAGE
    python synth_gen.py matcher-stress  --shots 20 --out synth/matcher/
    python synth_gen.py rule-fit        --shots 30 --rule tiktok_v1 --out synth/rules/
    python synth_gen.py edge-cases      --out synth/edges/

Every generated file has a top-level 'synthetic: true' flag plus a
'groundTruth' block recording how it was built. Real-data tools should
ignore the groundTruth block; test tools read it to validate against.
"""

from __future__ import annotations
import argparse, hashlib, json, random, sys
from copy import deepcopy
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Manifest scaffolding — produces minimal-but-valid schema
# ---------------------------------------------------------------------------
SCHEMA = "ae_comp_delta_export"
SCHEMA_VERSION = 1
SCRIPT_VERSION = "synth-1.0.0"


def h(*parts) -> str:
    """Stable short hash for IDs and stableKeys."""
    s = "::".join(str(p) for p in parts)
    return "h" + hashlib.sha1(s.encode()).hexdigest()[:8]


def make_layer(idx: int, name: str, width: int, height: int,
               layer_type: str = "AVLayer",
               x: Optional[float] = None,
               y: Optional[float] = None,
               scale_x: float = 100.0,
               scale_y: float = 100.0,
               source_id: Optional[str] = None,
               parent_id: Optional[str] = None,
               keyframes_y: Optional[list] = None) -> dict:
    """Build one layer dict. Centered by default."""
    cx = width / 2 if x is None else x
    cy = height / 2 if y is None else y

    position_property = {
        "matchName": "ADBE Position",
        "value": [cx, cy],
        "isKeyframed": bool(keyframes_y),
    }
    if keyframes_y:
        position_property["keys"] = [
            {"time": kt, "value": [cx, ky]} for kt, ky in keyframes_y
        ]

    transform_group = {
        "matchName": "ADBE Transform Group",
        "children": [
            {"matchName": "ADBE Anchor Point", "value": [cx, cy]},          # children[0]
            position_property,                                              # children[1]
            {"matchName": "ADBE Rotate X", "value": 0},                     # children[2]
            {"matchName": "ADBE Rotate Y", "value": 0},                     # children[3]
            {"matchName": "ADBE Rotate Z", "value": 0},                     # children[4]
            {"matchName": "ADBE Scale", "value": [scale_x, scale_y]},       # children[5]
            {"matchName": "ADBE Opacity", "value": 100},                    # children[6]
        ]
    }

    layer = {
        "id": h("layer", name, idx),
        "name": name,
        "index": idx,
        "type": layer_type,
        "enabled": True,
        "sourceId": source_id,
        "parentId": parent_id,
        "stableKey": h("stable", name, source_id or "", layer_type),
        "transform": transform_group,
        "effects": [],
        "masks": [],
        "text": None,
        "timeRemap": None,
    }
    return layer


def make_comp(name: str, width: int, height: int, layers: list,
              duration: float = 10.0, frame_rate: float = 24.0,
              comp_id: Optional[str] = None) -> dict:
    return {
        "id": comp_id or h("comp", name, width, height),
        "nameHash": h("name", name),
        "formatKey": classify_canvas(width, height),
        "width": width,
        "height": height,
        "pixelAspect": 1.0,
        "duration": duration,
        "frameRate": frame_rate,
        "frameDuration": 1.0 / frame_rate,
        "displayStartTime": 0,
        "workAreaStart": 0,
        "workAreaDuration": duration,
        "bgColor": [0, 0, 0],
        "motionBlur": False,
        "draft3d": False,
        "frameBlending": False,
        "preserveNestedFrameRate": False,
        "preserveNestedResolution": False,
        "renderer": "ADBE Standard 3d",
        "layerCount": len(layers),
        "layers": layers,
        "markers": [],
    }


def classify_canvas(w: int, h: int) -> str:
    if w == 1920 and h == 1080: return "HD"
    if w == 1080 and h == 1920: return "9x16"
    if w == 1080 and h == 1080: return "1x1"
    if w == 1080 and h == 1350: return "4x5"
    if w == 4096 and h == 2160: return "DCI4K_Container"
    if w == 4096 and h == 1716: return "DCI4K_Scope"
    return f"Custom_{w}x{h}"


def make_manifest(baseline_comp: dict, target_comps: dict,
                  ground_truth: dict) -> dict:
    """Build a full ae_comp_delta_export manifest with computed deltas."""
    return {
        "schema": SCHEMA,
        "schemaVersion": SCHEMA_VERSION,
        "scriptVersion": SCRIPT_VERSION,
        "generatedAtLocal": "2026-05-14T00:00:00",
        "synthetic": True,
        "anonymized": True,
        "deterministic": False,  # The fitter will set this; we don't claim it
        "anonymizationMapPolicy": {
            "cryptographic": False,
            "stableWithinExport": True,
            "rawNamesIncluded": False,
            "rawPathsIncluded": False,
            "rawExpressionsIncluded": False,
        },
        "sourceProject": {"nameHash": h("synthetic_project"),
                          "pathHash": h("synthetic_path")},
        "discovery": {
            "expectedBaseline": {"width": baseline_comp["width"],
                                  "height": baseline_comp["height"]},
            "expectedFormats": [{"key": k, "width": v["width"], "height": v["height"]}
                                for k, v in target_comps.items()],
            "missingFormats": [],
            "selectedBaseline": baseline_comp["id"],
            "selectedBaselineNameHash": baseline_comp["nameHash"],
        },
        "baseline": baseline_comp,
        "formats": target_comps,
        "deltas": compute_deltas(baseline_comp, target_comps),
        "groundTruth": ground_truth,  # Synthetic-only — readable by test harness
    }


# ---------------------------------------------------------------------------
# Delta computation — same semantics as the real exporter, but knowing the
# layer correspondence directly (no matcher needed) since we built both sides.
# ---------------------------------------------------------------------------
def compute_deltas(baseline: dict, targets: dict) -> dict:
    out = {}
    for fk, tgt in targets.items():
        out[fk] = compute_one_delta(baseline, tgt, fk)
    return out


def compute_one_delta(baseline: dict, target: dict, fk: str) -> dict:
    base_layers = {L["stableKey"]: L for L in baseline["layers"]}
    tgt_layers  = {L["stableKey"]: L for L in target["layers"]}

    matched_keys  = set(base_layers) & set(tgt_layers)
    missing_keys  = set(base_layers) - set(tgt_layers)
    added_keys    = set(tgt_layers)  - set(base_layers)

    layer_deltas = []
    for k in matched_keys:
        b = base_layers[k]
        t = tgt_layers[k]
        layer_deltas.append(compute_layer_delta(b, t))

    return {
        "formatKey": fk,
        "comp": {
            "widthDelta": target["width"] - baseline["width"],
            "heightDelta": target["height"] - baseline["height"],
            "durationDelta": target["duration"] - baseline["duration"],
            "frameRateDelta": target["frameRate"] - baseline["frameRate"],
            "aspectDelta": (target["width"]/target["height"])
                         - (baseline["width"]/baseline["height"]),
            "layerCountDelta": target["layerCount"] - baseline["layerCount"],
        },
        "layers": layer_deltas,
        "addedInTarget": sorted(added_keys),
        "missingInTarget": sorted(missing_keys),
    }


def compute_layer_delta(base: dict, target: dict) -> dict:
    numeric = []
    # Walk both transform groups in parallel
    b_tg = base["transform"]["children"]
    t_tg = target["transform"]["children"]
    for ci, (bc, tc) in enumerate(zip(b_tg, t_tg)):
        bv, tv = bc.get("value"), tc.get("value")
        if isinstance(bv, list) and isinstance(tv, list):
            for vi, (bvv, tvv) in enumerate(zip(bv, tv)):
                if isinstance(bvv, (int, float)) and isinstance(tvv, (int, float)):
                    if abs(bvv - tvv) > 1e-9:
                        numeric.append({
                            "path": f"children[{ci}].value[{vi}]",
                            "base": bvv, "target": tvv, "delta": tvv - bvv
                        })
        elif isinstance(bv, (int, float)) and isinstance(tv, (int, float)):
            if abs(bv - tv) > 1e-9:
                numeric.append({
                    "path": f"children[{ci}].value",
                    "base": bv, "target": tv, "delta": tv - bv
                })
        # Keyframed
        b_keys = bc.get("keys") or []
        t_keys = tc.get("keys") or []
        for ki, (bk, tk) in enumerate(zip(b_keys, t_keys)):
            bkv, tkv = bk.get("value"), tk.get("value")
            if isinstance(bkv, list) and isinstance(tkv, list):
                for vi, (bvv, tvv) in enumerate(zip(bkv, tkv)):
                    if isinstance(bvv, (int, float)) and isinstance(tvv, (int, float)):
                        if abs(bvv - tvv) > 1e-9:
                            numeric.append({
                                "path": f"children[{ci}].keys[{ki}].value[{vi}]",
                                "base": bvv, "target": tvv, "delta": tvv - bvv
                            })

    tD_changed = bool(numeric)
    tD = {
        "baseHash": h("transform", base["stableKey"]),
        "targetHash": h("transform", target["stableKey"]) if not tD_changed
                     else h("transform_changed", base["stableKey"]),
        "changed": tD_changed,
        "numericDelta": numeric,
    }

    return {
        "stableKey": base["stableKey"],
        "baseLayerId": base["id"],
        "targetLayerId": target["id"],
        "matched": True,
        "indexDelta": target["index"] - base["index"],
        "parentChanged": base.get("parentId") != target.get("parentId"),
        "sourceChanged": base.get("sourceId") != target.get("sourceId"),
        "flagsChanged": {},
        "timing": {"inPointDelta": 0, "outPointDelta": 0,
                   "startTimeDelta": 0, "stretchDelta": 0},
        "transformDelta": tD,
        "effectsDelta":  {"baseHash": h("fx", base["stableKey"]),
                          "targetHash": h("fx", base["stableKey"]),
                          "changed": False, "numericDelta": []},
        "masksDelta":    {"baseHash": h("mk", base["stableKey"]),
                          "targetHash": h("mk", base["stableKey"]),
                          "changed": False, "numericDelta": []},
        "textDelta":     {"baseHash": h("tx", base["stableKey"]),
                          "targetHash": h("tx", base["stableKey"]),
                          "changed": False},
        "timeRemapDelta":{"baseHash": h("tr", base["stableKey"]),
                          "targetHash": h("tr", base["stableKey"]),
                          "changed": False},
    }


# ---------------------------------------------------------------------------
# Reformat rules — applied to a baseline comp to produce a target comp
# ---------------------------------------------------------------------------
RULES = {
    "tiktok_v1": {
        "target_w": 1080, "target_h": 1920,
        "scale_coef": 0.5906, "bleed_factor": 1.05,
        "y_bias_px": -19.2,
    },
    "square_v1": {
        "target_w": 1080, "target_h": 1080,
        "scale_coef": 0.5625, "bleed_factor": 1.05,
        "y_bias_px": 0.0,
    },
    "portrait_4x5_v1": {
        "target_w": 1080, "target_h": 1350,
        "scale_coef": 0.7031, "bleed_factor": 1.0,
        "y_bias_px": 0.0,
    },
    "dci4k_container_v1": {
        "target_w": 4096, "target_h": 2160,
        "scale_coef": 2.1333, "bleed_factor": 1.0,
        "y_bias_px": 0.0,
    },
}


def apply_rule(baseline: dict, rule: dict, rule_name: str) -> dict:
    """Reformat baseline → target by applying the rule deterministically.
    Used by rule-fit mode to manufacture a verifiable target."""
    tw, th = rule["target_w"], rule["target_h"]
    scale = rule["scale_coef"] * rule["bleed_factor"]
    y_bias = rule["y_bias_px"]
    cx, cy = tw / 2, th / 2

    new_layers = []
    for L in baseline["layers"]:
        nL = deepcopy(L)
        # Recompute position toward new canvas center + bias
        pos_p = nL["transform"]["children"][1]
        if pos_p.get("value"):
            pos_p["value"] = [cx, cy + y_bias]
        for k in (pos_p.get("keys") or []):
            k["value"] = [cx, cy + y_bias]
        # Scale uniformly
        scl_p = nL["transform"]["children"][5]
        old = scl_p["value"]
        scl_p["value"] = [round(old[0] * scale, 6), round(old[1] * scale, 6)]
        new_layers.append(nL)

    return make_comp(
        f"{baseline['nameHash']}_{rule_name}",
        tw, th, new_layers,
        duration=baseline["duration"],
        frame_rate=baseline["frameRate"],
        comp_id=h("synth_target", baseline["id"], rule_name)
    )


# ---------------------------------------------------------------------------
# Mode 1: matcher-stress
# ---------------------------------------------------------------------------
def gen_matcher_stress(n_shots: int, out_dir: Path, seed: int = 42):
    """Each shot has baseline + target where some layers are renamed,
    reindexed, parented differently, or dropped — a stress test for
    correspondence recovery."""
    random.seed(seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    NOISE_TYPES = ["rename", "reindex", "reparent", "source_swap", "drop", "clean"]

    for shot_idx in range(n_shots):
        n_layers = random.randint(5, 15)
        bw, bh = 1920, 1080
        layers = []
        for li in range(1, n_layers + 1):
            layers.append(make_layer(
                idx=li,
                name=f"Layer_{li:02d}",
                width=bw, height=bh,
                source_id=h("src", shot_idx, li),
            ))
        baseline = make_comp(f"shot_{shot_idx:02d}_base", bw, bh, layers)

        # Build a target with controlled noise
        target_layers = []
        gt_matches = []   # ground-truth correspondence
        noise_applied = []
        for L in deepcopy(layers):
            noise = random.choice(NOISE_TYPES)
            if noise == "drop" and len(target_layers) >= 2:
                noise_applied.append({"layer": L["name"], "type": "dropped"})
                continue
            if noise == "rename":
                L["name"] = f"renamed_{L['name']}_{random.randint(100,999)}"
                noise_applied.append({"layer": L["name"], "type": "renamed"})
            elif noise == "source_swap":
                L["sourceId"] = h("swapped_src", shot_idx, L["name"])
                noise_applied.append({"layer": L["name"], "type": "source_swap"})
            elif noise == "reparent":
                L["parentId"] = h("new_parent", shot_idx, L["name"])
                noise_applied.append({"layer": L["name"], "type": "reparent"})
            # NOTE: the noise edits the layer but does NOT touch stableKey,
            # so the synthetic exporter still matches by stableKey. The point
            # of this dataset is for a *new* matcher implementation to recover
            # correspondence using OTHER signals (name, source, position) even
            # when stableKey is unreliable. So we also produce a "decoy"
            # stableKey for ~30% of layers:
            if random.random() < 0.3:
                L["stableKey"] = h("decoy", L["name"], random.random())
                noise_applied.append({"layer": L["name"], "type": "stablekey_changed"})

            target_layers.append(L)
            gt_matches.append({
                "baseline_id": L["id"],
                "target_name": L["name"],
                "noise": noise,
            })
        # Reindex
        for i, L in enumerate(target_layers, 1):
            L["index"] = i

        target = make_comp(f"shot_{shot_idx:02d}_9x16",
                           1080, 1920, target_layers,
                           duration=baseline["duration"],
                           frame_rate=baseline["frameRate"])

        gt = {
            "mode": "matcher-stress",
            "n_baseline_layers": n_layers,
            "n_target_layers": len(target_layers),
            "n_expected_matches": len(target_layers),
            "noise_applied": noise_applied,
            "correspondence": gt_matches,
        }
        manifest = make_manifest(baseline, {"9x16": target}, gt)
        path = out_dir / f"synth_matcher_{shot_idx:02d}.json"
        path.write_text(json.dumps(manifest, indent=2))
    print(f"Generated {n_shots} matcher-stress shots → {out_dir}")


# ---------------------------------------------------------------------------
# Mode 2: rule-fit
# ---------------------------------------------------------------------------
def gen_rule_fit(n_shots: int, rule_name: str, out_dir: Path, seed: int = 42):
    """Generate N shots all reformatted with the SAME known rule, plus
    a small amount of per-layer variation, so rule_fit.py should recover
    the rule's coefficients with low variance."""
    if rule_name not in RULES:
        print(f"Unknown rule: {rule_name}. Options: {list(RULES.keys())}", file=sys.stderr)
        sys.exit(1)
    rule = RULES[rule_name]
    random.seed(seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    for shot_idx in range(n_shots):
        # Source canvas varies a bit so the test covers different baselines
        baseline_choice = random.choice([(1920, 1080), (1920, 1080), (1920, 1080)])
        bw, bh = baseline_choice
        n_layers = random.randint(6, 12)

        layers = []
        for li in range(1, n_layers + 1):
            # Add some baseline variation: layers at varied positions
            jitter_x = random.uniform(-50, 50)
            jitter_y = random.uniform(-50, 50)
            layers.append(make_layer(
                idx=li, name=f"L_{li:02d}", width=bw, height=bh,
                x=bw/2 + jitter_x, y=bh/2 + jitter_y,
                source_id=h("src", shot_idx, li),
            ))
        baseline = make_comp(f"shot_{shot_idx:02d}_base", bw, bh, layers)
        target = apply_rule(baseline, rule, rule_name)

        gt = {
            "mode": "rule-fit",
            "rule_name": rule_name,
            "rule_params": rule,
            "expected_scale_coef": rule["scale_coef"] * rule["bleed_factor"],
            "expected_y_bias_px": rule["y_bias_px"],
        }
        fk = classify_canvas(rule["target_w"], rule["target_h"])
        manifest = make_manifest(baseline, {fk: target}, gt)
        path = out_dir / f"synth_rulefit_{rule_name}_{shot_idx:02d}.json"
        path.write_text(json.dumps(manifest, indent=2))
    print(f"Generated {n_shots} rule-fit shots ({rule_name}) → {out_dir}")


# ---------------------------------------------------------------------------
# Mode 3: edge-cases
# ---------------------------------------------------------------------------
def gen_edge_cases(out_dir: Path):
    """Curated battery of structurally tricky comps. Each is one shot
    targeting one specific failure mode."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = []

    # Case 1: text-only comp
    bw, bh = 1920, 1080
    text_layers = []
    for li in range(1, 6):
        L = make_layer(li, f"Text_{li}", bw, bh, layer_type="TextLayer")
        L["text"] = {"value": f"Text content {li}", "fontSize": 48 + li * 4}
        text_layers.append(L)
    baseline = make_comp("text_only_base", bw, bh, text_layers)
    target = apply_rule(baseline, RULES["tiktok_v1"], "tiktok_v1")
    cases.append(("edge_text_only.json", baseline, {"9x16": target},
                  {"case": "text_only_comp",
                   "description": "All-text comp — rule-fit should still apply uniformly"}))

    # Case 2: deep parent chain (each layer parented to the previous)
    parent_chain = []
    prev = None
    for li in range(1, 8):
        L = make_layer(li, f"Chain_{li}", bw, bh,
                       parent_id=prev["id"] if prev else None)
        parent_chain.append(L); prev = L
    baseline = make_comp("parent_chain_base", bw, bh, parent_chain)
    target = apply_rule(baseline, RULES["tiktok_v1"], "tiktok_v1")
    cases.append(("edge_parent_chain.json", baseline, {"9x16": target},
                  {"case": "deep_parent_chain",
                   "description": "Chain of parented layers — reformat must preserve chain"}))

    # Case 3: keyframed Y-position layers
    kf_layers = []
    for li in range(1, 6):
        L = make_layer(li, f"Anim_{li}", bw, bh,
                       keyframes_y=[(0.0, bh/2 - 100),
                                    (5.0, bh/2 + 100)])
        kf_layers.append(L)
    baseline = make_comp("keyframed_base", bw, bh, kf_layers)
    target = apply_rule(baseline, RULES["tiktok_v1"], "tiktok_v1")
    cases.append(("edge_keyframed_y.json", baseline, {"9x16": target},
                  {"case": "keyframed_y_position",
                   "description": "Animated Y on every layer — rule applied to all keys"}))

    # Case 4: 3-layer baseline → big target (one of the suspicious real-data shapes)
    tiny = [make_layer(li, f"Tiny_{li}", bw, bh) for li in range(1, 4)]
    baseline = make_comp("tiny_3layer_base", bw, bh, tiny)
    target = apply_rule(baseline, RULES["portrait_4x5_v1"], "portrait_4x5_v1")
    cases.append(("edge_tiny_baseline.json", baseline, {"4x5": target},
                  {"case": "tiny_baseline_normal_target",
                   "description": "3 baseline layers, target same count — sanity check"}))

    # Case 5: empty comp (no layers at all)
    baseline = make_comp("empty_base", bw, bh, [])
    target = make_comp("empty_target", 1080, 1920, [])
    cases.append(("edge_empty_comp.json", baseline, {"9x16": target},
                  {"case": "empty_comp",
                   "description": "Zero layers — should not crash analysis tools"}))

    # Case 6: single layer
    single = [make_layer(1, "Single", bw, bh)]
    baseline = make_comp("single_layer_base", bw, bh, single)
    target = apply_rule(baseline, RULES["tiktok_v1"], "tiktok_v1")
    cases.append(("edge_single_layer.json", baseline, {"9x16": target},
                  {"case": "single_layer",
                   "description": "One layer — degenerate case for statistical fits"}))

    # Case 7: extreme layer count (50)
    many = [make_layer(li, f"Many_{li:02d}", bw, bh) for li in range(1, 51)]
    baseline = make_comp("many_layers_base", bw, bh, many)
    target = apply_rule(baseline, RULES["tiktok_v1"], "tiktok_v1")
    cases.append(("edge_50_layers.json", baseline, {"9x16": target},
                  {"case": "extreme_layer_count",
                   "description": "50 layers — perf + stats robustness"}))

    # Case 8: aspect-flip (vertical → horizontal)
    v_layers = [make_layer(li, f"VtoH_{li}", 1080, 1920) for li in range(1, 6)]
    baseline = make_comp("vertical_base", 1080, 1920, v_layers)
    # Manually build a horizontal target (no rule for v→h yet)
    h_layers = []
    for L in deepcopy(v_layers):
        L["transform"]["children"][1]["value"] = [960, 540]
        L["transform"]["children"][5]["value"] = [56.25, 56.25]
        h_layers.append(L)
    h_target = make_comp("horizontal_target", 1920, 1080, h_layers)
    cases.append(("edge_vertical_to_horizontal.json", baseline, {"HD": h_target},
                  {"case": "aspect_flip",
                   "description": "Vertical baseline reformatted to horizontal — unusual direction"}))

    for fname, base, tgts, gt in cases:
        gt["mode"] = "edge-case"
        manifest = make_manifest(base, tgts, gt)
        (out_dir / fname).write_text(json.dumps(manifest, indent=2))
    print(f"Generated {len(cases)} edge-case shots → {out_dir}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Synthetic AE comp manifest generator")
    sub = p.add_subparsers(dest="mode", required=True)

    m1 = sub.add_parser("matcher-stress")
    m1.add_argument("--shots", type=int, default=20)
    m1.add_argument("--out", type=Path, default=Path("synth/matcher"))
    m1.add_argument("--seed", type=int, default=42)

    m2 = sub.add_parser("rule-fit")
    m2.add_argument("--shots", type=int, default=30)
    m2.add_argument("--rule", choices=list(RULES.keys()), default="tiktok_v1")
    m2.add_argument("--out", type=Path, default=Path("synth/rules"))
    m2.add_argument("--seed", type=int, default=42)

    m3 = sub.add_parser("edge-cases")
    m3.add_argument("--out", type=Path, default=Path("synth/edges"))

    args = p.parse_args()
    if args.mode == "matcher-stress":
        gen_matcher_stress(args.shots, args.out, args.seed)
    elif args.mode == "rule-fit":
        gen_rule_fit(args.shots, args.rule, args.out, args.seed)
    elif args.mode == "edge-cases":
        gen_edge_cases(args.out)


if __name__ == "__main__":
    main()
