# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_comp_cleaner_adapter.py
Issue #350 -- comp_cleaner_adapter.py bridges the real ProjectStructure
shape (comps + top-level references edge list) into the legacy shape
core/comp_cleaner.py expects.

The critical risk this closes: comp_cleaner.py's own existing tests
(test_comp_cleaner.py) and Pillar 63 both hand-build the legacy shape
by hand, so nothing has ever proven the reference-count graph survives
contact with real data. If the adapter silently drops references, every
Dimension-duplicate-named comp gets flagged an orphan -- including ones
actively referenced by other comps -- and the eventual delete feature
(a separate, deliberately-deferred piece) would destroy real work. This
file exists specifically to prove that doesn't happen, using a REAL
captured project_structure.json for the main contract test, not a
synthetic Python fixture (CLAUDE.md's own documented anti-pattern for
JSX<->Python integration contracts).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.comp_cleaner import CompCleaner
from logic.comp_cleaner_adapter import adapt_project_structure_for_comp_cleaner
from models.project_structure import CompNode, CompReference, ProjectStructure, ScanMeta

_REAL_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures" / "session_2026_07_02" / "parallax_project_structure.json"
)


def _load_real_fixture() -> ProjectStructure:
    with open(_REAL_FIXTURE) as f:
        raw = json.load(f)
    return ProjectStructure.model_validate(raw)


def _minimal_scan_meta() -> ScanMeta:
    return ScanMeta(scanned_at="2026-09-03T00:00:00Z", project_name="test.aep")


class TestAdapterAgainstRealFixture:
    """Proves the adapter doesn't crash or silently drop data against a
    REAL captured project_structure.json, not synthetic Python data."""

    def test_produces_one_item_per_real_comp(self):
        ps = _load_real_fixture()
        legacy = adapt_project_structure_for_comp_cleaner(ps)
        assert len(legacy["items"]) == len(ps.comps) == 63

    def test_reference_graph_is_actually_populated(self):
        """The whole point of this adapter -- confirms real reference
        data (134 edges in this fixture) survives into the legacy
        layers[].source_comp_id shape comp_cleaner.py reads."""
        ps = _load_real_fixture()
        legacy = adapt_project_structure_for_comp_cleaner(ps)
        total_synthetic_layers = sum(len(item["layers"]) for item in legacy["items"])
        assert total_synthetic_layers == len(ps.references) == 134

    def test_a_real_referenced_comp_has_nonzero_ref_count_through_the_full_pipeline(self):
        """Run the REAL fixture all the way through CompCleaner.analyze_project
        (not just the adapter in isolation) and confirm at least one
        comp that's a real reference target ends up with a nonzero
        reference count -- proving the full chain, not just one hop."""
        ps = _load_real_fixture()
        legacy = adapt_project_structure_for_comp_cleaner(ps)
        # Force every comp to match the orphan-candidate naming pattern
        # so analyze_project's reference-count bookkeeping is exercised
        # for all of them, then check specifically the ones we know
        # from ps.references are real reference targets.
        plan = CompCleaner.analyze_project(legacy, custom_pattern=".*")

        referenced_ids = {ref.to_comp_id for ref in ps.references}
        protected_ids_with_refs = {
            p["comp_id"] for p in plan.protected_comps
            if p["reason"] == "REFERENCED_BY_LAYERS"
        }
        # At least the comps we know are reference targets must show up
        # protected for that exact reason -- if the adapter silently
        # dropped references, this set would be empty.
        assert referenced_ids & protected_ids_with_refs, (
            "expected at least one real reference target to be protected "
            "with reason=REFERENCED_BY_LAYERS -- adapter may be dropping "
            "reference data"
        )


class TestCriticalFalseOrphanRegression:
    """The exact failure mode flagged as dangerous on issue #350: a
    Dimension-duplicate-named comp that IS referenced must never be
    reported as an orphan. Synthetic, but deliberately minimal and
    clearly documenting the specific shape being tested -- not a stand-in
    for the real-fixture contract test above, a targeted addition to it."""

    def test_referenced_dim_dup_comp_is_not_orphaned(self):
        ps = ProjectStructure(
            status="OK",
            scan_meta=_minimal_scan_meta(),
            comps=[
                CompNode(id=1, name="Main_Comp", width=1920, height=1080, layer_count=1),
                CompNode(id=2, name="Background__dim_dup_01", width=1920, height=1080, layer_count=0),
            ],
            references=[
                CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="Background"),
            ],
        )
        legacy = adapt_project_structure_for_comp_cleaner(ps)
        plan = CompCleaner.analyze_project(legacy)

        orphan_ids = {c.comp_id for c in plan.orphaned_candidates}
        assert 2 not in orphan_ids, (
            "Background__dim_dup_01 IS referenced by Main_Comp but got "
            "flagged as an orphan -- this is the exact silent-destruction "
            "risk issue #350 was raised to prevent"
        )
        protected_ids = {p["comp_id"] for p in plan.protected_comps}
        assert 2 in protected_ids

    def test_genuinely_unreferenced_dim_dup_comp_is_flagged(self):
        """The positive case -- a comp with the naming pattern and truly
        zero references must still be correctly identified, or the
        adapter would be useless in the other direction."""
        ps = ProjectStructure(
            status="OK",
            scan_meta=_minimal_scan_meta(),
            comps=[
                CompNode(id=1, name="Main_Comp", width=1920, height=1080, layer_count=0),
                CompNode(id=2, name="Orphan__dim_dup_01", width=1920, height=1080, layer_count=0),
            ],
            references=[],
        )
        legacy = adapt_project_structure_for_comp_cleaner(ps)
        plan = CompCleaner.analyze_project(legacy)

        orphan_ids = {c.comp_id for c in plan.orphaned_candidates}
        assert 2 in orphan_ids

    def test_render_queued_dim_dup_comp_is_protected_via_is_render_target(self):
        """is_render_target (the real field name) must correctly map to
        is_render_queued (the legacy field comp_cleaner.py reads) --
        the second field-name mismatch the original diagnosis flagged."""
        ps = ProjectStructure(
            status="OK",
            scan_meta=_minimal_scan_meta(),
            comps=[
                CompNode(id=1, name="Queued__dim_dup_01", width=1920, height=1080,
                         layer_count=0, is_render_target=True),
            ],
            references=[],
        )
        legacy = adapt_project_structure_for_comp_cleaner(ps)
        assert legacy["items"][0]["is_render_queued"] is True

        plan = CompCleaner.analyze_project(legacy)
        orphan_ids = {c.comp_id for c in plan.orphaned_candidates}
        assert 1 not in orphan_ids
        protected = next(p for p in plan.protected_comps if p["comp_id"] == 1)
        assert protected["reason"] == "RENDER_QUEUED"

    def test_active_comp_id_still_protects_correctly_through_the_adapter(self):
        ps = ProjectStructure(
            status="OK",
            scan_meta=_minimal_scan_meta(),
            comps=[
                CompNode(id=1, name="Active__dim_dup_01", width=1920, height=1080, layer_count=0),
            ],
            references=[],
        )
        legacy = adapt_project_structure_for_comp_cleaner(ps)
        plan = CompCleaner.analyze_project(legacy, active_comp_id=1)

        orphan_ids = {c.comp_id for c in plan.orphaned_candidates}
        assert 1 not in orphan_ids
        protected = next(p for p in plan.protected_comps if p["comp_id"] == 1)
        assert protected["reason"] == "ACTIVE_COMP"
