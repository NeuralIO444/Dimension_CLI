# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/logic/duplication_preflight.py
v5.8 — pipeline-integration glue between the conform flow and the
DuplicationPlanner.

Pure-Python module. Keeps qt_controller lean: the controller only has
to invoke `build_duplication_session()`, exec the modal it returns,
and re-plan with the user's overrides.

Pipeline position (Q8):
    user clicks EXECUTE
        → load scrape_manifest.json (already on disk)
        → load project_structure.json (v5.7 sidecar)
        → planner.plan() → if empty, fall through (legacy path)
        → modal.exec() → user picks PROCEED / DRY_RUN / CANCEL
        → re-plan with user overrides
        → conform proceeds (PROCEED) or short-circuits (CANCEL/DRY_RUN)

The actual Babysitter dispatch lives in qt_controller — this module
stops at "build the plan + tell the controller what to do next."
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import NamedTuple, Optional, Tuple

from core.duplication_planner import DuplicationPlanner
from core.logger import log
from core.project_structure_analyzer import ProjectStructureAnalyzer
from models.duplication_plan import DuplicationPlan
from models.project_structure import ProjectStructure
from models.scrape_manifest import ScrapeManifest


class DuplicationPreflightSession(NamedTuple):
    """One live preflight context. Passed to the modal then back to
    the controller. The planner is included so the controller can
    re-call `plan(user_overrides=...)` after the modal closes."""
    planner: DuplicationPlanner
    default_plan: DuplicationPlan
    structure: ProjectStructure
    analyzer: ProjectStructureAnalyzer
    active_comp_name: str


def _resolve_structure_path(project_root: str,
                             scrape_manifest_path: str) -> Optional[Path]:
    """Find project_structure.json. v5.7's saveScrapeToFile writes it
    as a sibling of scrape_manifest.json. Falls back to a project-root
    search for older sessions where the layout was different."""
    sibling = Path(scrape_manifest_path).resolve().parent / "project_structure.json"
    if sibling.is_file():
        return sibling
    fallback = Path(project_root) / "project_structure.json"
    if fallback.is_file():
        log.info(
            "duplication preflight: structure missing as sibling, "
            "using project-root fallback",
            extra={"sibling": str(sibling), "fallback": str(fallback)},
        )
        return fallback
    return None


def build_duplication_session(
    project_root: str,
    scrape_manifest_path: str,
    target_dimensions: Tuple[int, int],
    preset_id: str,
    session_id: Optional[str] = None,
) -> Optional[DuplicationPreflightSession]:
    """Load disk artifacts, instantiate the planner, build the default
    plan. Returns None when:
      - project_structure.json missing (v5.7 not yet integrated, or
        the user is on a stale scrape from before v5.7 shipped)
      - manifest validation fails (malformed scrape)
      - structure validation fails
      - planner emits an empty plan (no shared precomps in active chain)

    Caller short-circuits to the legacy conform path on None — zero
    behavioral change for projects without shared precomps.

    `session_id`: when set, threads through to `DuplicationPlanner` so
    `plan.session_id` (and the `duplication_log.json` Babysitter
    writes at dispatch tail) matches the orchestrator's id. The
    reporter's leak-defense compares against the same id; alignment
    keeps the defense useful (rejects prior-session stale logs)
    without over-firing on current-session logs. When None, the
    planner self-generates a UUID4 — legacy callers that don't thread
    an id see no behavior change."""

    structure_path = _resolve_structure_path(project_root, scrape_manifest_path)
    if structure_path is None:
        log.info("duplication preflight: no project_structure.json — "
                 "skipping (legacy path)",
                 extra={"manifest": scrape_manifest_path})
        return None

    # Load + validate scrape manifest.
    try:
        with open(scrape_manifest_path, "r", encoding="utf-8") as f:
            manifest_raw = json.load(f)
        manifest = ScrapeManifest.model_validate(manifest_raw)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("duplication preflight: manifest unreadable",
                    extra={"path": scrape_manifest_path, "error": str(e)})
        return None
    except Exception as e:  # noqa: BLE001 — pydantic ValidationError
        log.warning("duplication preflight: manifest validation failed",
                    extra={"path": scrape_manifest_path, "error": str(e)})
        return None

    # Load + validate project structure.
    try:
        with open(structure_path, "r", encoding="utf-8") as f:
            structure_raw = json.load(f)
        structure = ProjectStructure.model_validate(structure_raw)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("duplication preflight: structure unreadable",
                    extra={"path": str(structure_path), "error": str(e)})
        return None
    except Exception as e:  # noqa: BLE001 — pydantic ValidationError
        log.warning("duplication preflight: structure validation failed",
                    extra={"path": str(structure_path), "error": str(e)})
        return None

    # Build planner and default plan.
    planner = DuplicationPlanner(
        structure=structure,
        manifest=manifest,
        target_dimensions=target_dimensions,
        preset_id=preset_id,
        session_id=session_id,
    )
    default_plan = planner.plan()

    if default_plan.is_empty:
        log.info("duplication preflight: no shared precomps — "
                 "skipping modal (legacy path)",
                 extra={"target": list(target_dimensions),
                         "preset": preset_id})
        return None

    analyzer = ProjectStructureAnalyzer(structure)
    active_comp_name = manifest.project_info.name or ""

    log.info("duplication preflight: plan ready for modal",
             extra={"duplicates":           len(default_plan.duplicates),
                     "aspect_ratio_changed": default_plan.aspect_ratio_changed,
                     "preset":               preset_id,
                     "target":               list(target_dimensions)})

    return DuplicationPreflightSession(
        planner=planner,
        default_plan=default_plan,
        structure=structure,
        analyzer=analyzer,
        active_comp_name=active_comp_name,
    )


def duplication_log_path_for(project_root: str) -> str:
    """Where Babysitter writes duplication_log.json. Sibling of the
    .dimension/ sidecar dir so the reporter finds it cleanly."""
    return os.path.join(project_root, ".dimension", "duplication_log.json")
