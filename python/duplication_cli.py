"""Duplication-plan CLI shim — lets the CEP panel show a "what will
be duplicated" preview before EXECUTE runs.

When the project has shared precomps (the same precomp used by
multiple compositions), Babysitter has to fork them per-target so
the conform engine can transform one without affecting the others.
That duplication is deterministic given the manifest + project
structure + target dimensions; we can pre-compute and surface the
plan in the panel so the artist knows what's about to happen.

Usage:
    duplication_cli.py preview --manifest <path> --width W --height H
                               [--preset <id>]

Output (one JSON object on stdout):
    {
      "available":      bool,   # false when no shared precomps / no project_structure.json
      "reason":         "...",  # only present when available=false
      "session_id":     "uuid",
      "target":         [W, H],
      "preset_id":      "...",
      "duplicate_count": N,
      "rewire_count":    M,
      "duplicates": [
        {
          "original_name":   "Background_v3",
          "duplicate_name":  "Background_v3_D",
          "target_folder":   "From Dimensions/.../...",
          "original_dims":   [1920, 1080],
          "reason":          "SHARED",
          "is_protected":    false,
          "will_be_skipped": false,
          "name_version":    1,
          "fork_per_consumer": false
        },
        ...
      ],
      "rewires_summary": [
        {"layer": "Hero comp · 1", "from": "Background_v3", "to": "Background_v3_D"},
        ...
      ]
    }

The panel uses `available` to gate whether to show the duplication
modal at all — false means the active project has no shared
precomps in scope for this conform, so the modal can be skipped
without any user surprise.
"""

import argparse
import dataclasses
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from logic.duplication_preflight import build_duplication_session
from logic.comp_cleaner_adapter import adapt_project_structure_for_comp_cleaner
from core.comp_cleaner import CompCleaner, load_known_duplicate_names
from models.project_structure import ProjectStructure


def _layer_label(layer_uid: str, manifest_layers: dict) -> str:
    """Best-effort human-readable label for a layer uid, used in the
    rewires_summary list. Falls back to the uid prefix when no
    matching layer is in the manifest (rare — usually means a
    cross-comp rewire whose source layer wasn't in the active comp's
    scrape)."""
    info = manifest_layers.get(layer_uid)
    if info is None:
        return f"uid:{layer_uid[:8]}…"
    return f"{info.get('comp', '?')} · {info.get('name', '?')}"


def main():
    parser = argparse.ArgumentParser(prog="duplication")
    sub = parser.add_subparsers(dest="op", required=True)

    pv = sub.add_parser("preview", help="Compute the duplication plan + emit JSON")
    pv.add_argument("--manifest", required=True)
    pv.add_argument("--width",  type=int, required=True)
    pv.add_argument("--height", type=int, required=True)
    pv.add_argument("--preset", default="manual")

    # Issue #350 -- read-only orphan-comp report. Deliberately NOT a
    # delete command: this only ever reports candidates, never touches
    # the AE project. The destructive path (actual deletion, undo group,
    # manual AE QA) is scoped separately per the explicit decision to
    # ship non-destructive first.
    cr = sub.add_parser(
        "cleanup-report",
        help="Read-only report of unreferenced Dimension duplicate comps (never deletes)",
    )
    cr.add_argument("--project-structure", required=True,
                     help="Path to project_structure.json")
    cr.add_argument("--active-comp-id", type=int, default=None,
                     help="AE comp.id of the currently active comp, if known "
                          "(protects it from being listed as an orphan)")
    # Issue #10 — provenance, not names. Duplicate candidates come from
    # the project database .dimension/dimension.db (issue #16; REPLACES
    # duplication_log.json) unioned with a manifest's duplication_plan.
    # The old --pattern regex override is gone with DIMENSION_DUP_PATTERN.
    cr.add_argument("--db", default=None,
                     help="Path to .dimension/dimension.db "
                          "(default: <project-structure-dir>/.dimension/dimension.db)")
    cr.add_argument("--chunk-manifest", default=None,
                     help="Optional chunk/scrape manifest whose duplication_plan "
                          "is unioned into the provenance set")

    args = parser.parse_args()

    if args.op == "preview":
        if not os.path.exists(args.manifest):
            print(json.dumps({
                "available": False,
                "reason": f"manifest not found: {args.manifest}",
            }))
            sys.exit(0)

        project_root = os.path.dirname(os.path.abspath(args.manifest))
        session = build_duplication_session(
            project_root=project_root,
            scrape_manifest_path=args.manifest,
            target_dimensions=(args.width, args.height),
            preset_id=args.preset,
            session_id=None,
        )
        if session is None:
            # build_duplication_session returns None when project_structure.json
            # is missing OR when the plan has no shared precomps —
            # both are normal flat-comp cases.
            print(json.dumps({
                "available": False,
                "reason": "no shared precomps in scope (flat comp or no project_structure.json)",
            }))
            sys.exit(0)

        plan = session.default_plan

        # Build a layer-uid → {comp, name} index from the manifest so
        # rewires_summary can show human-readable labels instead of
        # raw uids.
        try:
            with open(args.manifest, "r", encoding="utf-8") as f:
                raw = json.load(f)
            manifest_layers = {}
            for layer in raw.get("layers", []):
                manifest_layers[layer.get("uid", "")] = {
                    "comp":  layer.get("containing_comp_name", raw.get("project_info", {}).get("name", "?")),
                    "name":  layer.get("name", "?"),
                }
        except Exception:
            manifest_layers = {}

        out = {
            "available":       True,
            "session_id":      plan.session_id,
            "target":          list(plan.target_dimensions),
            "preset_id":       plan.preset_id,
            "duplicate_count": len(plan.duplicates),
            "rewire_count":    len(plan.rewires),
            "duplicates": [
                {
                    "original_name":     d.original_name,
                    "duplicate_name":    d.duplicate_name,
                    "target_folder":     d.target_folder_path,
                    "original_dims":     [d.original_width, d.original_height],
                    "reason":            d.reason,
                    "is_protected":      d.is_protected,
                    "will_be_skipped":   d.will_be_skipped,
                    "name_version":      d.name_version,
                    "fork_per_consumer": d.fork_per_consumer,
                }
                for d in plan.duplicates
            ],
            "rewires_summary": [
                {
                    "layer": _layer_label(r.conformed_layer_uid, manifest_layers),
                    "from":  _layer_label(r.original_source_uid, manifest_layers),
                    "to":    _layer_label(r.new_source_uid, manifest_layers),
                }
                for r in plan.rewires[:20]  # cap at 20 for the modal — full list lives in duplication_log.json
            ],
            "rewires_truncated": len(plan.rewires) > 20,
        }
        print(json.dumps(out))

    elif args.op == "cleanup-report":
        if not os.path.exists(args.project_structure):
            print(json.dumps({
                "available": False,
                "reason": f"project_structure.json not found: {args.project_structure}",
            }))
            sys.exit(0)

        try:
            with open(args.project_structure, "r", encoding="utf-8") as f:
                raw = json.load(f)
            ps = ProjectStructure.model_validate(raw)
        except Exception as e:
            print(json.dumps({
                "available": False,
                "reason": f"could not parse project_structure.json: {e}",
            }))
            sys.exit(0)

        legacy_shape = adapt_project_structure_for_comp_cleaner(ps)

        # Provenance, not names (issue #10): candidates are comps Babysitter
        # provably created, per .dimension/dimension.db (issue #16) ∪
        # manifest plan. The DB is read-only here — never created.
        db_path = args.db
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(os.path.abspath(args.project_structure)),
                ".dimension", "dimension.db",
            )
        known = load_known_duplicate_names(
            db_path=db_path,
            manifest_path=args.chunk_manifest,
        )
        plan = CompCleaner.analyze_project(
            legacy_shape,
            active_comp_id=args.active_comp_id,
            known_duplicates=known,
        )

        print(json.dumps({
            "available": True,
            "read_only": True,
            "provenance_names_loaded": len(known),
            "total_comps_scanned": plan.total_comps_scanned,
            "total_orphans_count": plan.total_orphans_count,
            "estimated_memory_freed_mb": plan.estimated_memory_freed_mb,
            "orphaned_candidates": [dataclasses.asdict(c) for c in plan.orphaned_candidates],
            "protected_comps": plan.protected_comps,
        }))


if __name__ == "__main__":
    main()
