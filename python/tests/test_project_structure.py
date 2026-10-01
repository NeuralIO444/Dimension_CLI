# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_project_structure.py
v5.7 — coverage for the ProjectStructure model + ProjectStructureAnalyzer.

Tests build small synthetic ProjectStructure scans (no AE required) and
exercise:
  - schema round-trip (model_dump_json → model_validate_json)
  - graph queries (precomps_of / parents_of / descendants / ancestors)
  - role classification (roots / orphans / shared_precomps / render_targets)
  - depth + max_depth on chains, diamonds, and cycles
  - cycle detection
  - one_shot summary() shape
  - from_file() loader
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


def _make_structure(comps, references, *, render_target_ids=()):
    """Build a ProjectStructure dict from compact tuple inputs."""
    from models.project_structure import (
        CompNode,
        CompReference,
        ProjectStructure,
        ScanMeta,
    )

    comp_nodes = []
    rt = set(render_target_ids)
    for cid, name, w, h in comps:
        comp_nodes.append(CompNode(
            id=cid,
            name=name,
            width=w,
            height=h,
            fps=23.976,
            duration=10.0,
            pixel_aspect=1.0,
            bg_color=[0.0, 0.0, 0.0],
            layer_count=5,
            is_render_target=(cid in rt),
            folder_path="",
        ))
    refs = [
        CompReference(
            from_comp_id=f, to_comp_id=t,
            layer_index=li, layer_name=ln,
        )
        for (f, t, li, ln) in references
    ]
    return ProjectStructure(
        status="OK",
        schema_version="1.0",
        scan_meta=ScanMeta(
            scanned_at="2026-04-24T00:00:00Z",
            ae_version="24.0",
            project_name="test.aep",
        ),
        comps=comp_nodes,
        references=refs,
    )


# ── Model ───────────────────────────────────────────────────────────


class TestProjectStructureModel:
    def test_round_trip_json(self):
        from models.project_structure import ProjectStructure

        structure = _make_structure(
            comps=[(1, "MAIN", 3840, 2160), (2, "BG", 1920, 1080)],
            references=[(1, 2, 3, "BG_layer")],
            render_target_ids=(1,),
        )
        as_json = structure.model_dump_json()
        rehydrated = ProjectStructure.model_validate_json(as_json)
        assert rehydrated.status == "OK"
        assert rehydrated.schema_version == "1.0"
        assert len(rehydrated.comps) == 2
        assert len(rehydrated.references) == 1
        assert rehydrated.comps[0].is_render_target is True

    def test_comps_by_id_indexes_correctly(self):
        structure = _make_structure(
            comps=[(10, "A", 100, 100), (20, "B", 200, 200)],
            references=[],
        )
        idx = structure.comps_by_id()
        assert set(idx.keys()) == {10, 20}
        assert idx[10].name == "A"
        assert idx[20].name == "B"

    def test_layer_index_must_be_positive(self):
        """AE layer indices are 1-based — schema enforces ge=1."""
        from models.project_structure import CompReference
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CompReference(
                from_comp_id=1, to_comp_id=2,
                layer_index=0, layer_name="bad",
            )


# ── Analyzer: basic graph ──────────────────────────────────────────


class TestAnalyzerGraph:
    def test_empty_project(self):
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        analyzer = ProjectStructureAnalyzer(
            _make_structure(comps=[], references=[])
        )
        assert analyzer.comp_count == 0
        assert analyzer.reference_count == 0
        assert analyzer.roots() == []
        assert analyzer.orphans() == []
        assert analyzer.shared_precomps() == []
        assert analyzer.max_depth() == 0
        assert analyzer.cycles() == []

    def test_precomps_of_and_parents_of(self):
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        # MAIN(1) → BG(2), MAIN(1) → LOGO(3)
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "MAIN", 0, 0), (2, "BG", 0, 0), (3, "LOGO", 0, 0)],
            references=[(1, 2, 1, "bg"), (1, 3, 2, "logo")],
        ))
        assert analyzer.precomps_of(1) == {2, 3}
        assert analyzer.precomps_of(2) == set()
        assert analyzer.parents_of(2) == {1}
        assert analyzer.parents_of(3) == {1}
        assert analyzer.parents_of(1) == set()

    def test_descendants_and_ancestors_transitive(self):
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        # 1 → 2 → 3 → 4 (chain)
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "A", 0, 0), (2, "B", 0, 0),
                   (3, "C", 0, 0), (4, "D", 0, 0)],
            references=[
                (1, 2, 1, "x"),
                (2, 3, 1, "y"),
                (3, 4, 1, "z"),
            ],
        ))
        assert analyzer.descendants(1) == {2, 3, 4}
        assert analyzer.descendants(3) == {4}
        assert analyzer.descendants(4) == set()
        assert analyzer.ancestors(4) == {1, 2, 3}
        assert analyzer.ancestors(1) == set()

    def test_unknown_reference_dropped_defensively(self):
        """If references mention a comp not in `comps`, the edge is
        silently dropped — corrupt scans must not crash the analyzer."""
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "MAIN", 0, 0)],
            references=[(1, 999, 1, "ghost")],   # 999 doesn't exist
        ))
        assert analyzer.precomps_of(1) == set()


# ── Analyzer: roles ─────────────────────────────────────────────────


class TestAnalyzerRoles:
    def test_render_targets(self):
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "MAIN_4K", 0, 0), (2, "MAIN_HD", 0, 0), (3, "BG", 0, 0)],
            references=[(1, 3, 1, "bg"), (2, 3, 1, "bg")],
            render_target_ids=(1, 2),
        ))
        assert sorted(analyzer.render_targets()) == [1, 2]

    def test_roots_includes_render_targets_and_unreferenced(self):
        """A root is anything in the render queue OR not referenced
        from above. Both go to the same bucket because both are
        natural starting points for a top-down walk."""
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        # 1 → 2 (1 is render target, 2 has parent so not root).
        # 3 floats free (no parents, no render queue) — still a root.
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "MAIN", 0, 0), (2, "BG", 0, 0), (3, "FLOAT", 0, 0)],
            references=[(1, 2, 1, "bg")],
            render_target_ids=(1,),
        ))
        assert analyzer.roots() == [1, 3]

    def test_orphans_unreferenced_and_not_in_queue(self):
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "MAIN", 0, 0), (2, "BG", 0, 0), (3, "DEAD", 0, 0)],
            references=[(1, 2, 1, "bg")],
            render_target_ids=(1,),
        ))
        # DEAD(3) has no parents and isn't a render target — orphan.
        # MAIN(1) has no parents but IS a render target — not orphan.
        # BG(2) has a parent — not orphan.
        assert analyzer.orphans() == [3]

    def test_shared_precomps_two_or_more_parents(self):
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        # MAIN_4K(1) → BG(3); MAIN_HD(2) → BG(3); MAIN_4K(1) → LOGO(4).
        # BG is shared (2 parents); LOGO is not (1 parent).
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "M4K", 0, 0), (2, "MHD", 0, 0),
                   (3, "BG", 0, 0), (4, "LOGO", 0, 0)],
            references=[
                (1, 3, 1, "bg"), (2, 3, 1, "bg"),
                (1, 4, 2, "logo"),
            ],
        ))
        assert analyzer.shared_precomps() == [3]

    def test_shared_precomps_sorted_by_parent_count_desc(self):
        """When multiple precomps are shared, the one used by more
        parents comes first — duplication-engine UX wants the biggest
        fan-in surfaced first."""
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        # BG(10) used by 1, 2, 3.  LOGO(20) used by 1, 2.
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "A", 0, 0), (2, "B", 0, 0), (3, "C", 0, 0),
                   (10, "BG", 0, 0), (20, "LOGO", 0, 0)],
            references=[
                (1, 10, 1, "bg"), (2, 10, 1, "bg"), (3, 10, 1, "bg"),
                (1, 20, 2, "logo"), (2, 20, 2, "logo"),
            ],
        ))
        assert analyzer.shared_precomps() == [10, 20]


# ── Analyzer: depth ────────────────────────────────────────────────


class TestAnalyzerDepth:
    def test_leaf_depth_zero(self):
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "A", 0, 0)],
            references=[],
        ))
        assert analyzer.comp_depth(1) == 0
        assert analyzer.max_depth() == 0

    def test_chain_depth(self):
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        # 1 → 2 → 3 → 4
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "A", 0, 0), (2, "B", 0, 0),
                   (3, "C", 0, 0), (4, "D", 0, 0)],
            references=[(1, 2, 1, "x"), (2, 3, 1, "y"), (3, 4, 1, "z")],
        ))
        assert analyzer.comp_depth(4) == 0
        assert analyzer.comp_depth(3) == 1
        assert analyzer.comp_depth(2) == 2
        assert analyzer.comp_depth(1) == 3
        assert analyzer.max_depth() == 3

    def test_diamond_depth(self):
        """Diamond: 1 → 2, 1 → 3, 2 → 4, 3 → 4. Depth(1) = 2."""
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "A", 0, 0), (2, "B", 0, 0),
                   (3, "C", 0, 0), (4, "D", 0, 0)],
            references=[
                (1, 2, 1, "b"), (1, 3, 2, "c"),
                (2, 4, 1, "d"), (3, 4, 1, "d"),
            ],
        ))
        assert analyzer.comp_depth(1) == 2
        assert analyzer.max_depth() == 2

    def test_depth_handles_cycle_without_infinite_loop(self):
        """If two comps reference each other (impossible in vanilla AE
        but defensive coverage), comp_depth must still return."""
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "A", 0, 0), (2, "B", 0, 0)],
            references=[(1, 2, 1, "b"), (2, 1, 1, "a")],
        ))
        # Should return finite, not stack-overflow.
        d = analyzer.comp_depth(1)
        assert isinstance(d, int)
        assert d >= 1


# ── Analyzer: cycles ───────────────────────────────────────────────


class TestAnalyzerCycles:
    def test_no_cycles_in_dag(self):
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "A", 0, 0), (2, "B", 0, 0), (3, "C", 0, 0)],
            references=[(1, 2, 1, "b"), (2, 3, 1, "c"), (1, 3, 2, "c")],
        ))
        assert analyzer.cycles() == []

    def test_two_node_cycle(self):
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "A", 0, 0), (2, "B", 0, 0)],
            references=[(1, 2, 1, "b"), (2, 1, 1, "a")],
        ))
        cycles = analyzer.cycles()
        assert len(cycles) == 1
        assert sorted(cycles[0]) == [1, 2]

    def test_three_node_cycle(self):
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "A", 0, 0), (2, "B", 0, 0), (3, "C", 0, 0)],
            references=[(1, 2, 1, "b"), (2, 3, 1, "c"), (3, 1, 1, "a")],
        ))
        cycles = analyzer.cycles()
        assert len(cycles) == 1
        assert sorted(cycles[0]) == [1, 2, 3]


# ── Analyzer: summary ──────────────────────────────────────────────


class TestAnalyzerSummary:
    def test_summary_shape(self):
        from core.project_structure_analyzer import ProjectStructureAnalyzer
        analyzer = ProjectStructureAnalyzer(_make_structure(
            comps=[(1, "MAIN", 0, 0), (2, "BG", 0, 0), (3, "LOGO", 0, 0)],
            references=[(1, 2, 1, "bg"), (1, 3, 2, "logo")],
            render_target_ids=(1,),
        ))
        summary = analyzer.summary()
        assert summary["comp_count"] == 3
        assert summary["reference_count"] == 2
        assert summary["render_targets"] == [1]
        assert summary["roots"] == [1]
        assert summary["orphans"] == []
        assert summary["max_depth"] == 1
        assert summary["cycles"] == []


# ── Loader ─────────────────────────────────────────────────────────


class TestFromFile:
    def test_from_file_round_trip(self, tmp_path):
        from core.project_structure_analyzer import ProjectStructureAnalyzer

        structure = _make_structure(
            comps=[(1, "MAIN", 0, 0), (2, "BG", 0, 0)],
            references=[(1, 2, 1, "bg")],
            render_target_ids=(1,),
        )
        path = tmp_path / "project_structure.json"
        path.write_text(structure.model_dump_json(), encoding="utf-8")
        analyzer = ProjectStructureAnalyzer.from_file(path)
        assert analyzer.comp_count == 2
        assert analyzer.precomps_of(1) == {2}
