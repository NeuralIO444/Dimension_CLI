# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_placement_units.py — U1 diagnostic placement-unit tree.

The builder must read a real comp's design the way the artist drew it:
87N's flowchart (2026-07-02 session) is root → camera scene (camera +
5 outlines + logo wrapper) + fill plates, with the nesting-doll chain
HD_10 → Pre_Neon_logo as sealed precomp units. Verified on the live
session fixture, per the anti-synthetic-fixture rule.

U1 is diagnostic-only: nothing here is consumed by conform math. The
builder must never raise (degenerate report with `error` set instead).
"""

from __future__ import annotations

import json
import os
import sys


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from models.scrape_manifest import ScrapeManifest  # noqa: E402
from core.placement_units import build_placement_units  # noqa: E402

_FRESH = os.path.join(os.path.dirname(__file__), "fixtures",
                      "session_2026_07_02", "87n_fresh_manifest.json")
_BUG_L = os.path.join(os.path.dirname(__file__), "fixtures",
                      "bug_l", "87n_source_manifest.json")


def _load(path):
    with open(path) as f:
        return ScrapeManifest.model_validate(json.load(f))


def _by_kind(report, kind):
    return [u for u in report.units if u.kind == kind]


class TestFreshSessionFixture:
    def setup_method(self):
        self.report = build_placement_units(_load(_FRESH))

    def test_builder_clean(self):
        assert self.report.error is None
        assert self.report.summary

    def test_camera_scene_holds_the_lockup(self):
        scenes = _by_kind(self.report, "camera_scene")
        assert len(scenes) == 1
        names = {m.name for m in scenes[0].members}
        assert "Camera 1" in names
        for outline in ("EVERYTHING  Outlines", "YOU WANT Outlines",
                        "IS ON THE Outlines", "OTHER SIDE Outlines",
                        "OF FEAR Outlines"):
            assert outline in names, f"{outline} must be in the camera scene"
        assert "87N_Reels_DEV_87Neon_HD_10_mc" in names, (
            "the 3D logo wrapper is part of the scene")
        assert "preserve" in scenes[0].anchor_auto

    def test_nesting_doll_chain(self):
        """The flowchart truth: root → HD_10 precomp → Pre_Neon precomp."""
        precomps = {u.unit_id: u for u in _by_kind(self.report, "precomp")}
        assert len(precomps) == 2
        parent = next(u for u in precomps.values()
                      if "HD_10" in u.label)
        child = next(u for u in precomps.values()
                     if "Pre_Neon" in u.label)
        assert child.unit_id in parent.child_unit_ids, (
            "inner doll must be a child of the outer doll, not of root")
        root = next(u for u in self.report.units if u.kind == "root_frame")
        assert parent.unit_id in root.child_unit_ids
        assert child.unit_id not in root.child_unit_ids

    def test_precomps_are_sealed(self):
        for u in _by_kind(self.report, "precomp"):
            assert "sealed" in u.anchor_auto

    def test_fill_plates_outside_the_scene(self):
        """EFX / grunge / 87N.mov are 2D fills — they must not be pulled
        into the camera scene."""
        scenes = _by_kind(self.report, "camera_scene")
        scene_names = {m.name for m in scenes[0].members}
        for plate in ("EFX", "87N.mov"):
            assert plate not in scene_names
        all_member_names = {m.name for u in self.report.units
                            for m in u.members}
        assert "EFX" in all_member_names, "fills still get a unit"

    def test_every_layer_lands_in_exactly_one_root_partition(self):
        """No layer may be orphaned or double-placed. Precomp units
        describe comp INTERNALS; the root partition covers root layers."""
        manifest = _load(_FRESH)
        root_cid = manifest.layers[0].containing_comp_id
        root_keys = {(l.containing_comp_id, l.index)
                     for l in manifest.layers
                     if l.containing_comp_id == root_cid}
        placed = []
        for u in self.report.units:
            if u.kind in ("camera_scene", "group", "singleton"):
                placed.extend((m.comp_id, m.index) for m in u.members)
        assert sorted(placed) == sorted(root_keys), (
            "root partition must cover every root layer exactly once")


class TestBugLFixture:
    def test_camera_scene_detected(self):
        report = build_placement_units(_load(_BUG_L))
        assert report.error is None
        scenes = _by_kind(report, "camera_scene")
        assert len(scenes) == 1
        assert any(m.name == "Camera 1" for m in scenes[0].members)


class TestNeverRaises:
    def test_empty_manifest(self):
        report = build_placement_units(
            ScrapeManifest.model_validate({
                "status": "OK",
                "project_info": {"name": "X", "width": 1920,
                                 "height": 1080, "fps": 24.0},
                "layers": [],
            }))
        assert report.error is None
        assert report.summary == "no layers"

    def test_garbage_input_degrades_loudly(self):
        report = build_placement_units(object())
        assert report.units == [] or report.error is None


class TestReportCard:
    def test_units_render_in_report(self, tmp_path):
        from logic.report_generator import generate_report
        report = build_placement_units(_load(_FRESH))
        path = generate_report(
            conformed_manifest={"layers": [], "warnings": {}},
            scrape_manifest={"project_info": {"name": "T"}},
            preset_label="TikTok", target_w=1080, target_h=1920,
            scale_mode="Fit", uniform_scale=1.0, session_id="s",
            output_path=str(tmp_path / "r.html"),
            placement_units=report.model_dump(),
        )
        html = open(path, encoding="utf-8").read()
        assert "How Dimension Read This Comp" in html
        assert "Camera scene" in html
        assert "sealed" in html

    def test_no_card_without_units(self, tmp_path):
        from logic.report_generator import generate_report
        path = generate_report(
            conformed_manifest={"layers": [], "warnings": {}},
            scrape_manifest={"project_info": {"name": "T"}},
            preset_label="TikTok", target_w=1080, target_h=1920,
            scale_mode="Fit", uniform_scale=1.0, session_id="s",
            output_path=str(tmp_path / "r.html"),
        )
        html = open(path, encoding="utf-8").read()
        assert "How Dimension Read This Comp" not in html
