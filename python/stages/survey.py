# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Stage 1 — survey / classify layers on a scraped manifest.

Pure Python: mutates `ScrapeManifest` in place via `survey_manifest()`.
AE scrape (Sovereign_Core / bridge) is a separate upstream step.
"""

from __future__ import annotations

from typing import Any, Optional

from core.surveyor import survey_manifest
from models.scrape_manifest import ScrapeManifest


def resolve_studio_profile(profile_id: Optional[str] = None):
    """Return the studio profile for Pass 2 prefix rules."""
    try:
        from logic.studio_profile_registry import REGISTRY as registry
        if profile_id:
            return registry.get(profile_id)
        return registry.get_active()
    except Exception:
        return None


def run_survey(
    manifest: ScrapeManifest,
    *,
    profile_id: Optional[str] = None,
    profile=None,
    comp_width: Optional[int] = None,
    comp_height: Optional[int] = None,
) -> dict[str, Any]:
    """Classify layers on `manifest` in place.  Returns surveyor stats dict."""
    active_profile = profile if profile is not None else resolve_studio_profile(profile_id)
    if comp_width is None or comp_height is None:
        info = manifest.project_info
        comp_width = info.width if info else 0
        comp_height = info.height if info else 0
    return survey_manifest(
        manifest,
        comp_width=comp_width,
        comp_height=comp_height,
        profile=active_profile,
    )