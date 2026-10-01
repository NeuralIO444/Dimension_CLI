# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Stage 2b — duplication planning (shared precomp forks).

Wraps `logic.duplication_preflight` so conform/inject callers import
one stage module instead of reaching into planner glue.
"""

from __future__ import annotations

from typing import Optional, Tuple

import os

from core.io_utils import atomic_write_json
from logic.duplication_preflight import (
    DuplicationPreflightSession,
    build_duplication_session,
)
from models.duplication_plan import DuplicationPlan


def compute_duplication_plan(
    project_root: str,
    scrape_manifest_path: str,
    target_dimensions: Tuple[int, int],
    preset_id: str,
    *,
    session_id: Optional[str] = None,
) -> Optional[DuplicationPreflightSession]:
    """Build the default duplication plan, or None when no forks needed."""
    return build_duplication_session(
        project_root,
        scrape_manifest_path,
        target_dimensions,
        preset_id,
        session_id=session_id,
    )


def write_duplication_plan(
    plan: DuplicationPlan,
    destination_path: str,
) -> str:
    """Persist a plan JSON for Babysitter / audit.  Returns path written."""
    path = os.path.abspath(destination_path)
    atomic_write_json(path, plan.model_dump())
    return path