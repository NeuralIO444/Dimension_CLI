# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Survey op: run the tag-classifier heuristics over a scrape manifest.

Headless. Classifies layers in place (the manifest is the artist's
tag-once surface) unless dry_run is set.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from dimension.common import DimensionError
from models.scrape_manifest import ScrapeManifest
from stages.survey import resolve_studio_profile, run_survey


def survey_op(
    *,
    manifest_path: str,
    profile: Optional[str] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Classify layers on the manifest. Returns surveyor stats."""
    if not os.path.isfile(manifest_path):
        raise DimensionError(
            f"scrape manifest not found: {manifest_path}",
            code="SOURCE_NOT_FOUND",
        )
    with open(manifest_path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        manifest = ScrapeManifest.model_validate_json(raw)
    except Exception as e:
        raise DimensionError(
            f"could not parse scrape manifest: {e}",
            code="MANIFEST_INVALID",
        ) from e

    if profile is not None and resolve_studio_profile(profile) is None:
        raise DimensionError(
            f"studio profile not found: {profile}",
            code="PROFILE_NOT_FOUND",
        )

    stats = run_survey(manifest, profile_id=profile)
    if not dry_run:
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(manifest.model_dump_json(by_alias=True, indent=2))

    return {
        "status": "OK",
        "headless": True,
        "manifest": os.path.abspath(manifest_path),
        "written": not dry_run,
        "stats": stats if isinstance(stats, dict) else {"result": stats},
    }
