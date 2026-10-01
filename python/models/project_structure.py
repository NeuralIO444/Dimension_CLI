# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/models/project_structure.py
v5.7 — Project-wide structure manifest, written by Sovereign_Core.jsx
(`scanProjectStructure()`) as a sibling to scrape_manifest.json.

The active-comp scrape (scrape_manifest.json) walks ONE comp's layers.
The project-structure scan walks every CompItem in the project and
records:
  - comp metadata (id, dimensions, layer count, render-queue membership)
  - the precomp dependency graph (which comp uses which)

This is the read-only inventory the v5.8 Flat Duplication Engine needs
before it can plan a duplication: "if I'm about to duplicate MAIN_4K and
MAIN_HD, is there a precomp shared between them that I need to fork?"

PRIVACY: project_structure.json contains only IDs, names, and dimensions
— no layer pixel data, no expressions, no source paths. Same local-only
guarantees as scrape_manifest.json.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class CompNode(BaseModel):
    """One CompItem in the project. Identity is `id` (AE-assigned, stable
    across scrapes within a project file)."""

    id: int = Field(..., description="AE comp.id — stable per project file")
    name: str
    width: int
    height: int
    fps: Optional[float] = None
    duration: Optional[float] = None
    pixel_aspect: Optional[float] = None
    bg_color: Optional[List[float]] = Field(
        default=None,
        description="[r, g, b] 0..1 — comp background color",
    )
    layer_count: int = Field(default=0, ge=0)

    is_render_target: bool = Field(
        default=False,
        description="True if this comp is currently in app.project.renderQueue",
    )
    folder_path: Optional[str] = Field(
        default=None,
        description='Slash-joined parent-folder chain, e.g. "Renders/4K". '
                    'Empty string for top-level comps.',
    )

    # ── Slot 12.5 / schema 5.1 — composition advanced flags ──────────────
    # Both are properties of the CompItem (Composition Settings → Advanced),
    # NOT properties of the precomp layer instance in the parent comp.
    # Stage B's recursive scrape will populate these when walking the
    # project structure; Stage C's matrix walker reads them to decide
    # how nested precomps compose into their parents.
    preserve_nested_frame_rate: bool = Field(
        default=False,
        description='AE "Preserve frame rate when nested or in render '
                    'queue" — when True, this comp keeps its own fps '
                    'when referenced from a parent with a different fps.',
    )
    preserve_nested_resolution: bool = Field(
        default=False,
        description='AE "Preserve resolution when nested" — when True, '
                    'this comp renders at its own resolution when '
                    'referenced from a parent.',
    )


class CompReference(BaseModel):
    """Edge in the comp dependency graph: comp `from_comp_id` has a layer
    whose source is comp `to_comp_id`."""

    from_comp_id: int
    to_comp_id: int
    layer_index: int = Field(..., ge=1)
    layer_name: str


class ScanMeta(BaseModel):
    scanned_at: str = Field(..., description="ISO8601 UTC timestamp")
    ae_version: Optional[str] = None
    project_name: Optional[str] = Field(
        default=None,
        description="Filename of app.project.file, or None for unsaved projects",
    )


class ProjectStructure(BaseModel):
    """Top-level container. Mirrors `scanProjectStructure()` JSON output."""

    status: str = Field(..., description='"OK" on success, "ERROR" otherwise')
    schema_version: str = "1.0"
    scan_meta: ScanMeta
    comps: List[CompNode] = Field(default_factory=list)
    references: List[CompReference] = Field(default_factory=list)

    def comps_by_id(self) -> dict[int, CompNode]:
        """Index helper — analyzer hits this constantly."""
        return {c.id: c for c in self.comps}
