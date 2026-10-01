# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_u2_unit_routing.py — U2: the conform routed through the
placement-unit tree.

Phase 2: compute_placement_resolution must reproduce the legacy inline
pre-pass computations exactly (clusters, root cid, seal sets) on the two
AE-verified real fixtures — the resolution is a derivation change, not a
behavior change (goldens pin the output; this pins the intermediate).
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from models.scrape_manifest import ScrapeManifest  # noqa: E402
from core.scale_engine import ScaleEngine  # noqa: E402
from core.classify import (  # noqa: E402
    detect_3d_camera_scenes_by_comp,
    is_layer_root,
)
from core.layer_utils import canon_tag  # noqa: E402
from core.placement_units import (  # noqa: E402
    SPATIAL_CLUSTER_THRESHOLD_FRACTION,
    compute_placement_resolution,
    single_linkage_clusters,
)

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures",
                         "session_2026_07_02")
_87N = os.path.join(_FIXTURES, "87n_fresh_manifest.json")
_PARALLAX = os.path.join(_FIXTURES, "parallax_manifest.json")


def _load(path: str) -> ScrapeManifest:
    with open(path) as f:
        return ScrapeManifest.model_validate(json.load(f))


def _legacy_prepass_clusters(manifest: ScrapeManifest, engine: ScaleEngine):
    """The pre-U2 inline pre-pass, reproduced test-side: gather same-tag
    root-layer positions with the engine's own rule resolver, then
    single-linkage cluster at 0.40 * max(w, h). This is the reference
    the shared helper must match filter-for-filter."""
    layers = manifest.layers
    has_containing = any(
        getattr(l, "containing_comp_id", None) is not None for l in layers)
    if has_containing:
        layer_indices = {(l.containing_comp_id, l.index) for l in layers}
    else:
        layer_indices = {l.index for l in layers}

    group_positions: dict = {}
    for layer in layers:
        tag = canon_tag(getattr(layer, "content_tag", None))
        if not tag or tag in ("GUIDE", "PROTECT"):
            continue
        parent_idx = getattr(layer, "parent_index", -1)
        comp_id = getattr(layer, "containing_comp_id", None)
        if not is_layer_root(parent_idx, layer_indices, comp_id):
            continue
        rule, _ = engine._resolve_gravity_rule(layer)
        if rule is None:
            continue
        pos = layer.position or [0.0, 0.0, 0.0]
        group_positions.setdefault((comp_id, tag), []).append(
            (pos[0], pos[1], layer.index))

    orig_w = manifest.project_info.width
    orig_h = manifest.project_info.height
    threshold = max(orig_w, orig_h) * SPATIAL_CLUSTER_THRESHOLD_FRACTION
    return single_linkage_clusters(group_positions, threshold)


class TestResolutionMatchesLegacyPrepass:
    @pytest.mark.parametrize("fixture", [_87N, _PARALLAX],
                             ids=["87n", "parallax"])
    def test_clusters_match_inline_recompute(self, fixture):
        manifest = _load(fixture)
        engine = ScaleEngine(manifest, 1080, 1920, "Fit", 0.0, layout="tags")
        exp_centroids, exp_sizes, exp_groups = _legacy_prepass_clusters(
            manifest, engine)

        res = compute_placement_resolution(
            manifest, layout="tags",
            rule_resolver=engine._resolve_gravity_rule)
        assert res.layer_centroids == exp_centroids
        assert res.layer_cluster_sizes == exp_sizes
        assert res.group_centroids == exp_groups
        # Real-fixture sanity: the pre-pass actually clusters something.
        assert exp_centroids, "fixture must exercise the pre-pass"

    @pytest.mark.parametrize("fixture,root_cid",
                             [(_87N, 1600004739), (_PARALLAX, 15)],
                             ids=["87n", "parallax"])
    def test_root_cid_and_global_seal_semantics(self, fixture, root_cid):
        manifest = _load(fixture)
        all_cids = {l.containing_comp_id for l in manifest.layers}
        scenes = detect_3d_camera_scenes_by_comp(manifest.layers)

        # tags: nothing preserves, nothing seals.
        res = compute_placement_resolution(manifest, layout="tags")
        assert res.root_cid == root_cid
        assert res.preserving_scene_cids == set()
        assert res.sealed_precomp_cids == set()
        assert res.camera_depth_force_s_cids == set()
        assert res.any_preserving_scene_unit is False

        # scene: legacy GLOBAL semantics — every comp preserves, every
        # nested comp seals.
        res = compute_placement_resolution(manifest, layout="scene")
        assert res.preserving_scene_cids == all_cids
        assert res.sealed_precomp_cids == all_cids - {None, root_cid}
        assert res.any_preserving_scene_unit is True

        # auto pre-Phase-6: global flip iff ANY comp is a scene (both
        # fixtures have at least one).
        assert scenes
        res = compute_placement_resolution(manifest, layout="auto")
        assert res.any_preserving_scene_unit is True

    def test_engine_build_units_is_idempotent(self):
        manifest = _load(_87N)
        engine = ScaleEngine(manifest, 1080, 1920, "Fit", 0.0, layout="auto")
        report1 = engine.build_units()
        res1 = engine.placement_resolution
        report2 = engine.build_units()
        assert report2 is report1
        assert engine.placement_resolution is res1
        assert engine.placement_degraded is None
        assert res1 is not None
        # conform() re-calls build_units(); the resolution must not be
        # rebuilt mid-run.
        engine.conform()
        assert engine.placement_resolution is res1


# ─────────────────────────────────────────────────────────────────────
# Phase 6 — per-comp scene resolution under layout=auto
# ─────────────────────────────────────────────────────────────────────
#
# The synthetic manifests below are allowed ONLY for topology no real
# fixture has yet (one camera-scene precomp + one tags root comp in a
# single conform; a camera-less nested comp under auto). Flagged in
# .pipeline/status.md for Matt to capture real mixed comps later — per
# the anti-synthetic-fixture rule, real-comp behavior is pinned by the
# u2 goldens on 87N + Parallax.

_S = 0.5625           # 1920x1080 → 1080x1920 Fit
_K = 1920.0 / 1080.0  # depth scalar for the same target


def _mixed_manifest() -> ScrapeManifest:
    """Root comp (cid 1) is NOT a scene — tagged 2D layers → tag
    gravity. Nested comp (cid 2) IS a 3D camera scene (depth-animated
    camera + 3D layer) → sealed unit, camera depth forced S."""
    return ScrapeManifest.model_validate({
        "status": "OK",
        "project_info": {"name": "Mixed", "width": 1920, "height": 1080,
                         "fps": 24.0},
        "scrape_meta": {"schema_version": "5.1"},
        "layers": [
            {"index": 1, "name": "Title", "uid": "aaa001",
             "parent_index": -1, "layer_kind": "av",
             "containing_comp_id": 1, "content_tag": "TT",
             "position": [960.0, 540.0, 0.0],
             "scale": [100.0, 100.0, 100.0]},
            {"index": 2, "name": "Legal", "uid": "aaa002",
             "parent_index": -1, "layer_kind": "av",
             "containing_comp_id": 1, "content_tag": "LGL",
             "position": [960.0, 1000.0, 0.0],
             "scale": [100.0, 100.0, 100.0]},
            {"index": 3, "name": "SceneWrapper", "uid": "aaa003",
             "parent_index": -1, "layer_kind": "av",
             "containing_comp_id": 1,
             "position": [960.0, 540.0, 0.0],
             "scale": [100.0, 100.0, 100.0],
             "source_item": {"kind": "comp", "id": 2, "name": "Scene"}},
            {"index": 1, "name": "Camera 1", "uid": "bbb001",
             "parent_index": -1, "layer_kind": "camera",
             "containing_comp_id": 2,
             "position": [960.0, 540.0, -1500.0],
             "camera": {"zoom": 1500.0, "focusDistance": 1000.0,
                        "pointOfInterest": [960.0, 540.0, 0.0]},
             "temporal_data": {"position_z": {
                 "times": [0.0, 1.0], "values": [-1500.0, -1200.0]}}},
            {"index": 2, "name": "Deep Text", "uid": "bbb002",
             "parent_index": -1, "layer_kind": "av", "threeD": True,
             "containing_comp_id": 2,
             "position": [960.0, 540.0, 200.0],
             "scale": [100.0, 100.0, 100.0]},
        ],
    })


def _no_camera_nested_manifest() -> ScrapeManifest:
    """Root comp (cid 1) + one nested precomp (cid 2), NO cameras
    anywhere. Pre-U2 auto behaved exactly like tags here; Phase 6
    seals the precomp under auto regardless (OQ-3, spec'd by task)."""
    return ScrapeManifest.model_validate({
        "status": "OK",
        "project_info": {"name": "Flat2D", "width": 1920, "height": 1080,
                         "fps": 24.0},
        "scrape_meta": {"schema_version": "5.1"},
        "layers": [
            {"index": 1, "name": "A", "uid": "ccc001",
             "parent_index": -1, "layer_kind": "av",
             "containing_comp_id": 1, "content_tag": "TT",
             "position": [960.0, 300.0, 0.0],
             "scale": [100.0, 100.0, 100.0]},
            {"index": 2, "name": "Wrapper", "uid": "ccc002",
             "parent_index": -1, "layer_kind": "av",
             "containing_comp_id": 1,
             "position": [960.0, 540.0, 0.0],
             "scale": [100.0, 100.0, 100.0],
             "source_item": {"kind": "comp", "id": 2, "name": "Inner"}},
            {"index": 1, "name": "B", "uid": "ccc003",
             "parent_index": -1, "layer_kind": "av",
             "containing_comp_id": 2,
             "position": [400.0, 400.0, 0.0],
             "scale": [100.0, 100.0, 100.0]},
        ],
    })


def _layer_out(result: dict, name: str) -> dict:
    hits = [l for l in result["layers"] if l["name"] == name]
    assert hits, f"layer {name!r} not in result"
    return hits[0]


class TestPerCompResolutionMixed:
    """One conform, two unit decisions: the non-scene root keeps tag
    behavior while the nested camera scene seals — the whole point of
    U2's per-comp resolution."""

    def setup_method(self):
        self.manifest = _mixed_manifest()
        self.engine = ScaleEngine(self.manifest, 1080, 1920, "Fit", 0.0,
                                  layout="auto")
        self.result = self.engine.conform()

    def test_resolution_sets(self):
        res = self.engine.placement_resolution
        assert res is not None
        assert res.root_cid == 1
        assert res.scenes == {2}
        assert res.preserving_scene_cids == set(), (
            "root comp is not a scene — its layers keep tag behavior")
        assert res.sealed_precomp_cids == {2}
        assert res.camera_depth_force_s_cids == {2}
        assert res.any_preserving_scene_unit is True
        # Root-comp semantics: the target comp itself does not preserve.
        assert self.engine.scene_preserve_active is False

    def test_root_layers_keep_tag_gravity(self):
        assert "Title" in self.engine.gravity_applied
        assert "Legal" in self.engine.gravity_applied
        # TOP-pinned title must land ABOVE the plain center-remap y.
        center_remap_y = (540.0 - 540.0) * _S + 960.0
        title_y = _layer_out(self.result, "Title")[
            "conformed_transforms"]["position"][1]
        assert title_y < center_remap_y - 100, (
            "TT tag gravity must still pin toward the top zone in the "
            "non-scene root comp under auto")

    def test_nested_scene_layers_sealed(self):
        for name in ("Camera 1", "Deep Text"):
            src = next(l for l in self.manifest.layers if l.name == name)
            out = _layer_out(self.result, name)["conformed_transforms"]
            assert out["position"] == pytest.approx(list(src.position)), name
        deep = _layer_out(self.result, "Deep Text")["conformed_transforms"]
        assert deep["scale"] == pytest.approx([100.0, 100.0, 100.0])

    def test_camera_depth_mode_per_comp(self):
        # Depth-animated camera → global auto-detect is K; root is not a
        # scene so no global K→S force fires — but the scene comp's
        # cameras resolve S via the per-comp accessor.
        assert self.engine.camera_depth_mode == "K"
        assert self.engine.camera_depth_mode_for(2) == "S"
        assert self.engine.camera_depth_mode_for(1) == "K"

    def test_camera_static_and_keyframe_paths_agree(self):
        """Extend the camera-parity pattern (Bug L/J): the static
        intrinsics and the keyframe apply_rule call must scale a scene
        camera's zoom by the SAME per-comp scalar (S, forced)."""
        from core.property_registry import apply_rule, by_temporal_key
        src_cam = next(l for l in self.manifest.layers
                       if l.layer_kind == "camera")
        out_cam = _layer_out(self.result, "Camera 1")
        static_zoom = out_cam["conformed_transforms"]["camera"]["zoom"]
        assert static_zoom == pytest.approx(src_cam.camera.zoom * _S), (
            "scene-comp camera zoom must scale by S (forced), not K")

        # Keyframe path: the orchestrator passes
        # K = k_factor if camera_depth_mode_for(cid) == "K" else s_factor.
        mode = self.engine.camera_depth_mode_for(2)
        k_for_keys = _K if mode == "K" else _S
        pdef = by_temporal_key("camera_zoom")
        key_zoom = apply_rule(
            pdef.lerp_rule, src_cam.camera.zoom,
            uniform_scale=_S, is_root=True,
            src_center=(960.0, 540.0), tgt_center=(540.0, 960.0),
            scale_z=True, K=k_for_keys, layer_kind="camera")
        assert key_zoom == pytest.approx(static_zoom), (
            "static and keyframe camera paths diverged — Bug L/J parity")


class TestAutoSealsPrecompsWithoutCamera:
    """OQ-3 / task scope item 4: under auto, precomps seal even when NO
    camera scene exists anywhere (pre-U2, auto == tags there). Pinned
    here; flagged on Matt's AE QA checklist."""

    def test_auto_seals_nested_statics(self):
        manifest = _no_camera_nested_manifest()
        engine = ScaleEngine(manifest, 1080, 1920, "Fit", 0.0,
                             layout="auto")
        result = engine.conform()
        res = engine.placement_resolution
        assert res.scenes == set()
        assert res.sealed_precomp_cids == {2}
        assert engine.sealed_precomp_cids == {2}
        # No scene anywhere: nothing preserves, SOE gate stays open,
        # root keeps tag behavior.
        assert res.preserving_scene_cids == set()
        assert res.any_preserving_scene_unit is False
        assert engine.any_preserving_scene_unit is False
        assert engine.scene_preserve_active is False
        assert "A" in engine.gravity_applied
        # The nested layer passes through untouched (sealed unit).
        src_b = next(l for l in manifest.layers if l.name == "B")
        out_b = _layer_out(result, "B")["conformed_transforms"]
        assert out_b["position"] == pytest.approx(list(src_b.position))
        assert out_b["scale"] == pytest.approx(list(src_b.scale))

    def test_tags_mode_still_resizes_nested(self):
        """Byte-identity guard: legacy tags mode keeps the recursive
        resize for nested layers — sealing is auto/scene only."""
        manifest = _no_camera_nested_manifest()
        engine = ScaleEngine(manifest, 1080, 1920, "Fit", 0.0,
                             layout="tags")
        result = engine.conform()
        assert engine.sealed_precomp_cids == set()
        src_b = next(l for l in manifest.layers if l.name == "B")
        out_b = _layer_out(result, "B")["conformed_transforms"]
        assert abs(out_b["position"][0] - src_b.position[0]) > 1, (
            "tags mode must keep remapping nested layers")


class TestAnchorResolved:
    """U2 Phase 7 — the Design read card shows what each unit ACTUALLY
    got, stamped from the same resolution object the math read."""

    def test_stamped_after_conform_auto(self):
        manifest = _load(_87N)
        engine = ScaleEngine(manifest, 1080, 1920, "Fit", 0.0,
                             layout="auto")
        engine.conform()
        report = engine.placement_units_report
        assert report is not None
        by_kind = {}
        for u in report.units:
            by_kind.setdefault(u.kind, []).append(u)
        assert all(u.anchor_resolved for u in report.units), (
            "every unit must carry anchor_resolved after conform")
        # 87N auto: root IS the scene → preserved; precomps sealed.
        assert "preserved" in by_kind["camera_scene"][0].anchor_resolved
        for u in by_kind["precomp"]:
            assert "sealed" in u.anchor_resolved

    def test_stamped_after_conform_tags(self):
        manifest = _load(_87N)
        engine = ScaleEngine(manifest, 1080, 1920, "Fit", 0.0,
                             layout="tags")
        engine.conform()
        report = engine.placement_units_report
        by_kind = {}
        for u in report.units:
            by_kind.setdefault(u.kind, []).append(u)
        assert "tag gravity" in by_kind["camera_scene"][0].anchor_resolved
        for u in by_kind["precomp"]:
            assert "recursive resize" in u.anchor_resolved

    def test_report_card_renders_read_vs_did(self, tmp_path):
        from logic.report_generator import generate_report
        manifest = _load(_87N)
        engine = ScaleEngine(manifest, 1080, 1920, "Fit", 0.0,
                             layout="auto")
        engine.conform()
        path = generate_report(
            conformed_manifest={"layers": [], "warnings": {}},
            scrape_manifest={"project_info": {"name": "T"}},
            preset_label="TikTok", target_w=1080, target_h=1920,
            scale_mode="Fit", uniform_scale=1.0, session_id="s",
            output_path=str(tmp_path / "r.html"),
            placement_units=engine.placement_units_report.model_dump(),
        )
        html = open(path, encoding="utf-8").read()
        assert "How Dimension Read This Comp" in html
        assert "did:" in html, "card must show the resolved anchor"
