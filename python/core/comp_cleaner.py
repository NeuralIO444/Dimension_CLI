# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/comp_cleaner.py
TASK-UX-01 (Issue #297) — Unreferenced Dimension Duplicate Comp Cleanup Utility.

Detects and plans safe garbage collection of orphaned compositions generated
during previous conform runs.

PROVENANCE, NOT NAMES (issue #10 / Dimension #551): the old
DIMENSION_DUP_PATTERN regex matched none of the names
`output_naming.resolve_output_name` actually produces, so the CLEAN COMPS
report was inert on real projects. The regex is deleted. A comp is now a
duplicate candidate if and only if its name appears in the provenance set:
`.dimension/duplication_log.json` (Babysitter records every comp it
creates: `duplicate_name` + ids) unioned with the manifest's
`duplication_plan`. No provenance → no candidates (safe default).

Invariants (unchanged):
  1. Never flag an active/open composition currently being viewed.
  2. Never flag a composition referenced by any other composition as a layer.
  3. Never flag a render-queued composition.
  4. Deletions in ExtendScript must be wrapped in an Undo Group
     ("Clean Dimension Orphan Comps") — the delete path itself stays out
     of this CLI entirely; only the read-only report ships
     (`duplication cleanup-report`; the full `dimension clean` lands in #5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Collection, Dict, List, Optional, Set


@dataclass(frozen=True)
class OrphanCompCandidate:
    comp_id: int
    name: str
    width: int
    height: int
    duration_s: float
    reference_count: int
    is_active_comp: bool = False
    is_render_queued: bool = False


@dataclass(frozen=True)
class CompCleanupPlan:
    total_comps_scanned: int
    orphaned_candidates: List[OrphanCompCandidate]
    protected_comps: List[Dict[str, Any]]
    total_orphans_count: int
    estimated_memory_freed_mb: float


def load_known_duplicate_names(
    duplication_log_path: Optional[str] = None,
    manifest_path: Optional[str] = None,
) -> Set[str]:
    """Build the provenance set for `CompCleaner.analyze_project`.

    Union of:
      - `<duplication_log_path>` → `duplicates_made[].duplicate_name`
        (Babysitter's per-run record of every comp it created), and
      - `<manifest_path>` → `duplication_plan.duplicates[].duplicate_name`
        (the chunk/scrape manifest's planned duplicates).

    Missing or unreadable files contribute nothing — provenance is
    opt-in evidence, and its absence must never invent candidates.
    """
    names: Set[str] = set()

    if duplication_log_path:
        try:
            with open(duplication_log_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict):
            for entry in payload.get("duplicates_made") or []:
                if isinstance(entry, dict):
                    name = str(entry.get("duplicate_name") or "").strip()
                    if name:
                        names.add(name)

    if manifest_path:
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict):
            plan = payload.get("duplication_plan") or {}
            if isinstance(plan, dict):
                for entry in plan.get("duplicates") or []:
                    if isinstance(entry, dict):
                        name = str(entry.get("duplicate_name") or "").strip()
                        if name:
                            names.add(name)

    return names


class CompCleaner:
    """Analyzes project hierarchy and identifies unreferenced Dimension duplicate compositions."""

    @classmethod
    def analyze_project(
        cls,
        project_structure: Dict[str, Any],
        active_comp_id: Optional[int] = None,
        known_duplicates: Optional[Collection[str]] = None,
    ) -> CompCleanupPlan:
        """Read-only duplicate-comp report.

        A comp is a duplicate candidate iff its name is in
        `known_duplicates` (the provenance set built by
        `load_known_duplicate_names`). `None`/empty → no candidates:
        without provenance we know nothing, so we report nothing.
        The live invariants still hold: active, layer-referenced, and
        render-queued comps are never candidates — they land in
        `protected_comps` with a reason instead.
        """
        known: Set[str] = set(known_duplicates) if known_duplicates else set()

        items = project_structure.get("items", []) or project_structure.get("compositions", [])
        comps = [item for item in items if item.get("type") == "composition" or item.get("typeName") == "Composition" or "width" in item]

        # 1. Build Layer Reference Graph: count occurrences of comp_id as layer source
        ref_counts: Dict[int, int] = {c.get("id", c.get("comp_id", 0)): 0 for c in comps}

        for c in comps:
            for layer in c.get("layers", []):
                src_id = layer.get("source_comp_id") or layer.get("source_id")
                if src_id and src_id in ref_counts:
                    ref_counts[src_id] += 1

        orphans: List[OrphanCompCandidate] = []
        protected: List[Dict[str, Any]] = []
        total_memory_mb = 0.0

        for c in comps:
            cid = c.get("id", c.get("comp_id", 0))
            name = c.get("name", "")
            w = c.get("width", 1920)
            h = c.get("height", 1080)
            dur = c.get("duration", 10.0)
            refs = ref_counts.get(cid, 0)
            is_active = (cid == active_comp_id)
            is_queued = bool(c.get("is_render_queued", False))

            # Provenance predicate: Babysitter provably created this comp.
            is_dim_dup = name in known

            if is_dim_dup:
                if refs == 0 and not is_active and not is_queued:
                    # Memory estimate (8-bpc 30fps cached buffer)
                    mem = (w * h * 4 * min(dur, 5.0) * 30) / (1024.0 * 1024.0)
                    total_memory_mb += mem
                    orphans.append(OrphanCompCandidate(
                        comp_id=cid,
                        name=name,
                        width=w,
                        height=h,
                        duration_s=dur,
                        reference_count=0,
                        is_active_comp=False,
                        is_render_queued=False,
                    ))
                else:
                    protected.append({
                        "comp_id": cid,
                        "name": name,
                        "reason": "ACTIVE_COMP" if is_active else ("RENDER_QUEUED" if is_queued else "REFERENCED_BY_LAYERS"),
                        "reference_count": refs,
                    })

        return CompCleanupPlan(
            total_comps_scanned=len(comps),
            orphaned_candidates=orphans,
            protected_comps=protected,
            total_orphans_count=len(orphans),
            estimated_memory_freed_mb=round(total_memory_mb, 2),
        )
