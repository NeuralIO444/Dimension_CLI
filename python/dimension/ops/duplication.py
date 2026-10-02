# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial.
# See LICENSE for full terms.

"""Duplication + cleanup ops. Headless and read-only.

`preview` computes the shared-precomp fork plan for a conform
("what will be duplicated") from the manifest + project structure.

`cleanup_report` lists orphaned Dimension-created comps. It NEVER
deletes — Matt descoped #540's destructive path to report-only for
the farewell release. Duplicate candidates come from provenance
(`.dimension/dimension.db`, issue #16), never name regexes (#551).
"""

from __future__ import annotations

import dataclasses
import json
import os
from typing import Any, Optional

from dimension.common import DimensionError
from core.comp_cleaner import CompCleaner, load_known_duplicate_names
from logic.comp_cleaner_adapter import adapt_project_structure_for_comp_cleaner
from logic.duplication_preflight import build_duplication_session
from models.project_structure import ProjectStructure


def _layer_label(layer_uid: str, manifest_layers: dict) -> str:
    info = manifest_layers.get(layer_uid)
    if info is None:
        return f"uid:{layer_uid[:8]}…"
    return f"{info.get('comp', '?')} · {info.get('name', '?')}"


def preview_op(
    *,
    manifest: str,
    width: int,
    height: int,
    preset: str = "manual",
) -> dict[str, Any]:
    """Compute the duplication plan. `available: false` is a normal
    answer for flat comps (no shared precomps in scope)."""
    if not os.path.isfile(manifest):
        raise DimensionError(
            f"scrape manifest not found: {manifest}", code="SOURCE_NOT_FOUND"
        )
    try:
        with open(manifest, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        raise DimensionError(
            f"could not parse scrape manifest: {e}",
            code="MANIFEST_INVALID",
        ) from e
    if not isinstance(raw, dict):
        raise DimensionError(
            "scrape manifest must be a JSON object",
            code="MANIFEST_INVALID",
        )
    project_root = os.path.dirname(os.path.abspath(manifest))
    session = build_duplication_session(
        project_root=project_root,
        scrape_manifest_path=manifest,
        target_dimensions=(width, height),
        preset_id=preset,
        session_id=None,
    )
    if session is None:
        return {
            "status": "OK",
            "headless": True,
            "available": False,
            "reason": "no shared precomps in scope (flat comp or no project_structure.json)",
        }
    plan = session.default_plan
    manifest_layers = {
        layer.get("uid", ""): {
            "comp": layer.get(
                "containing_comp_name",
                raw.get("project_info", {}).get("name", "?"),
            ),
            "name": layer.get("name", "?"),
        }
        for layer in raw.get("layers", [])
        if isinstance(layer, dict)
    }

    return {
        "status": "OK",
        "headless": True,
        "available": True,
        "session_id": plan.session_id,
        "target": list(plan.target_dimensions),
        "preset_id": plan.preset_id,
        "duplicate_count": len(plan.duplicates),
        "rewire_count": len(plan.rewires),
        "duplicates": [
            {
                "original_name": d.original_name,
                "duplicate_name": d.duplicate_name,
                "target_folder": d.target_folder_path,
                "original_dims": [d.original_width, d.original_height],
                "reason": d.reason,
                "is_protected": d.is_protected,
                "will_be_skipped": d.will_be_skipped,
                "name_version": d.name_version,
                "fork_per_consumer": d.fork_per_consumer,
            }
            for d in plan.duplicates
        ],
        "rewires_summary": [
            {
                "layer": _layer_label(r.conformed_layer_uid, manifest_layers),
                "from": _layer_label(r.original_source_uid, manifest_layers),
                "to": _layer_label(r.new_source_uid, manifest_layers),
            }
            for r in plan.rewires[:20]
        ],
        "rewires_truncated": len(plan.rewires) > 20,
    }


def cleanup_report_op(
    *,
    project_structure: str,
    active_comp_id: Optional[int] = None,
    db: Optional[str] = None,
    chunk_manifest: Optional[str] = None,
) -> dict[str, Any]:
    """Read-only orphan report. Provenance-gated, never deletes."""
    if not os.path.isfile(project_structure):
        raise DimensionError(
            f"project_structure.json not found: {project_structure}",
            code="SOURCE_NOT_FOUND",
        )
    try:
        with open(project_structure, "r", encoding="utf-8") as f:
            raw = json.load(f)
        ps = ProjectStructure.model_validate(raw)
    except Exception as e:
        raise DimensionError(
            f"could not parse project_structure.json: {e}",
            code="MANIFEST_INVALID",
        ) from e

    legacy_shape = adapt_project_structure_for_comp_cleaner(ps)

    # Provenance, not names (#10): the project DB (issue #16) unioned
    # with a manifest's duplication plan. Read-only — never created.
    db_path = db or os.path.join(
        os.path.dirname(os.path.abspath(project_structure)),
        ".dimension",
        "dimension.db",
    )
    known = load_known_duplicate_names(db_path=db_path, manifest_path=chunk_manifest)
    plan = CompCleaner.analyze_project(
        legacy_shape,
        active_comp_id=active_comp_id,
        known_duplicates=known,
    )
    return {
        "status": "OK",
        "headless": True,
        "available": True,
        "read_only": True,
        "provenance_names_loaded": len(known),
        "total_comps_scanned": plan.total_comps_scanned,
        "total_orphans_count": plan.total_orphans_count,
        "estimated_memory_freed_mb": plan.estimated_memory_freed_mb,
        "orphaned_candidates": [dataclasses.asdict(c) for c in plan.orphaned_candidates],
        "protected_comps": plan.protected_comps,
    }
