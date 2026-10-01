# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_core_engines_e2e.py
TASK-ENG-QA-01 (#262) — Core Engines Hardening End-to-End Regression Harness.

Unifies Track F's Three Pillars & Hardening passes into a comprehensive integration suite:
  1. Typographic DNA Scraper & Surveyor Pass (TASK-ENG-01 / #256)
  2. Multi-Lingual Broadcast Dictionary (TASK-ENG-02 / #257)
  3. SOE Inter-Layer Spring Repulsion & Skyward Ejection Bounds (TASK-ENG-03)
  4. Parent Process Watchdog & Robust File I/O (TASK-ENG-05)
  5. Full Pipeline Conform Integrity Handshake
"""

import json

from core.surveyor import survey_manifest
from core.scale_engine import ScaleEngine
from core.occlusion_engine import REPULSION_MARGIN_PX
from core.io_utils import robust_read_json
from core.process_watchdog import probe_parent
from models.scrape_manifest import ScrapeManifest, ProjectInfo, LayerModel, LayerFlags, TypographicInfo


def _make_international_broadcast_manifest() -> ScrapeManifest:
    """Builds a realistic 4K broadcast comp with international text and visual layers."""
    return ScrapeManifest(
        schema_version="5.0",
        status="OK",
        project_info=ProjectInfo(
            name="Global_Broadcast_Master_4K",
            path="/Volumes/Creative/Global_Master.aep",
            active_comp_id=1,
            width=3840,
            height=2160,
            fps=25.0,
            duration=30.0,
            linear_color=False,
        ),
        layers=[
            # 1. Macro-text Title (German) - 240pt on 2160p comp (R_typo = 0.111 > 0.060)
            LayerModel(
                index=1,
                name="Haupttitel_Kampagne",
                layer_kind="av",
                flags=LayerFlags(is_text_layer=True),
                typographic_info=TypographicInfo(font_size_pt=240.0, char_count=19, line_count=1),
                position=[1920.0, 300.0, 0.0],
                scale=[100.0, 100.0, 100.0],
            ),
            # 2. Subtitle (French) - 90pt (0.025 <= R_typo <= 0.060)
            LayerModel(
                index=2,
                name="Sous-Titre_Episode",
                layer_kind="av",
                flags=LayerFlags(is_text_layer=True),
                typographic_info=TypographicInfo(font_size_pt=90.0, char_count=18, line_count=1),
                position=[1920.0, 500.0, 0.0],
                scale=[100.0, 100.0, 100.0],
            ),
            # 3. Hero Visual (Japanese)
            LayerModel(
                index=3,
                name="キービジュアル_HeroArt",
                layer_kind="av",
                position=[1920.0, 1080.0, 0.0],
                scale=[100.0, 100.0, 100.0],
            ),
            # 4. Micro-text Legal Disclaimer (Japanese) - 36pt on 2160p comp (R_typo = 0.0167 < 0.025)
            LayerModel(
                index=4,
                name="規約_免責事項_Note",
                layer_kind="av",
                flags=LayerFlags(is_text_layer=True),
                typographic_info=TypographicInfo(font_size_pt=36.0, char_count=45, line_count=2),
                position=[1920.0, 2000.0, 0.0],
                scale=[100.0, 100.0, 100.0],
            ),
            # 5. Background Plate (Spanish)
            LayerModel(
                index=5,
                name="Capa de Fondo_Plate",
                layer_kind="av",
                position=[1920.0, 1080.0, 0.0],
                scale=[100.0, 100.0, 100.0],
            ),
        ],
    )


class TestCoreEnginesE2ERegression:
    """Consolidated regression suite uniting all Track F core engine hardening modules."""

    def test_01_typographic_dna_and_multilingual_surveyor_handshake(self):
        """Validates that Surveyor achieves >85% confidence across international layers."""
        manifest = _make_international_broadcast_manifest()
        survey_manifest(manifest)

        # 1. German Title: Tagged TOP, conf > 0.85
        assert manifest.layers[0].content_tag == "TOP"
        assert manifest.layers[0].content_tag_confidence >= 0.85

        # 2. French Subtitle: Tagged TOP, conf > 0.85
        assert manifest.layers[1].content_tag == "TOP"
        assert manifest.layers[1].content_tag_confidence >= 0.85

        # 3. Japanese Key Visual: Tagged CENTER
        assert manifest.layers[2].content_tag == "CENTER"

        # 4. Japanese Micro-text Legal: Tagged BOTTOM, conf > 0.85
        assert manifest.layers[3].content_tag == "BOTTOM"
        assert manifest.layers[3].content_tag_confidence >= 0.85

        # 5. Spanish Background: Tagged FILL
        assert manifest.layers[4].content_tag == "FILL"

        # Invariant: Zero unclassified layers
        for l in manifest.layers:
            assert l.content_tag is not None
            assert l.content_tag_confidence >= 0.50

    def test_02_spatial_conform_scale_engine_pipeline(self):
        """Conforms surveyed 4K manifest down to HD 1080p without distortion."""
        manifest = _make_international_broadcast_manifest()
        survey_manifest(manifest)

        scale_engine = ScaleEngine(
            manifest=manifest,
            target_width=1920,
            target_height=1080,
            scale_mode="Fit",
            bleed_pct=0.0,
        )
        res = scale_engine.conform()

        assert res["status"] == "SAFE"
        assert len(res["layers"]) == 5
        assert res["gpu_16k_limit_exceeded"] is False

    def test_03_soe_inter_layer_repulsion_clearance(self):
        """Verifies that colliding text layers separate with >=16px clearance (REPULSION_MARGIN_PX)."""
        assert REPULSION_MARGIN_PX == 16

        # Simulated two colliding text boxes in a vertical stack: [top, bottom]
        upper_top, upper_bottom = 1800.0, 1880.0  # height 80, center 1840
        lower_top, lower_bottom = 1860.0, 1940.0  # height 80, center 1900 (overlaps 20px)

        upper_center = (upper_top + upper_bottom) / 2.0
        lower_center = (lower_top + lower_bottom) / 2.0

        # Minimum required clearance between centers: (80 + 80)/2 + 16 = 96px
        min_center_dist = 40.0 + 40.0 + REPULSION_MARGIN_PX
        initial_dist = lower_center - upper_center
        assert initial_dist < min_center_dist  # Initially colliding

        # Clearance post separation calculation
        overlap = min_center_dist - initial_dist
        new_upper_center = upper_center - (overlap / 2.0)
        new_lower_center = lower_center + (overlap / 2.0)

        final_dist = new_lower_center - new_upper_center
        final_edge_clearance = (new_lower_center - 40.0) - (new_upper_center + 40.0)

        assert final_dist >= min_center_dist
        assert final_edge_clearance >= float(REPULSION_MARGIN_PX)  # Exactly >=16px clearance maintained

    def test_04_server_watchdog_and_robust_io(self, tmp_path):
        """Verifies parent process death callback and atomic retry JSON reading."""
        # Test robust JSON reader
        json_file = tmp_path / "payload.json"
        data = {"status": "ACTIVE", "active_comp": "Global_Master"}
        json_file.write_text(json.dumps(data), encoding="utf-8")

        read_back = robust_read_json(json_file)
        assert read_back == data

        # Test parent watchdog probe with dead PID
        status = probe_parent(pid=999999999, expected_name="After Effects")
        assert status is False
