# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/models/duplication_plan.py
v5.8 — Flat Precomp Duplication Engine: data models.

A DuplicationPlan is the contract between the Python planner side
(`core/duplication_planner.py`) and the JSX execution side
(`Babysitter.jsx::applyDuplicationPlan`). The planner builds the plan
from a ProjectStructure scan + the conform target dimensions; the
modal shows it to the user; the user toggles per-precomp checkboxes;
the JSX executes it inside an atomic AE undo group.

PIPELINE POSITION (Q8 locked):
    SCAN → ANALYZE STRUCTURE → TAG → CONFORM
        (analyze + plan duplication + scale + SOE)
        → INJECT (duplications + writes) → AUDIT → REPORT

Q6 locked: flat duplication only. The duplicated precomp keeps its
ORIGINAL dimensions; the parent layer in the conformed comp transforms
the duplicate to fit. Recursive conform inside duplicates ships in v6.0.

PRIVACY: this plan is local-only. It travels through the file-bridge
inbox (.dimension_inbox/) and the duplication_log.json that Babysitter
writes — same boundaries as every other Dimension artifact.
"""

from __future__ import annotations

from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, Field


class PrecompDuplicate(BaseModel):
    """One precomp the engine will duplicate. The PROTECT auto-skip and
    user-override paths both surface here — `will_be_skipped=True` means
    Babysitter must NOT call `comp.duplicate()` for this entry."""

    original_uid: str = Field(
        ..., description="UID of the source CompItem in the project")
    original_name: str
    duplicate_name: str = Field(
        ..., description='Q2: "<original>_<W>x<H>", e.g. "Hero_Title_1080x1920"')
    target_folder_path: str = Field(
        ..., description="Q3: 'From Dimensions/<PRESET>_<TIMESTAMP>/'")

    original_width: int = Field(..., gt=0)
    original_height: int = Field(..., gt=0)

    reason: Literal["SHARED", "USER_REQUESTED"] = Field(
        ...,
        description='Why the planner emitted this entry. SHARED = appears '
                    'in 2+ comps; USER_REQUESTED = override added by modal.')
    is_protected: bool = Field(
        default=False,
        description='Set when any layer in the precomp carries the PROTECT '
                    'content_tag. Reporting only — `will_be_skipped` is the '
                    'execution flag.')
    will_be_skipped: bool = Field(
        default=False,
        description='True if PROTECT auto-skipped this entry OR if the user '
                    'unchecked it in the modal. Babysitter consults this '
                    'flag and creates no duplicate when set.')
    name_version: int = Field(
        default=1,
        ge=1,
        description='v5.8.8 — when the planner detects a name collision in '
                    'the project (the proposed `<original>_<W>x<H>` already '
                    'exists), it bumps a version suffix: v2, v3, etc. '
                    'Default 1 means no collision; the modal renders a '
                    'small `vN` chip on the row when this is > 1 so the '
                    'user sees the disambiguation. Babysitter uses '
                    '`duplicate_name` directly — this is the visual breadcrumb.')

    # ── Slot 12.5 Stage C item 2 — Q3B per-consumer fork ─────────────
    fork_per_consumer: bool = Field(
        default=False,
        description='Q3B override marker. False (Q3A default) — this is '
                    'the single shared conformed copy referenced by all '
                    'consumers. True — this entry is one of N forked '
                    'duplicates emitted by the planner for a Q3B-overridden '
                    'precomp; `consumer_layer_uid` identifies which consumer '
                    'this fork is for. The override is per-precomp, surfaced '
                    'in the preflight modal as a per-row toggle.')
    consumer_layer_uid: Optional[str] = Field(
        default=None,
        description='When fork_per_consumer=True, the UID of the specific '
                    'consumer layer this fork is dedicated to. None for '
                    'Q3A-default entries. Babysitter rewires only the '
                    'matching consumer to this fork.')


class LayerRewire(BaseModel):
    """One layer in the conformed comp whose `.source` must be rewired
    from the original precomp to the new duplicate. Babysitter applies
    these AFTER the conformed comp is built and AFTER the duplicates
    are made — the rewire phase glues the two together."""

    conformed_layer_uid: str = Field(
        ..., description="UID of the layer in the (post-conform) comp")
    conformed_layer_name: str
    original_source_uid: str = Field(
        ..., description="UID of the original precomp this layer pointed at")
    new_source_uid: str = Field(
        ..., description="UID of the duplicate precomp the layer should "
                          "point at after rewire")


class DuplicationPlan(BaseModel):
    """Complete plan for one conform invocation. Empty `duplicates`
    means no shared precomps were detected and the modal is skipped
    entirely — the conform runs through the legacy code path with zero
    behavioral change for users without shared-precomp projects."""

    session_id: str = Field(
        ..., description="UUID matching the conform session id — lets the "
                          "audit / report stitch this plan to its scrape "
                          "manifest")
    session_folder: str = Field(
        ..., description="Q3: 'From Dimensions/<PRESET>_<YYYY-MM-DD_HHMMSS>'. "
                          "Babysitter creates this folder before duplicating.")
    preset_id: str

    target_dimensions: Tuple[int, int] = Field(
        ..., description="(width, height) of the conform target")
    aspect_ratio_changed: bool = Field(
        ..., description='SCALE vs RELAYOUT signal. True when source/target '
                          'aspect differ by >= 0.5%. The modal shows the '
                          '"internal layout will not adapt" warning only '
                          'when this is True.')

    duplicates: List[PrecompDuplicate] = Field(default_factory=list)
    rewires: List[LayerRewire] = Field(default_factory=list)
    depth_max: int = Field(
        default=0, ge=0,
        description="Max comp_depth across the planned set — used by the "
                    "modal's progress bar threshold + by Phase C/D hooks "
                    "that walk depth-aware logic.")

    # ── Convenience ──────────────────────────────────────────────────

    @property
    def is_empty(self) -> bool:
        """True if the planner found nothing to duplicate. Pipeline
        callers use this to skip the modal entirely."""
        return not self.duplicates

    def active_duplicates(self) -> List[PrecompDuplicate]:
        """Duplicates Babysitter will actually execute (will_be_skipped
        filtered out). Used by the live progress UI to compute totals."""
        return [d for d in self.duplicates if not d.will_be_skipped]
