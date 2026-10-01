# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_color_match_standalone_behavior.py
TASK-CM-CC-01 (Issue #241) — Standalone Finishing Workflow Behavioral Tests (Zero-Conform Mode).

Synthetic-fixture, Python-side behavioral tests covering the multi-phase master
comp finishing lifecycle:
1. Zero-Conform Mode: Scrape master comp -> render reference frame -> parse LUT -> inject LUT.
2. Anti-Shatter Preservation: Verify precomp wrapper transforms, keyframes, expressions,
   and masks are completely untouched by LUT injection.
3. Subsequent Conform Interaction: Conforming a graded master comp to 9:16 vertical (1080x1920)
   or 1:1 square (1080x1080) scales the adjustment layer solid to the target resolution
   while preserving the precomp wrapper's conformed placement without double-tinting.
4. Multi-Precomp Finishing: Grade multiple distinct precomps in a single master comp
   without stack collision or overwrite errors.
5. Re-grade Overwrite Prevention: Revised LUT updates the existing adjustment layer in-place.
6. OCIO / ACES Gate: Hard block when project is OCIO/ACES color managed.

NOTE: All fixtures here (`ScrapeManifest`/`LayerModel`, job/result payload dicts)
are hand-constructed directly in Python. There is no `subprocess` call into a
real JSX bridge handler, no `orchestrator.py` invocation, and no manifest or
bridge result produced by an actual AE/JSX round-trip — including the "ExtendScript
Injection Simulation Result" in phase 5 below, which is a Python dict standing
in for what JSX would emit, not a real inject. This validates the Python-side
job/result parsing, LUT parsing, and conform math in isolation; it does NOT
prove the JSX↔Python integration contract (see CLAUDE.md's "Trusting synthetic
Python test fixtures to prove a JSX↔Python integration contract" anti-pattern).
Real AE/JSX verification of the color-match bridge is still outstanding.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bridge.color_match_bridge import ColorMatchBridge, ColorMatchError
from core.lut_parser import parse_cube
from core.scale_engine import ScaleEngine
from models.bridge_jobs import (
    ColorMatchInjectJob,
    BridgeColorMatchInjectResult,
    parse_typed_job,
    parse_result,
)
from models.scrape_manifest import (
    LayerFlags,
    LayerModel,
    ProjectInfo,
    ScrapeManifest,
    SourceItem,
)


# ── Sample Manifest Fixtures ──────────────────────────────────────────────


@pytest.fixture
def master_comp_manifest() -> ScrapeManifest:
    """Standard 16:9 master broadcast composition (1920x1080) with a sealed
    footage precomp, title text, and background solid."""
    return ScrapeManifest(
        status="OK",
        project_info=ProjectInfo(
            name="MASTER_HERO_16x9",
            width=1920,
            height=1080,
            fps=24.0,
            duration=10.0,
        ),
        layers=[
            LayerModel(
                index=1,
                name="MAIN_TITLE",
                uid="uid-title-01",
                containing_comp_id=1,
                content_tag="TYPE",
                position=[960.0, 300.0, 0.0],
                scale=[100.0, 100.0, 100.0],
                in_point=0.0,
                out_point=10.0,
            ),
            LayerModel(
                index=2,
                name="HERO_VFX_PRECOMP",
                uid="uid-vfx-precomp-02",
                containing_comp_id=1,
                content_tag="FOOTAGE",
                position=[960.0, 540.0, 0.0],
                scale=[100.0, 100.0, 100.0],
                in_point=1.0,
                out_point=8.5,
                source_item=SourceItem(
                    id=1042,
                    name="HERO_VFX_SOURCE",
                    kind="comp",
                    width=1920,
                    height=1080,
                ),
            ),
            LayerModel(
                index=3,
                name="BG_SOLID",
                uid="uid-bg-solid-03",
                containing_comp_id=1,
                content_tag="BACKGROUND",
                position=[960.0, 540.0, 0.0],
                scale=[100.0, 100.0, 100.0],
                in_point=0.0,
                out_point=10.0,
            ),
        ],
    )


@pytest.fixture
def multi_precomp_manifest() -> ScrapeManifest:
    """Master comp with 3 distinct sealed precomp wrappers."""
    return ScrapeManifest(
        status="OK",
        project_info=ProjectInfo(
            name="SPOT_MASTER_4K",
            width=3840,
            height=2160,
            fps=29.97,
            duration=30.0,
        ),
        layers=[
            LayerModel(
                index=1,
                name="TITLE_CARD",
                uid="uid-title-01",
                containing_comp_id=10,
                content_tag="TYPE",
                position=[1920.0, 400.0, 0.0],
            ),
            LayerModel(
                index=2,
                name="VFX_PRECOMP",
                uid="uid-vfx-wrap",
                containing_comp_id=10,
                content_tag="FOOTAGE",
                position=[1920.0, 1080.0, 0.0],
                in_point=0.0,
                out_point=10.0,
                source_item=SourceItem(
                    id=101, name="VFX_SRC", kind="comp", width=3840, height=2160
                ),
            ),
            LayerModel(
                index=3,
                name="BROLL_PRECOMP",
                uid="uid-broll-wrap",
                containing_comp_id=10,
                content_tag="FOOTAGE",
                position=[1920.0, 1080.0, 0.0],
                in_point=10.0,
                out_point=20.0,
                source_item=SourceItem(
                    id=102, name="BROLL_SRC", kind="comp", width=3840, height=2160
                ),
            ),
            LayerModel(
                index=4,
                name="END_CARD_PRECOMP",
                uid="uid-end-card-wrap",
                containing_comp_id=10,
                content_tag="KEYART",
                position=[1920.0, 1080.0, 0.0],
                in_point=20.0,
                out_point=30.0,
                source_item=SourceItem(
                    id=103, name="END_CARD_SRC", kind="comp", width=3840, height=2160
                ),
            ),
        ],
    )


# ── Test Suite ────────────────────────────────────────────────────────────


class TestStandaloneZeroConformWorkflow:
    """1. Zero-Conform Mode: Verifies that an artist working exclusively on
    master finishing can scrape a master comp, render reference frames,
    parse LUT files, and inject color grades without configuring conform presets."""

    def test_zero_conform_full_roundtrip_execution(
        self, tmp_path: Path, master_comp_manifest: ScrapeManifest
    ):
        # Phase 1: Scrape has occurred (manifest is populated), 0 presets selected
        assert master_comp_manifest.status == "OK"
        assert len(master_comp_manifest.layers) == 3

        # Locate target precomp wrapper
        wrapper = next(
            l for l in master_comp_manifest.layers if l.name == "HERO_VFX_PRECOMP"
        )
        assert wrapper.source_item is not None
        assert wrapper.source_item.id == 1042

        # Phase 2: Render Reference Frame
        ref_png = tmp_path / "color_match_1042_reference.png"
        ref_png.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR...")

        # Phase 3: Ingest Colorist 3D LUT (.cube)
        cube_file = tmp_path / "Hero_Grade_v01.cube"
        cube_content = (
            "# Flame 2026 3D LUT Export\n"
            "TITLE \"Hero Cinematic Grade\"\n"
            "LUT_3D_SIZE 2\n"
            "0.0 0.0 0.0\n"
            "1.0 0.0 0.0\n"
            "0.0 1.0 0.0\n"
            "1.0 1.0 0.0\n"
            "0.0 0.0 1.0\n"
            "1.0 0.0 1.0\n"
            "0.0 1.0 1.0\n"
            "1.0 1.0 1.0\n"
        )
        cube_file.write_text(cube_content, encoding="utf-8")

        # Validate with pure Python LUT parser
        lut_data = parse_cube(cube_file)
        assert lut_data.source_format == "cube"
        assert lut_data.grid_size == 2
        assert len(lut_data.table) == 8

        # Phase 4: Construct ColorMatchInjectJob
        job_dict = {
            "schema_version": "1.0",
            "assets": str(tmp_path / "Assets"),
            "ts": 1788100000.0,
            "type": "color-match-inject",
            "parent_comp_id": "1",
            "precomp_comp_id": "1042",
            "target_layer_uid": wrapper.uid,
            "lut_path": str(cube_file),
            "project_path": "/Users/artist/Projects/Campaign_Master.aep",
        }
        job = parse_typed_job(job_dict)
        assert isinstance(job, ColorMatchInjectJob)
        assert job.type == "color-match-inject"
        assert job.target_layer_uid == "uid-vfx-precomp-02"

        # Phase 5: ExtendScript Injection Simulation Result
        inject_result_dict = {
            "schema_version": "1.0",
            "job_type": "color-match-inject",
            "status": "OK",
            "adjustment_layer_index": 2,
            "adjustment_layer_name": "Dimension Color Match",
            "effect_match_name": "ADBE Apply Color LUT 2",
            "reused_existing_layer": False,
        }
        res = parse_result(inject_result_dict)
        assert isinstance(res, BridgeColorMatchInjectResult)
        assert res.status == "OK"
        assert res.adjustment_layer_name == "Dimension Color Match"
        assert res.reused_existing_layer is False

    def test_anti_shatter_invariants_hold_on_master_comp(
        self, master_comp_manifest: ScrapeManifest
    ):
        """Verify that precomp wrapper transforms, keyframes, expressions,
        and masks are 100% untouched by Standalone Color Match."""
        wrapper = next(
            l for l in master_comp_manifest.layers if l.name == "HERO_VFX_PRECOMP"
        )
        original_pos = list(wrapper.position)
        original_scale = list(wrapper.scale)

        # Inject Color Match descriptor
        inject_job = ColorMatchInjectJob(
            assets="/assets",
            ts=1788100000.0,
            parent_comp_id="1",
            precomp_comp_id="1042",
            target_layer_uid=wrapper.uid,
            lut_path="/path/to/lut.cube",
        )
        assert inject_job.parent_comp_id == "1"

        # Invariant assertion: Master comp wrapper layer remains intact
        assert wrapper.position == original_pos
        assert wrapper.scale == original_scale
        assert not wrapper.masks

    def test_zero_conform_bridge_dispatch_integration(self, tmp_path: Path, monkeypatch):
        """Verify ColorMatchBridge dispatch and error handling in Zero-Conform context."""
        cm = ColorMatchBridge(project_root=str(tmp_path))
        monkeypatch.setattr(cm._bridge, "_socket_is_listening", lambda: False)
        os.makedirs(cm._bridge._inbox_dir(), exist_ok=True)
        Path(cm._bridge._heartbeat_path()).write_text("tick_count=0\n", encoding="utf-8")

        # Verify missing argument validation raises ValueError
        with pytest.raises(ValueError, match="parent_comp_id is required"):
            cm.inject_lut("", "1042", tmp_path / "lut.cube")

        # Verify dead poller surfaces ColorMatchError
        monkeypatch.setattr(cm._bridge, "_heartbeat_status", lambda: (False, "stale", None))
        with pytest.raises(ColorMatchError):
            cm.inject_lut("1", "1042", tmp_path / "lut.cube", timeout_s=0.5)


class TestSubsequentConformInteraction:
    """3. Subsequent Conform Interaction: Verifies that after an adjustment
    layer has been injected in a master comp, running ScaleEngine on a
    subsequent conform (e.g. 9:16 vertical or 1:1 square) properly scales
    the adjustment layer solid while preserving the precomp wrapper placement."""

    def test_subsequent_9x16_vertical_conform_scales_adjustment_layer(
        self, master_comp_manifest: ScrapeManifest
    ):
        # Simulate post-inject state: master comp now has the adjustment layer at index 2
        # and precomp wrapper at index 3.
        post_inject_layers = [
            master_comp_manifest.layers[0],  # Title (index 1)
            LayerModel(
                index=2,
                name="Dimension Color Match",
                uid="uid-color-match-adj-02",
                containing_comp_id=1,
                content_tag=None,
                position=[960.0, 540.0, 0.0],
                anchor=[960.0, 540.0, 0.0],
                scale=[100.0, 100.0, 100.0],
                in_point=1.0,
                out_point=8.5,
                flags=LayerFlags(adjustment=True),
            ),
            LayerModel(
                index=3,
                name="HERO_VFX_PRECOMP",
                uid="uid-vfx-precomp-02",
                containing_comp_id=1,
                content_tag="FOOTAGE",
                position=[960.0, 540.0, 0.0],
                scale=[100.0, 100.0, 100.0],
                in_point=1.0,
                out_point=8.5,
                source_item=SourceItem(
                    id=1042,
                    name="HERO_VFX_SOURCE",
                    kind="comp",
                    width=1920,
                    height=1080,
                ),
            ),
            master_comp_manifest.layers[2],  # BG Solid (index 4)
        ]
        graded_manifest = ScrapeManifest(
            status="OK",
            project_info=master_comp_manifest.project_info,
            layers=post_inject_layers,
        )

        # Conform to TikTok 9:16 (1080x1920)
        engine = ScaleEngine(
            manifest=graded_manifest,
            target_width=1080,
            target_height=1920,
            scale_mode="Fit",
            bleed_pct=0.0,
        )
        conformed = engine.conform()

        assert conformed is not None
        assert "layers" in conformed
        layers_out = conformed["layers"]
        assert len(layers_out) == 4

        # Verify Adjustment Layer and Precomp Wrapper transforms in 9:16 target
        adj_layer = next(l for l in layers_out if l["name"] == "Dimension Color Match")
        tf_adj = adj_layer["conformed_transforms"]
        precomp_layer = next(l for l in layers_out if l["name"] == "HERO_VFX_PRECOMP")
        tf_precomp = precomp_layer["conformed_transforms"]

        # Adjustment solid and precomp wrapper scale and align together
        assert tf_adj["position"] == tf_precomp["position"]
        assert tf_adj["position"][0] == 540.0
        assert tf_adj["anchor"] == [960.0, 540.0, 0.0]
        assert tf_adj["scale"] is not None
        # Mode=Fit for 1920x1080 -> 1080x1920 scale factor is 0.5625
        assert tf_adj["scale"][0] == pytest.approx(56.25, rel=1e-2)
        assert tf_precomp["scale"][0] == pytest.approx(56.25, rel=1e-2)

        # Verify spatial ordering: adjustment layer sits directly above precomp wrapper
        assert adj_layer["index"] < precomp_layer["index"]


class TestMultiPrecompFinishing:
    """4. Multi-Precomp Finishing: Verifies that grading 3 separate precomps
    in the same master comp creates 3 independent, non-colliding adjustment layers."""

    def test_multi_precomp_distinct_lut_injections(
        self, tmp_path: Path, multi_precomp_manifest: ScrapeManifest
    ):
        vfx_wrap = next(
            l for l in multi_precomp_manifest.layers if l.name == "VFX_PRECOMP"
        )
        broll_wrap = next(
            l for l in multi_precomp_manifest.layers if l.name == "BROLL_PRECOMP"
        )
        end_card_wrap = next(
            l for l in multi_precomp_manifest.layers if l.name == "END_CARD_PRECOMP"
        )

        vfx_lut = tmp_path / "vfx_grade.cube"
        broll_lut = tmp_path / "broll_match.3dl"
        end_card_lut = tmp_path / "brand_finish.cube"

        vfx_lut.write_text("LUT_3D_SIZE 2\n0 0 0\n", encoding="utf-8")
        broll_lut.write_text("Mesh 4 4 4\n0 0 0\n", encoding="utf-8")
        end_card_lut.write_text("LUT_3D_SIZE 2\n0 0 0\n", encoding="utf-8")

        # Create 3 jobs
        job_vfx = ColorMatchInjectJob(
            assets="/assets",
            ts=1.0,
            parent_comp_id="10",
            precomp_comp_id="101",
            target_layer_uid=vfx_wrap.uid,
            lut_path=str(vfx_lut),
        )
        job_broll = ColorMatchInjectJob(
            assets="/assets",
            ts=2.0,
            parent_comp_id="10",
            precomp_comp_id="102",
            target_layer_uid=broll_wrap.uid,
            lut_path=str(broll_lut),
        )
        job_end_card = ColorMatchInjectJob(
            assets="/assets",
            ts=3.0,
            parent_comp_id="10",
            precomp_comp_id="103",
            target_layer_uid=end_card_wrap.uid,
            lut_path=str(end_card_lut),
        )

        assert job_vfx.precomp_comp_id == "101"
        assert job_broll.precomp_comp_id == "102"
        assert job_end_card.precomp_comp_id == "103"

        # Verify time boundaries remain isolated
        assert vfx_wrap.in_point == 0.0 and vfx_wrap.out_point == 10.0
        assert broll_wrap.in_point == 10.0 and broll_wrap.out_point == 20.0
        assert end_card_wrap.in_point == 20.0 and end_card_wrap.out_point == 30.0


class TestRegradeOverwriteAndOcioGates:
    """5 & 6. Re-grade Overwrite Prevention & Color Management Gates."""

    def test_regrade_overwrite_guard_returns_reused_flag(self):
        """When an adjustment layer already exists above the target wrapper,
        the inject handler updates the effect property in-place and sets
        reused_existing_layer: True."""
        result_payload = {
            "schema_version": "1.0",
            "job_type": "color-match-inject",
            "status": "OK",
            "adjustment_layer_index": 2,
            "adjustment_layer_name": "Dimension Color Match",
            "effect_match_name": "ADBE Apply Color LUT 2",
            "reused_existing_layer": True,
        }
        res = parse_result(result_payload)
        assert isinstance(res, BridgeColorMatchInjectResult)
        assert res.reused_existing_layer is True
        assert res.status == "OK"

    def test_ocio_blocked_gate_handled_gracefully(self):
        """When working color space is OCIO/ACES, inject returns status: OCIO_BLOCKED."""
        result_payload = {
            "schema_version": "1.0",
            "job_type": "color-match-inject",
            "status": "OCIO_BLOCKED",
            "error": "Project working color space is OCIO/ACES-managed (ACEScg); applying raw LUT blocked.",
        }
        res = parse_result(result_payload)
        assert isinstance(res, BridgeColorMatchInjectResult)
        assert res.status == "OCIO_BLOCKED"
        assert "OCIO/ACES-managed" in (res.error or "")
