# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/comp_cleaner.py
TASK-UX-01 (Issue #297) — Unreferenced Dimension Duplicate Comp Cleanup Utility.

Detects and plans safe garbage collection of orphaned compositions generated
during previous conform runs (e.g. `*__dim_dup_*`, `*__dim_*`).

Invariants:
  1. Never delete an active/open composition currently being viewed.
  2. Never delete a composition referenced by any other composition as a layer.
  3. Deletions in ExtendScript must be wrapped in an Undo Group ("Clean Dimension Orphan Comps").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


DIMENSION_DUP_PATTERN = re.compile(r"(__dim_dup_|_dim_dup|__dim_|\[CONFORM\]|\[DIM_DUP\])", re.IGNORECASE)


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


class CompCleaner:
    """Analyzes project hierarchy and identifies unreferenced Dimension duplicate compositions."""

    @classmethod
    def analyze_project(
        cls,
        project_structure: Dict[str, Any],
        active_comp_id: Optional[int] = None,
        custom_pattern: Optional[str] = None,
    ) -> CompCleanupPlan:
        pattern = re.compile(custom_pattern, re.IGNORECASE) if custom_pattern else DIMENSION_DUP_PATTERN

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

            # Match criteria: Has Dimension naming tag AND reference count is 0 AND not active
            is_dim_dup = bool(pattern.search(name))

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
