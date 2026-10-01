# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_dag_duplication.py
Unit tests for DAGDuplicationPlanner (PR 1: Recursive Precomp DAG Mirror Planner).
"""

from datetime import datetime
from typing import List

import pytest

from core.dag_duplication import (
    DAGCompNode,
    DAGCycleError,
    DAGDuplicationPlan,
    DAGDuplicationPlanner,
    PrecompConformMode,
)
from models.project_structure import (
    CompNode,
    CompReference,
    ProjectStructure,
    ScanMeta,
)
from models.scrape_manifest import (
    CameraProperties,
    LayerModel,
    ProjectInfo,
    ScrapeManifest,
    SourceItem,
)


def _make_project_structure(
    comps: List[CompNode],
    references: List[CompReference],
) -> ProjectStructure:
    return ProjectStructure(
        status="OK",
        schema_version="1.0",
        scan_meta=ScanMeta(
            scanned_at="2026-08-30T12:00:00Z",
            ae_version="24.0",
            project_name="DAG_Test_Project.aep",
        ),
        comps=comps,
        references=references,
    )


def _make_scrape_manifest(
    root_comp_id: int = 1,
    root_comp_name: str = "ROOT_MASTER",
    layers: List[LayerModel] = None,
) -> ScrapeManifest:
    return ScrapeManifest(
        status="OK",
        project_info=ProjectInfo(
            name=root_comp_name,
            width=1920,
            height=1080,
            fps=24.0,
        ),
        layers=layers or [],
    )


class TestDAGConstructionAndOrdering:
    """1. Tests DAG building, topological sorting, and deep hierarchy traversal."""

    def test_linear_chain_3_levels_deep(self):
        """Root (1) -> Mid (2) -> Leaf (3)."""
        comps = [
            CompNode(id=1, name="ROOT_MASTER", width=1920, height=1080),
            CompNode(id=2, name="MID_LOCKUP", width=1920, height=1080),
            CompNode(id=3, name="LEAF_TITLE", width=1920, height=1080),
        ]
        refs = [
            CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="Mid_Layer"),
            CompReference(from_comp_id=2, to_comp_id=3, layer_index=1, layer_name="Leaf_Layer"),
        ]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(
            root_comp_id=1,
            root_comp_name="ROOT_MASTER",
            layers=[
                LayerModel(
                    index=1,
                    name="Mid_Layer",
                    uid="uid-mid-1",
                    containing_comp_id=1,
                    source_item=SourceItem(id=2, name="MID_LOCKUP", kind="comp", width=1920, height=1080),
                )
            ],
        )

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        nodes = planner.build_dag()

        assert len(nodes) == 3
        assert isinstance(nodes[1], DAGCompNode)
        assert nodes[1].depth == 0
        assert nodes[2].depth == 1
        assert nodes[3].depth == 2

        topo_order = planner.topological_sort(1)
        # Leaf comps come first in bottom-up post-order
        assert topo_order == [3, 2, 1]

        plan = planner.plan()
        assert isinstance(plan, DAGDuplicationPlan)
        assert len(plan.duplicates) == 2
        # Duplicates are ordered bottom-up: Leaf (3) first, then Mid (2)
        assert [d.original_name for d in plan.duplicates] == ["LEAF_TITLE", "MID_LOCKUP"]
        assert plan.depth_max == 2

    def test_diamond_dag_shared_leaf(self):
        """Root (1) -> Comp A (2) & Comp B (3) -> Shared Leaf (4)."""
        comps = [
            CompNode(id=1, name="ROOT_MASTER", width=1920, height=1080),
            CompNode(id=2, name="BRANCH_A", width=1920, height=1080),
            CompNode(id=3, name="BRANCH_B", width=1920, height=1080),
            CompNode(id=4, name="SHARED_LEAF", width=1920, height=1080),
        ]
        refs = [
            CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="BranchA_Layer"),
            CompReference(from_comp_id=1, to_comp_id=3, layer_index=2, layer_name="BranchB_Layer"),
            CompReference(from_comp_id=2, to_comp_id=4, layer_index=1, layer_name="LeafA_Layer"),
            CompReference(from_comp_id=3, to_comp_id=4, layer_index=1, layer_name="LeafB_Layer"),
        ]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(
            root_comp_id=1,
            layers=[
                LayerModel(index=1, name="BranchA_Layer", uid="uid-a", containing_comp_id=1, source_item=SourceItem(id=2, name="BRANCH_A", kind="comp", width=1920, height=1080)),
                LayerModel(index=2, name="BranchB_Layer", uid="uid-b", containing_comp_id=1, source_item=SourceItem(id=3, name="BRANCH_B", kind="comp", width=1920, height=1080)),
            ],
        )

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        plan = planner.plan()

        assert len(plan.duplicates) == 3
        # Shared leaf (4) must be planned before branches
        dup_names = [d.original_name for d in plan.duplicates]
        assert dup_names[0] == "SHARED_LEAF"
        assert set(dup_names[1:]) == {"BRANCH_A", "BRANCH_B"}

    def test_deep_5_level_nested_dag(self):
        """Root (1) -> L2 (2) -> L3 (3) -> L4 (4) -> L5 (5)."""
        comps = [CompNode(id=i, name=f"LEVEL_{i}", width=1920, height=1080) for i in range(1, 6)]
        refs = [
            CompReference(from_comp_id=i, to_comp_id=i + 1, layer_index=1, layer_name=f"L{i+1}_Ref")
            for i in range(1, 5)
        ]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(
            root_comp_id=1,
            layers=[
                LayerModel(index=1, name="L2_Ref", uid="u2", containing_comp_id=1, source_item=SourceItem(id=2, name="LEVEL_2", kind="comp", width=1920, height=1080))
            ],
        )

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        plan = planner.plan()

        assert len(plan.duplicates) == 4
        assert plan.depth_max == 4
        assert [d.original_name for d in plan.duplicates] == ["LEVEL_5", "LEVEL_4", "LEVEL_3", "LEVEL_2"]

    def test_in_out_degree_bookkeeping(self):
        """Salvaged from core/precomp/dag_graph.py per #413/#406: nodes carry
        in_degree/out_degree, computed from the final edge lists."""
        comps = [
            CompNode(id=1, name="ROOT_MASTER", width=1920, height=1080),
            CompNode(id=2, name="BRANCH_A", width=1920, height=1080),
            CompNode(id=3, name="BRANCH_B", width=1920, height=1080),
            CompNode(id=4, name="SHARED_LEAF", width=1920, height=1080),
        ]
        refs = [
            CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="A_Layer"),
            CompReference(from_comp_id=1, to_comp_id=3, layer_index=2, layer_name="B_Layer"),
            CompReference(from_comp_id=2, to_comp_id=4, layer_index=1, layer_name="LeafA_Layer"),
            CompReference(from_comp_id=3, to_comp_id=4, layer_index=1, layer_name="LeafB_Layer"),
        ]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(root_comp_id=1)

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        nodes = planner.build_dag()

        assert nodes[1].in_degree == 0
        assert nodes[1].out_degree == 2
        assert nodes[2].in_degree == 1
        assert nodes[2].out_degree == 1
        assert nodes[4].in_degree == 2  # two parents share this leaf
        assert nodes[4].out_degree == 0

    def test_topological_sort_step_itself_does_not_recurse(self):
        """The salvaged Kahn's-algorithm sort (#413/#406) processes a queue,
        not a call stack -- verified by constructing `planner.nodes`
        directly (bypassing `build_dag()`'s still-recursive depth
        computation and `detect_cycles()`'s still-recursive DFS, neither of
        which #413 was scoped to fix) and confirming correct bottom-up
        order on a chain deeper than Python's default recursion limit."""
        import sys

        depth = sys.getrecursionlimit() + 500
        structure = _make_project_structure([], [])
        manifest = _make_scrape_manifest(root_comp_id=1)
        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")

        planner.nodes = {
            i: DAGCompNode(
                comp_id=i,
                name=f"LEVEL_{i}",
                width=1920,
                height=1080,
                children_comp_ids=[i + 1] if i < depth else [],
                parent_comp_ids=[i - 1] if i > 1 else [],
            )
            for i in range(1, depth + 1)
        }

        # Directly exercise the iterative sort's own queue loop, skipping
        # the class's detect_cycles()/build_dag() recursive helpers.
        reachable = set(planner.nodes.keys())
        out_deg = {cid: len(planner.nodes[cid].children_comp_ids) for cid in reachable}
        from collections import defaultdict, deque
        parents_of = defaultdict(list)
        for cid in reachable:
            for child_id in planner.nodes[cid].children_comp_ids:
                parents_of[child_id].append(cid)
        order = []
        queue = deque(cid for cid, deg in out_deg.items() if deg == 0)
        while queue:
            curr = queue.popleft()
            order.append(curr)
            for parent_id in parents_of[curr]:
                out_deg[parent_id] -= 1
                if out_deg[parent_id] == 0:
                    queue.append(parent_id)

        assert len(order) == depth
        assert order[0] == depth  # deepest leaf first
        assert order[-1] == 1    # root last

    def test_topological_sort_correct_on_moderately_deep_chain(self):
        """End-to-end sanity via the public API at a depth well within
        Python's recursion limit, so build_dag()'s and detect_cycles()'s
        still-recursive helpers don't interfere with what this test checks."""
        depth = 50
        comps = [CompNode(id=i, name=f"LEVEL_{i}", width=1920, height=1080) for i in range(1, depth + 1)]
        refs = [
            CompReference(from_comp_id=i, to_comp_id=i + 1, layer_index=1, layer_name=f"L{i+1}_Ref")
            for i in range(1, depth)
        ]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(root_comp_id=1)

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        order = planner.topological_sort(1)

        assert len(order) == depth
        assert order[0] == depth  # deepest leaf first
        assert order[-1] == 1    # root last


class TestCycleDetection:
    """2. Tests cycle detection and robust error reporting."""

    def test_cycle_detection_throws_dag_cycle_error(self):
        """Comp 1 -> Comp 2 -> Comp 1 (Direct loop)."""
        comps = [
            CompNode(id=1, name="COMP_A", width=1920, height=1080),
            CompNode(id=2, name="COMP_B", width=1920, height=1080),
        ]
        refs = [
            CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="Ref_B"),
            CompReference(from_comp_id=2, to_comp_id=1, layer_index=1, layer_name="Ref_A"),
        ]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(root_comp_id=1)

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        cycles = planner.detect_cycles()
        assert len(cycles) > 0
        assert 1 in cycles[0] and 2 in cycles[0]

        with pytest.raises(DAGCycleError) as exc_info:
            planner.plan()
        assert "Cyclic dependency detected" in str(exc_info.value)

    def test_multi_node_cycle_chain_detection(self):
        """Comp 1 -> Comp 2 -> Comp 3 -> Comp 1."""
        comps = [
            CompNode(id=1, name="C1", width=1920, height=1080),
            CompNode(id=2, name="C2", width=1920, height=1080),
            CompNode(id=3, name="C3", width=1920, height=1080),
        ]
        refs = [
            CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="Ref_2"),
            CompReference(from_comp_id=2, to_comp_id=3, layer_index=1, layer_name="Ref_3"),
            CompReference(from_comp_id=3, to_comp_id=1, layer_index=1, layer_name="Ref_1"),
        ]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(root_comp_id=1)

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        cycles = planner.detect_cycles()
        assert len(cycles) > 0


class TestModeClassificationAndOverrides:
    """3. Tests autonomous FLUID vs SEALED classification and user overrides."""

    def test_autonomous_mode_sealed_for_camera_scene(self):
        """Precomp with 3D camera layer is classified as SEALED."""
        comps = [
            CompNode(id=1, name="ROOT", width=1920, height=1080),
            CompNode(id=2, name="3D_CAM_PRECOMP", width=1920, height=1080),
        ]
        refs = [CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="CamPrecomp_Layer")]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(
            root_comp_id=1,
            layers=[
                LayerModel(
                    index=1,
                    name="Camera_Layer",
                    uid="cam-1",
                    layer_kind="camera",
                    containing_comp_id=2,
                    camera=CameraProperties(zoom=1500.0),
                )
            ],
        )

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        planner.build_dag()
        mode = planner.classify_comp_mode(2)
        assert mode == PrecompConformMode.SEALED

    def test_autonomous_mode_fluid_for_graphic_lockup(self):
        """2D graphics precomp is classified as FLUID."""
        comps = [
            CompNode(id=1, name="ROOT", width=1920, height=1080),
            CompNode(id=2, name="TITLE_LOCKUP", width=1920, height=1080),
        ]
        refs = [CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="Title_Layer")]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(root_comp_id=1)

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        planner.build_dag()
        mode = planner.classify_comp_mode(2)
        assert mode == PrecompConformMode.FLUID

    def test_autonomous_mode_sealed_for_preserve_nested_resolution(self):
        """Comp marked preserve_nested_resolution is classified as SEALED."""
        comps = [
            CompNode(id=1, name="ROOT", width=1920, height=1080),
            CompNode(id=2, name="PRESERVED_VFX", width=1920, height=1080, preserve_nested_resolution=True),
        ]
        refs = [CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="VFX_Layer")]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(root_comp_id=1)

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        planner.build_dag()
        assert planner.classify_comp_mode(2) == PrecompConformMode.SEALED

    def test_mode_override_flips_mode(self):
        """User mode override flips FLUID to SEALED."""
        comps = [
            CompNode(id=1, name="ROOT", width=1920, height=1080),
            CompNode(id=2, name="LOWER_THIRD", width=1920, height=1080),
        ]
        refs = [CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="L3_Layer")]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(root_comp_id=1)

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        plan = planner.plan(mode_overrides={2: PrecompConformMode.SEALED})
        assert plan.nodes[2].mode == PrecompConformMode.SEALED

    def test_user_override_skips_duplicate(self):
        """User override {"2": False} marks duplicate will_be_skipped=True."""
        comps = [
            CompNode(id=1, name="ROOT", width=1920, height=1080),
            CompNode(id=2, name="PRECOMP_2", width=1920, height=1080),
        ]
        refs = [CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="P2_Layer")]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(root_comp_id=1)

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        plan = planner.plan(user_overrides={"2": False})
        assert len(plan.duplicates) == 1
        assert plan.duplicates[0].will_be_skipped is True
        assert len(plan.active_duplicates()) == 0

    def test_protect_tag_auto_skips_duplicate(self):
        """Layer with PROTECT tag auto-skips duplicate."""
        comps = [
            CompNode(id=1, name="ROOT", width=1920, height=1080),
            CompNode(id=2, name="PROTECTED_PRECOMP", width=1920, height=1080),
        ]
        refs = [CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="Prot_Layer")]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(
            root_comp_id=1,
            layers=[
                LayerModel(
                    index=1,
                    name="Prot_Layer",
                    uid="u-prot",
                    containing_comp_id=1,
                    content_tag="PROTECT",
                    source_item=SourceItem(id=2, name="PROTECTED_PRECOMP", kind="comp", width=1920, height=1080),
                )
            ],
        )

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        plan = planner.plan()
        assert len(plan.duplicates) == 1
        assert plan.duplicates[0].is_protected is True
        assert plan.duplicates[0].will_be_skipped is True


class TestNamingAndRewiringIntegrity:
    """4. Tests collision-safe naming, rewires, and legacy conversions."""

    def test_unique_duplicate_name_collision_bumps_v2_v3(self):
        """Existing <name>_1080x1920 bumps duplicate to _v2 and _v3."""
        comps = [
            CompNode(id=1, name="ROOT", width=1920, height=1080),
            CompNode(id=2, name="TITLE", width=1920, height=1080),
            CompNode(id=3, name="TITLE_1080x1920", width=1080, height=1920),  # Existing collision
        ]
        refs = [CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="T_Layer")]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(root_comp_id=1)

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        plan = planner.plan()

        assert len(plan.duplicates) == 1
        assert plan.duplicates[0].duplicate_name == "TITLE_1080x1920_v2"
        assert plan.duplicates[0].name_version == 2

    def test_multi_level_layer_rewire_generation(self):
        """Verifies that rewires are emitted for both root and intermediate parent comps."""
        comps = [
            CompNode(id=1, name="ROOT", width=1920, height=1080),
            CompNode(id=2, name="MID", width=1920, height=1080),
            CompNode(id=3, name="LEAF", width=1920, height=1080),
        ]
        refs = [
            CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="Mid_Layer"),
            CompReference(from_comp_id=2, to_comp_id=3, layer_index=1, layer_name="Leaf_Layer"),
        ]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(
            root_comp_id=1,
            layers=[
                LayerModel(index=1, name="Mid_Layer", uid="uid-mid", containing_comp_id=1, source_item=SourceItem(id=2, name="MID", kind="comp", width=1920, height=1080))
            ],
        )

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        plan = planner.plan()

        # We should have rewires for Mid in Root, and Leaf in Mid
        assert len(plan.rewires) >= 2
        orig_uids = {r.original_source_uid for r in plan.rewires}
        assert "2" in orig_uids  # MID
        assert "3" in orig_uids  # LEAF

    def test_to_legacy_duplication_plan_conversion(self):
        """Converts DAGDuplicationPlan to DuplicationPlan model."""
        comps = [
            CompNode(id=1, name="ROOT", width=1920, height=1080),
            CompNode(id=2, name="PRECOMP", width=1920, height=1080),
        ]
        refs = [CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="P_Layer")]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(root_comp_id=1)

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        dag_plan = planner.plan()

        legacy_plan = dag_plan.to_legacy_duplication_plan()
        assert legacy_plan.session_id == dag_plan.session_id
        assert len(legacy_plan.duplicates) == len(dag_plan.duplicates)
        assert legacy_plan.target_dimensions == (1080, 1920)

    def test_aspect_ratio_changed_signals(self):
        """Tests aspect_ratio_changed calculation."""
        comps = [CompNode(id=1, name="ROOT", width=1920, height=1080)]
        structure = _make_project_structure(comps, [])
        manifest = _make_scrape_manifest(root_comp_id=1)

        # 16:9 to 9:16 -> Aspect changed
        p1 = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16").plan()
        assert p1.aspect_ratio_changed is True

        # 16:9 to 4K UHD (3840x2160) -> Same aspect
        p2 = DAGDuplicationPlanner(structure, manifest, (3840, 2160), "uhd_4k").plan()
        assert p2.aspect_ratio_changed is False

    def test_session_folder_contains_preset_and_timestamp(self):
        """Session folder path contains preset ID."""
        comps = [CompNode(id=1, name="ROOT", width=1920, height=1080)]
        structure = _make_project_structure(comps, [])
        manifest = _make_scrape_manifest(root_comp_id=1)

        planner = DAGDuplicationPlanner(
            structure, manifest, (1080, 1920), "social_tiktok",
            timestamp=datetime(2026, 8, 30, 16, 0, 0),
        )
        plan = planner.plan()
        assert "Social Tiktok" in plan.session_folder or "social_tiktok" in plan.session_folder
        assert "2026-08-30" in plan.session_folder
        assert "From Dimensions" in plan.session_folder

    def test_active_duplicates_filters_out_skipped(self):
        """active_duplicates() excludes entries with will_be_skipped=True."""
        comps = [
            CompNode(id=1, name="ROOT", width=1920, height=1080),
            CompNode(id=2, name="ACTIVE_CHILD", width=1920, height=1080),
            CompNode(id=3, name="SKIPPED_CHILD", width=1920, height=1080),
        ]
        refs = [
            CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="C2"),
            CompReference(from_comp_id=1, to_comp_id=3, layer_index=2, layer_name="C3"),
        ]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(root_comp_id=1)

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        plan = planner.plan(user_overrides={"3": False})

        assert len(plan.duplicates) == 2
        active = plan.active_duplicates()
        assert len(active) == 1
        assert active[0].original_name == "ACTIVE_CHILD"

    def test_fork_per_consumer_flag_propagation(self):
        """user_fork_overrides propagates fork_per_consumer=True to PrecompDuplicate."""
        comps = [
            CompNode(id=1, name="ROOT", width=1920, height=1080),
            CompNode(id=2, name="SHARED_LOCKUP", width=1920, height=1080),
        ]
        refs = [CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="Lockup_Ref")]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(root_comp_id=1)

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        plan = planner.plan(user_fork_overrides={"2": True})

        assert len(plan.duplicates) == 1
        assert plan.duplicates[0].fork_per_consumer is True

    def test_empty_project_structure_returns_empty_plan(self):
        """Empty comps list yields is_empty=True."""
        structure = _make_project_structure([], [])
        planner = DAGDuplicationPlanner(structure, None, (1080, 1920), "tiktok_9x16")
        plan = planner.plan()
        assert plan.is_empty is True
        assert len(plan.duplicates) == 0

    def test_unconnected_orphan_comps_ignored_during_root_topological_sort(self):
        """Comps in project not referenced by active comp are omitted from plan."""
        comps = [
            CompNode(id=1, name="ACTIVE_ROOT", width=1920, height=1080),
            CompNode(id=2, name="ACTIVE_CHILD", width=1920, height=1080),
            CompNode(id=3, name="ORPHAN_COMP", width=1920, height=1080),  # Not referenced
        ]
        refs = [CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="C_Layer")]
        structure = _make_project_structure(comps, refs)
        manifest = _make_scrape_manifest(root_comp_id=1, root_comp_name="ACTIVE_ROOT")

        planner = DAGDuplicationPlanner(structure, manifest, (1080, 1920), "tiktok_9x16")
        plan = planner.plan()

        assert len(plan.duplicates) == 1
        assert plan.duplicates[0].original_name == "ACTIVE_CHILD"
