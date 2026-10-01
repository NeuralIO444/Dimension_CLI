# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_presets_2_0_e2e.py
TASK-P2-QA-01 (#254) — End-to-End TDD Test Harness for Presets 2.0 & OOH Slicing.

Verifies the complete Presets 2.0 technical pipeline across:
  1. Multi-Panel Triptych Slicing Engine (ADR 01 / TASK-P2-01)
  2. Framerate & Timebase Conformance Engine (ADR 02 / TASK-P2-02)
  3. Silent Audio Stripping & Output Module Presetting (ADR 03 / TASK-P2-03)
  4. GPU 16K Texture Allocation Safety Guard (Scenario 1 / TASK-P2-04)
  5. Full Unified Lifecycle Integration Simulation
"""

import math

from core.panel_slicer import PanelSlicingPlanner, MultiPanelSpec
from core.framerate_engine import FramerateEngine, FrameratePlan
from core.render_queue import RenderQueuePlanner, RenderQueueConfig
from core.scale_engine import ScaleEngine
from models.scrape_manifest import ScrapeManifest, ProjectInfo, LayerModel


def _make_source_manifest(w=1920, h=1080, fps=23.976, duration=10.01001) -> ScrapeManifest:
    return ScrapeManifest(
        schema_version="5.0",
        status="OK",
        project_info=ProjectInfo(
            name="Market_Transit_Master",
            path="/Volumes/Creative/Transit_Master.aep",
            active_comp_id=1,
            width=w,
            height=h,
            fps=fps,
            duration=duration,
            linear_color=False,
        ),
        layers=[
            LayerModel(
                index=1,
                name="HERO_TrainLogo",
                layer_kind="av",
                content_tag="HERO",
                position=[960.0, 540.0, 0.0],
                scale=[100.0, 100.0, 100.0],
            ),
            LayerModel(
                index=2,
                name="BG_StationPlate",
                layer_kind="av",
                content_tag="FILL",
                position=[960.0, 540.0, 0.0],
                scale=[100.0, 100.0, 100.0],
            ),
        ],
    )


class TestPresets20EndToEnd:
    """Consolidated End-to-End integration suite for Presets 2.0."""

    def test_01_multi_panel_slicing_math_market_15_liveboard(self):
        """ADR 01: Slices 3-panel Market 15 Liveboard (1080x1920 panels, 221px gaps)."""
        spec = MultiPanelSpec(
            panel_count=3,
            panel_width=1080,
            panel_height=1920,
            gap_px=221,
            split_naming=["LEFT", "CENTER", "RIGHT"],
        )
        plan = PanelSlicingPlanner(spec, "Market_15").plan()

        # 1. Total Canvas Width: 3 * 1080 + 2 * 221 = 3682
        assert plan.master_width == 3682
        assert plan.master_height == 1920

        # 2. Child comp X-offsets: 540, -761, -2062
        assert len(plan.panels) == 3
        assert plan.panels[0].master_layer_position == (540.0, 960.0)
        assert plan.panels[1].master_layer_position == (-761.0, 960.0)
        assert plan.panels[2].master_layer_position == (-2062.0, 960.0)

        # 3. Gap zones for physical pillar occlusion
        assert len(plan.gap_zones) == 2
        assert plan.gap_zones[0].x_start == 1080
        assert plan.gap_zones[0].x_end == 1301
        assert plan.gap_zones[1].x_start == 2381
        assert plan.gap_zones[1].x_end == 2602

    def test_02_framerate_duration_snapping_film_to_stadium_led(self):
        """ADR 02: Conforming 23.976 film master to 59.94 high-refresh stadium timebase."""
        src_fps = 24000 / 1001
        tgt_fps = 60000 / 1001
        src_duration = 240 / src_fps  # exactly 240 frames (~10.01001s)

        plan = FramerateEngine.plan_conformance(
            source_fps=src_fps,
            source_duration_s=src_duration,
            target_fps=tgt_fps,
        )

        assert isinstance(plan, FrameratePlan)
        assert plan.source_frame_count == 240
        assert plan.target_frame_count == 600  # Exactly 600 integer frames
        assert math.isclose(plan.target_duration_s, 600 / tgt_fps, abs_tol=1e-7)
        # Invariant: Layer stretch remains 100.0%
        assert plan.layer_stretch_factor == 100.0

    def test_03_silent_audio_invariant_and_render_queue_delivery(self):
        """ADR 03: Silent audio invariant and defensive render queue template mapping."""
        ooh_spec = {
            "spec_id": "OOH-064",
            "video_codec": "ProRes 4444",
            "audio": False,
            "alpha": True,
        }
        rq_cfg = RenderQueuePlanner.plan_for_spec(ooh_spec)

        assert isinstance(rq_cfg, RenderQueueConfig)
        assert rq_cfg.disable_audio is True
        assert rq_cfg.render_alpha is True
        assert rq_cfg.template_name == "ProRes 4444 with Alpha"
        assert rq_cfg.file_extension == ".mov"

        opts = rq_cfg.to_extendscript_options(output_path="/Volumes/SAN/OOH-064.mov")
        assert opts["disableAudio"] is True
        assert opts["templateName"] == "ProRes 4444 with Alpha"

    def test_04_gpu_16k_texture_limit_protection(self):
        """Scenario 1: Massive displays (>16K) trigger GPU allocation guard."""
        manifest = _make_source_manifest()

        # Under 16K: Safe
        eng_under = ScaleEngine(manifest, 3682, 1920, "Fit", 0.0)
        assert eng_under.gpu_16k_limit_exceeded is False

        # Over 16K: 21,360px ribbon triggers guard
        eng_over = ScaleEngine(manifest, 21360, 48, "Fit", 0.0)
        assert eng_over.gpu_16k_limit_exceeded is True
        res = eng_over.conform()
        assert res["gpu_16k_limit_exceeded"] is True
        assert res["warnings"]["gpu_16k_limit"] is True

    def test_05_unified_lifecycle_integration(self):
        """Full lifecycle conform test connecting all 4 Presets 2.0 subsystems."""
        # 1. Source comp
        manifest = _make_source_manifest()

        # 2. Timebase conformance
        fps_plan = FramerateEngine.plan_conformance(
            source_fps=manifest.project_info.fps,
            source_duration_s=manifest.project_info.duration,
            target_fps=59.94,
        )
        assert fps_plan.target_frame_count == 600

        # 3. Multi-panel slicing
        slicing_spec = MultiPanelSpec(
            panel_count=3,
            panel_width=1080,
            panel_height=1920,
            gap_px=221,
            split_naming=["LEFT", "CENTER", "RIGHT"],
        )
        slicing_plan = PanelSlicingPlanner(slicing_spec, "Campaign_01").plan()
        assert slicing_plan.master_width == 3682

        # 4. Spatial conform into Master Design Comp dimensions
        scale_engine = ScaleEngine(
            manifest,
            target_width=slicing_plan.master_width,
            target_height=slicing_plan.master_height,
            scale_mode="Fit",
            bleed_pct=0.0,
        )
        conformed = scale_engine.conform()
        assert conformed["status"] == "SAFE"
        assert len(conformed["layers"]) == 2
        assert conformed["gpu_16k_limit_exceeded"] is False

        # 5. Render queue and silent audio plan
        ooh_delivery = {
            "spec_id": "OOH-064",
            "video_codec": "ProRes 4444",
            "audio": False,
        }
        rq_cfg = RenderQueuePlanner.plan_for_spec(ooh_delivery)
        assert rq_cfg.disable_audio is True

        # Pipeline handshake verification
        assert slicing_plan.master_width == scale_engine.target_width
        assert slicing_plan.master_height == scale_engine.target_height
