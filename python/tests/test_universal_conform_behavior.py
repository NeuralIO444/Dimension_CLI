# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_universal_conform_behavior.py
Universal Conform Behavioral Suite (PR 6: Grand Capstone).

Synthetic-fixture, Python-side behavioral tests covering all 5 pillars of the
Next-Gen Relayout & Bi-Directional Reconform Engine collaborating within the
in-process conform pipeline:
  1. Pillar 1: Recursive Precomp DAG Mirror Planner (dag_duplication.py)
  2. Pillar 2: World-Space Kinematics & Null Normalizer (kinematics.py)
  3. Pillar 3: Bi-Directional Reconform Engine & Center-World Fallback (scale_engine_widen.py)
  4. Pillar 4: Layer Archetypes & Artwork Boundary Engine (alpha_hull.py)
  5. Pillar 5: Adaptive AST Expression Normalizer (expression_rewriter.py)

NOTE: These fixtures are hand-constructed `ScrapeManifest`/`LayerModel` objects
built directly in Python — there is no `subprocess` call into a real JSX
scraper, no `orchestrator.py` invocation, and no manifest produced by an
actual AE scrape. This exercises the Python-side conform logic in isolation
and does NOT prove the JSX↔Python integration contract (see CLAUDE.md's
"Trusting synthetic Python test fixtures to prove a JSX↔Python integration
contract" anti-pattern). Real AE/JSX verification of these code paths is
still outstanding — see docs/qa/pending-live-ae-verification.md.
"""

import pytest

from core.alpha_hull import classify_layer_archetype
from core.aspect_strategy import AspectStrategy
from core.dag_duplication import DAGDuplicationPlanner
from core.expression_rewriter import ExpressionRewriter
from core.kinematics import KinematicSolver, decompose_affine_matrix
from core.scale_engine import ScaleEngine
from models.project_structure import CompNode, CompReference, ProjectStructure, ScanMeta
from models.scrape_manifest import (
    ArtworkBounds,
    CameraProperties,
    LayerArchetype,
    LayerModel,
    ProjectInfo,
    ScrapeManifest,
    SourceItem,
    TypographicInfo,
)


def _make_project_structure(comps, refs) -> ProjectStructure:
    return ProjectStructure(
        status="OK",
        schema_version="1.0",
        scan_meta=ScanMeta(scanned_at="2026-08-30T12:00:00Z", ae_version="24.0", project_name="Universal_E2E.aep"),
        comps=comps,
        references=refs,
    )


class TestUniversalConformPipelineIntegration:
    """Synthetic-fixture behavioral tests unifying all 5 pillars. These are
    Python-side unit/behavioral tests (hand-built manifests, no real JSX
    scrape or subprocess round-trip), not a JSX/AE integration or E2E suite."""

    def test_e2e_9x16_to_16x9_widescreen_with_nested_precomps_kinematics_and_expressions(self):
        """Behavior: Widen vertical comp to horizontal with parenting, expressions, and artwork bounds."""
        # 1. Setup Layer Manifest
        l_bg = LayerModel(index=1, name="BG_Solid", position=[540.0, 960.0, 0.0], scale=[100.0, 100.0, 100.0], content_tag="BG")
        l_null = LayerModel(index=2, name="Header_Gimbal_Null", position=[540.0, 300.0, 0.0], scale=[100.0, 100.0, 100.0])
        l_logo = LayerModel(
            index=3,
            name="Agency_Logo.ai",
            parent_index=2,
            position=[0.0, 50.0, 0.0],
            scale=[100.0, 100.0, 100.0],
            collapseTransformations=True,
            artwork_bounds=ArtworkBounds(left=20.0, top=10.0, width=160.0, height=80.0, centroid=[100.0, 50.0]),
        )
        l_title = LayerModel(
            index=4,
            name="Title_Text",
            typographic_info=TypographicInfo(font_size_pt=72.0, line_count=2, char_count=24),
            position=[540.0, 960.0, 0.0],
            content_tag="TYPE",
        )
        manifest = ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(name="Vertical_Master", width=1080, height=1920, fps=30.0),
            layers=[l_bg, l_null, l_logo, l_title],
        )

        # 2. Test Pillar 4 (Archetype Classification)
        assert classify_layer_archetype(l_logo) == LayerArchetype.VECTOR_2D
        assert classify_layer_archetype(l_title) == LayerArchetype.TYPE

        # 3. Test Pillar 3 (Conform Widen Strategy Dispatch)
        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()
        assert res["status"] == "SAFE"
        assert res["aspect_strategy"] == AspectStrategy.WIDEN.value
        assert len(res["layers"]) == 4

        # 4. Test Pillar 2 (Kinematic World-Space Position Solver)
        solver = KinematicSolver(manifest.layers, 1080, 1920, 1920, 1080, uniform_scale=0.5625)
        world_m = solver.compute_world_matrix(3)
        decomp = decompose_affine_matrix(world_m)
        # In source comp: Null at (540, 300) + child local (0, 50) = world (540, 350)
        assert abs(decomp["position"][0] - 540.0) < 1e-3
        assert abs(decomp["position"][1] - 350.0) < 1e-3

        # 5. Test Pillar 5 (AST Expression Normalization on Coordinates)
        rewriter = ExpressionRewriter(source_width=1080, source_height=1920, target_width=1920, target_height=1080)
        orig_expr = "wiggle(2, 40) + [540, 960];"
        rw_expr, mod, _ = rewriter.rewrite_expression(orig_expr)
        assert mod is True
        assert "[960, 540]" in rw_expr
        # 1-click comment backup
        backup_tag = rewriter.create_comment_backup(orig_expr)
        assert rewriter.restore_from_comment(f"UID:123 | {backup_tag}") == orig_expr

    def test_e2e_16x9_to_9x16_vertical_with_camera_scene_and_typography(self):
        """Behavior: Narrow horizontal comp to vertical with 3D camera and typography."""
        cam = LayerModel(
            index=1,
            name="Camera 1",
            uid="u-cam",
            layer_kind="camera",
            position=[960.0, 540.0, -1500.0],
            camera=CameraProperties(zoom=1500.0),
        )
        l_text = LayerModel(
            index=2,
            name="HERO_Headline",
            uid="u-hero",
            position=[960.0, 400.0, 0.0],
            scale=[100.0, 100.0, 100.0],
            content_tag="HERO",
        )
        manifest = ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(name="Horizontal_Master", width=1920, height=1080, fps=24.0),
            layers=[cam, l_text],
        )

        engine = ScaleEngine(manifest, target_width=1080, target_height=1920, scale_mode="Fit", bleed_pct=0.0, camera_depth_mode="K")
        res = engine.conform()

        assert res["status"] == "SAFE"
        assert res["aspect_strategy"] == AspectStrategy.NARROW.value
        cam_conf = res["layers"][0]["conformed_transforms"]["camera"]
        # Camera zoom in vertical Mode K scales by K = 1920/1080 = 1.777778
        assert cam_conf["zoom"] == pytest.approx(1500.0 * (1920.0 / 1080.0), abs=1e-1)

    def test_e2e_multi_level_diamond_precomp_hierarchy_duplication(self):
        """Behavior: Diamond DAG precomp hierarchy deduplicates shared leaf and builds plan."""
        # Graph: 1 -> [2, 3] -> 4 (shared leaf)
        comps = [
            CompNode(id=1, name="ROOT_MASTER", width=1920, height=1080),
            CompNode(id=2, name="LEFT_GRAPHICS", width=1920, height=1080),
            CompNode(id=3, name="RIGHT_GRAPHICS", width=1920, height=1080),
            CompNode(id=4, name="SHARED_LEAF_LOWER_THIRD", width=1920, height=1080),
        ]
        refs = [
            CompReference(from_comp_id=1, to_comp_id=2, layer_index=1, layer_name="Left_Comp"),
            CompReference(from_comp_id=1, to_comp_id=3, layer_index=2, layer_name="Right_Comp"),
            CompReference(from_comp_id=2, to_comp_id=4, layer_index=1, layer_name="Shared_Leaf_L"),
            CompReference(from_comp_id=3, to_comp_id=4, layer_index=1, layer_name="Shared_Leaf_R"),
        ]
        ps = _make_project_structure(comps, refs)
        manifest = ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(name="ROOT_MASTER", width=1920, height=1080),
            layers=[
                LayerModel(index=1, name="Left_Comp", containing_comp_id=1, source_item=SourceItem(id=2, name="LEFT_GRAPHICS", kind="comp", width=1920, height=1080)),
                LayerModel(index=2, name="Right_Comp", containing_comp_id=1, source_item=SourceItem(id=3, name="RIGHT_GRAPHICS", kind="comp", width=1920, height=1080)),
            ],
        )

        planner = DAGDuplicationPlanner(ps, manifest, (1080, 1920), "tiktok_9x16")
        topo_order = planner.topological_sort(1)
        # Shared leaf (4) must come before parents (2, 3) before root (1)
        assert topo_order.index(4) < topo_order.index(2)
        assert topo_order.index(4) < topo_order.index(3)
        assert topo_order.index(2) < topo_order.index(1)

        plan = planner.plan()
        assert len(plan.duplicates) == 3
        # Duplicate names bottom-up
        dup_names = [d.original_name for d in plan.duplicates]
        assert dup_names[0] == "SHARED_LEAF_LOWER_THIRD"
        assert set(dup_names[1:]) == {"LEFT_GRAPHICS", "RIGHT_GRAPHICS"}

    def test_e2e_degenerate_corrupt_manifest_recovers_to_center_world(self):
        """Behavior: Corrupt inputs (NaN, singular null, unparseable JS) safely recover."""
        # 1. Corrupt Layer with NaN Position
        l_corrupt = LayerModel(index=1, name="Corrupt_Layer", position=[540.0, 960.0, 0.0], scale=[100.0, 100.0, 100.0])
        l_corrupt.position = [float("nan"), 960.0, 0.0]
        manifest = ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(name="Corrupt_Comp", width=1080, height=1920),
            layers=[l_corrupt],
        )

        # ScaleEngine Center-World Fail-Safe
        engine = ScaleEngine(manifest, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()
        assert res["status"] == "SAFE"
        assert res["layers"][0]["conformed_transforms"]["position"][0] == 960.0
        assert res["layers"][0]["conformed_transforms"]["position"][1] == 540.0

        # Expression Fail-Safe Passthrough
        rewriter = ExpressionRewriter(1080, 1920, 1920, 1080)
        bad_js = "eval(unknown %%$# 999"
        rw, mod, reason = rewriter.rewrite_expression(bad_js)
        assert mod is False
        assert rw == bad_js

    def test_e2e_roundtrip_reversibility_100_percent_fidelity(self):
        """Behavior: HD (1920x1080) -> Vertical (1080x1920) -> HD (1920x1080) roundtrip."""
        l1 = LayerModel(index=1, name="Root_Center", position=[960.0, 540.0, 0.0], scale=[100.0, 100.0, 100.0])
        l2 = LayerModel(index=2, name="Offset_Item", position=[400.0, 300.0, 0.0], scale=[80.0, 80.0, 100.0])
        manifest_a = ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(name="HD_Comp", width=1920, height=1080),
            layers=[l1, l2],
        )

        # Forward: HD -> Vertical
        eng_fwd = ScaleEngine(manifest_a, target_width=1080, target_height=1920, scale_mode="Fit", bleed_pct=0.0)
        res_fwd = eng_fwd.conform()
        assert res_fwd["status"] == "SAFE"

        # Construct intermediate vertical manifest
        l1_v = LayerModel(index=1, name="Root_Center", position=res_fwd["layers"][0]["conformed_transforms"]["position"], scale=res_fwd["layers"][0]["conformed_transforms"]["scale"])
        l2_v = LayerModel(index=2, name="Offset_Item", position=res_fwd["layers"][1]["conformed_transforms"]["position"], scale=res_fwd["layers"][1]["conformed_transforms"]["scale"])
        manifest_v = ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(name="Vert_Comp", width=1080, height=1920),
            layers=[l1_v, l2_v],
        )

        # Reverse: Vertical -> HD
        eng_rev = ScaleEngine(manifest_v, target_width=1920, target_height=1080, scale_mode="Fit", bleed_pct=0.0)
        res_rev = eng_rev.conform()
        assert res_rev["status"] == "SAFE"

        # Assert Root Center matches within epsilon 0.005px
        p1 = res_rev["layers"][0]["conformed_transforms"]["position"]
        assert abs(p1[0] - 960.0) < 0.005
        assert abs(p1[1] - 540.0) < 0.005
