# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_scene_preserve_layout.py
Scene-preserve layout mode (2026-07-02 ground-truth session).

The 87N TikTok conform split a single visual lockup into zones: the
manually-TOP-tagged outline layers were pinned to the top safe area
while the CENTER-tagged logo precomp sat near center — but the artist's
hand-built reference keeps the WHOLE lockup together, uniformly scaled
and centered. Scene-preserve mode reproduces the hand-built result: tag
gravity is bypassed for everything except FILL (backgrounds must still
cover the frame), so content layers take the standard uniform
center-remap.

Two real fixtures, per the "never trust synthetic fixtures for layout
behavior" anti-pattern:
  - bug_l/87n_source_manifest.json — the canonical Bug L capture
    (legacy TT/HERO/BOXART vocab, no FILL at root).
  - session_2026_07_02/87n_fresh_manifest.json — the live scrape from
    the 2026-07-02 AE ground-truth session (heuristic FILL on
    EFX/87N.mov, manual TOP outlines) — the exact comp that exposed
    the lockup-splitting problem.

Expectations are derived from each layer's own source values, not
hardcoded coordinates, so fixture drift can't silently invalidate them.
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
from core.output_naming import build_mirror_tree_spec  # noqa: E402
from models.project_structure import (  # noqa: E402
    CompNode, ProjectStructure, ScanMeta,
)
from models.target import Target  # noqa: E402

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
_BUG_L = os.path.join(_FIXTURES, "bug_l", "87n_source_manifest.json")
_FRESH = os.path.join(_FIXTURES, "session_2026_07_02",
                      "87n_fresh_manifest.json")

# 1920×1080 → 1080×1920 Fit: S = 1080/1920, fill_S = 1920/1080.
_S = 0.5625
_FILL_S = 1920.0 / 1080.0
_SRC_C = (960.0, 540.0)
_TGT_C = (540.0, 960.0)


def _load(path: str) -> ScrapeManifest:
    with open(path) as f:
        return ScrapeManifest.model_validate(json.load(f))


def _conform(path: str, layout: str):
    engine = ScaleEngine(_load(path), 1080, 1920, "Fit", 0.0, layout=layout)
    return engine, engine.conform()


def _layer(result: dict, name: str) -> dict:
    hits = [l for l in result["layers"] if l["name"] == name]
    assert hits, f"layer {name!r} not in result"
    return hits[0]


def _src_layer(manifest: ScrapeManifest, name: str):
    hits = [l for l in manifest.layers if l.name == name]
    assert hits, f"layer {name!r} not in fixture"
    return hits[0]


def _expected_remap(src_pos):
    """The standard uniform center-remap scene mode must produce."""
    return [
        (src_pos[0] - _SRC_C[0]) * _S + _TGT_C[0],
        (src_pos[1] - _SRC_C[1]) * _S + _TGT_C[1],
        (src_pos[2] * _S) if len(src_pos) > 2 else 0.0,
    ]


class TestModeResolution:
    def test_scene_mode_activates(self):
        engine, _ = _conform(_BUG_L, "scene")
        assert engine.scene_preserve_active is True

    def test_auto_activates_on_3d_camera_scene(self):
        """87N has a camera + 3D layers — auto must resolve to scene."""
        engine, _ = _conform(_BUG_L, "auto")
        assert engine.scene_preserve_active is True

    def test_default_tags_mode_does_not_activate(self):
        engine, _ = _conform(_BUG_L, "tags")
        assert engine.scene_preserve_active is False

    def test_tags_mode_output_unchanged_by_layout_param(self):
        """layout='tags' must be byte-identical to the pre-layout-mode
        default — the param's existence cannot perturb legacy output."""
        _, explicit = _conform(_BUG_L, "tags")
        default = ScaleEngine(_load(_BUG_L), 1080, 1920, "Fit", 0.0).conform()
        assert json.dumps(explicit, sort_keys=True, default=str) == \
            json.dumps(default, sort_keys=True, default=str)


class TestScenePreserveBugLFixture:
    def test_outlines_keep_composed_geometry(self):
        """TOP-tagged outlines take the uniform center-remap of their own
        source positions — no top-zone pinning, no z→y spread."""
        manifest = _load(_BUG_L)
        _, result = _conform(_BUG_L, "scene")
        for name in ("EVERYTHING  Outlines", "YOU WANT Outlines",
                     "IS ON THE Outlines", "OTHER SIDE Outlines",
                     "OF FEAR Outlines"):
            src = _src_layer(manifest, name).position
            dst = _layer(result, name)["conformed_transforms"]["position"]
            exp = _expected_remap(src)
            assert dst[0] == pytest.approx(exp[0], abs=0.1), name
            assert dst[1] == pytest.approx(exp[1], abs=0.1), name
            assert dst[2] == pytest.approx(exp[2], abs=0.1), name

    def test_logo_precomp_uniform_scale(self):
        """The logo precomp keeps its composed src scale × S instead of a
        CENTER-gravity rule multiplier."""
        manifest = _load(_BUG_L)
        _, result = _conform(_BUG_L, "scene")
        name = "87N_Reels_DEV_87Neon_HD_10_mc"
        src = _src_layer(manifest, name)
        lay = _layer(result, name)["conformed_transforms"]
        assert lay["position"][0] == pytest.approx(_TGT_C[0], abs=0.1)
        assert lay["position"][1] == pytest.approx(_TGT_C[1], abs=0.1)
        assert lay["scale"][0] == pytest.approx(src.scale[0] * _S, abs=0.1)

    def test_scene_mode_differs_from_tags_mode_for_pinned_layers(self):
        """Sanity: the mode actually changes the outlines' placement."""
        _, tags_result = _conform(_BUG_L, "tags")
        _, scene_result = _conform(_BUG_L, "scene")
        t = _layer(tags_result, "EVERYTHING  Outlines")["conformed_transforms"]["position"]
        s = _layer(scene_result, "EVERYTHING  Outlines")["conformed_transforms"]["position"]
        assert abs(t[1] - s[1]) > 100, (
            "TOP pin vs center-remap must land >100px apart on a tall target")


class TestMirrorTreeKeepSourceDims:
    """Scene-preserve's inject half: nested mirror comps must be created
    at SOURCE dimensions (sealed units), root always at target."""

    def _spec(self, preserve: bool):
        structure = ProjectStructure(
            status="OK", schema_version="1.0",
            scan_meta=ScanMeta(scanned_at="2026-07-02T12:00:00Z"),
            comps=[
                CompNode(id=1, name="Root", width=1920, height=1080,
                         fps=24.0, duration=10.0, pixel_aspect=1.0,
                         bg_color=[0, 0, 0], layer_count=2,
                         is_render_target=True, folder_path=""),
                CompNode(id=2, name="Nested", width=2048, height=2048,
                         fps=24.0, duration=10.0, pixel_aspect=1.0,
                         bg_color=[0, 0, 0], layer_count=1,
                         is_render_target=False, folder_path=""),
            ],
            references=[],
        )
        manifest = ScrapeManifest.model_validate({
            "status": "OK",
            "project_info": {"name": "Root", "width": 1920,
                             "height": 1080, "fps": 24.0},
            "scrape_meta": {"schema_version": "5.1"},
            "layers": [
                {"index": 1, "name": "A", "uid": "aaa111",
                 "parent_index": -1, "layer_kind": "av",
                 "containing_comp_id": 1},
                {"index": 1, "name": "B", "uid": "bbb222",
                 "parent_index": -1, "layer_kind": "av",
                 "containing_comp_id": 2},
            ],
        })
        target = Target(
            id="test:tiktok", label="TikTok", category="social",
            subcategory="tiktok", width=1080, height=1920,
            aspect_ratio=1080 / 1920, aspect_label="9:16",
            source="builtin",
        )
        return build_mirror_tree_spec(
            manifest, structure, target, preserve_nested_dims=preserve)

    def test_nested_entries_flagged_when_preserving(self):
        entries = {e.source_comp_name: e for e in self._spec(True)}
        assert entries["Root"].is_root is True
        assert entries["Root"].keep_source_dims is False, (
            "root must ALWAYS resize to the conform target")
        assert entries["Nested"].keep_source_dims is True

    def test_default_spec_unflagged(self):
        """Legacy behavior untouched: without the scene-preserve flag,
        every mirror still resizes to target."""
        assert all(not e.keep_source_dims for e in self._spec(False))


class TestMirrorTreeSealedCidsKwarg:
    """U3 Phase B — the precise `sealed_cids` kwarg on
    build_mirror_tree_spec. Three nested precomps so a PARTIAL seal
    (only one of two nested comps sealed) can be expressed — something
    the old `preserve_nested_dims: bool` coercion could never do
    (it was all-nested-or-none)."""

    def _spec(self, *, sealed_cids=None, preserve_nested_dims=False):
        structure = ProjectStructure(
            status="OK", schema_version="1.0",
            scan_meta=ScanMeta(scanned_at="2026-07-02T12:00:00Z"),
            comps=[
                CompNode(id=1, name="Root", width=1920, height=1080,
                         fps=24.0, duration=10.0, pixel_aspect=1.0,
                         bg_color=[0, 0, 0], layer_count=3,
                         is_render_target=True, folder_path=""),
                CompNode(id=2, name="Sealed", width=2048, height=2048,
                         fps=24.0, duration=10.0, pixel_aspect=1.0,
                         bg_color=[0, 0, 0], layer_count=1,
                         is_render_target=False, folder_path=""),
                CompNode(id=3, name="Unsealed", width=1024, height=1024,
                         fps=24.0, duration=10.0, pixel_aspect=1.0,
                         bg_color=[0, 0, 0], layer_count=1,
                         is_render_target=False, folder_path=""),
            ],
            references=[],
        )
        manifest = ScrapeManifest.model_validate({
            "status": "OK",
            "project_info": {"name": "Root", "width": 1920,
                             "height": 1080, "fps": 24.0},
            "scrape_meta": {"schema_version": "5.1"},
            "layers": [
                {"index": 1, "name": "A", "uid": "aaa111",
                 "parent_index": -1, "layer_kind": "av",
                 "containing_comp_id": 1},
                {"index": 1, "name": "B", "uid": "bbb222",
                 "parent_index": -1, "layer_kind": "av",
                 "containing_comp_id": 2},
                {"index": 1, "name": "C", "uid": "ccc333",
                 "parent_index": -1, "layer_kind": "av",
                 "containing_comp_id": 3},
            ],
        })
        target = Target(
            id="test:tiktok", label="TikTok", category="social",
            subcategory="tiktok", width=1080, height=1920,
            aspect_ratio=1080 / 1920, aspect_label="9:16",
            source="builtin",
        )
        return build_mirror_tree_spec(
            manifest, structure, target,
            preserve_nested_dims=preserve_nested_dims,
            sealed_cids=sealed_cids)

    def test_partial_seal_precision(self):
        """Only comp 2 is sealed — comp 3 must resize to target despite
        BOTH being nested. Impossible to express with the legacy bool
        (which sealed every nested comp or none)."""
        entries = {e.source_comp_name: e for e in self._spec(sealed_cids={2})}
        assert entries["Root"].keep_source_dims is False
        assert entries["Sealed"].keep_source_dims is True
        assert entries["Unsealed"].keep_source_dims is False

    def test_sealed_cids_none_falls_back_to_legacy_bool(self):
        """sealed_cids omitted (None) — parity with the pre-Phase-B
        coarse bool coercion (all nested comps sealed together)."""
        entries = {e.source_comp_name: e
                  for e in self._spec(sealed_cids=None,
                                       preserve_nested_dims=True)}
        assert entries["Root"].keep_source_dims is False
        assert entries["Sealed"].keep_source_dims is True
        assert entries["Unsealed"].keep_source_dims is True

    def test_empty_sealed_cids_overrides_legacy_bool(self):
        """sealed_cids given (even empty) takes precedence over the
        legacy bool — an explicit "nothing sealed" answer, not a
        silent no-op."""
        entries = {e.source_comp_name: e
                  for e in self._spec(sealed_cids=set(),
                                       preserve_nested_dims=True)}
        assert all(not e.keep_source_dims for e in entries.values())


class TestSealedUnitsFullPipeline:
    """Blast-radius audit (2026-07-02): the static path sealing nested
    units is not enough — the keyframe and effect passes in
    orchestrator.main() must seal them too, or an ANIMATED nested layer
    gets root-frame-remapped keys injected into an untouched comp.
    Runs the real pipeline end-to-end via subprocess (the guard lives in
    main(), not in a unit-callable)."""

    def test_nested_keys_sealed_but_root_keys_conform(self, tmp_path):
        import subprocess

        with open(_FRESH) as f:
            data = json.load(f)
        root_cid = data["layers"][0]["containing_comp_id"]
        donor = next(l for l in data["layers"]
                     if l["name"] == "YOU WANT Outlines")
        nested = next(l for l in data["layers"]
                      if l.get("containing_comp_id") not in (None, root_cid)
                      and l["name"] == "Cyan Solid 2")
        nested["temporal_data"] = json.loads(
            json.dumps(donor["temporal_data"]))

        src_path = tmp_path / "manifest.json"
        src_path.write_text(json.dumps(data))

        repo_py = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        proc = subprocess.run(
            [sys.executable, os.path.join(repo_py, "orchestrator.py"),
             "--source", str(src_path),
             "--width", "1080", "--height", "1920",
             "--layout", "scene", "--no-report",
             "--output", str(tmp_path / "chunks")],
            cwd=str(tmp_path), capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stderr[-2000:]

        chunk = json.loads((tmp_path / "chunk_manifest.json").read_text())
        layers = []
        for cp in chunk["chunk_paths"]:
            cdata = json.loads(open(cp, encoding="utf-8").read())
            layers.extend(cdata.get("layers", cdata) if isinstance(cdata, dict)
                          else cdata)

        def _find(name):
            return next(l for l in layers if l["name"] == name)

        # Nested keyed layer: sealed — no conformed keys emitted.
        assert not _find("Cyan Solid 2").get("conformed_keys"), (
            "nested layer keys must NOT be conformed in scene mode")
        # Root keyed layer: still conformed (scene remap applies to keys).
        assert _find("YOU WANT Outlines").get("conformed_keys"), (
            "root layer keys must still conform in scene mode")
        # Nested layer with effects: sealed — no conformed effect params.
        assert not _find("Noise").get("conformed_effects"), (
            "nested effects must NOT be conformed in scene mode")
        # 2026-07-02 AE ground truth (87N EFX / CC Lens): effect params
        # live in LAYER space and AE applies effects pre-transform, so a
        # transform-based conform must never scale them — for ANY layer,
        # root included. Double-transform warps the effect.
        assert not any(l.get("conformed_effects") for l in layers), (
            "effect params must not be conformed on the transform-scaled path")
        assert not any(l.get("conformed_layer_styles") for l in layers), (
            "layer-style params must not be conformed on the "
            "transform-scaled path")


class TestScenePreserveFreshSessionFixture:
    """The live 2026-07-02 scrape — heuristic FILL + manual TOP outlines,
    the exact comp whose conform split the lockup."""

    def test_fill_layers_still_cover_frame(self):
        """FILL is the one tag scene mode keeps — backgrounds must cover
        the 1080×1920 frame (fill scale ~177.78), not letterbox at 56.25."""
        manifest = _load(_FRESH)
        _, result = _conform(_FRESH, "scene")
        checked = 0
        root_comp_id = manifest.layers[0].containing_comp_id
        for src in manifest.layers:
            if src.containing_comp_id != root_comp_id:
                continue
            if (src.content_tag or "") != "FILL":
                continue
            lay = _layer(result, src.name)["conformed_transforms"]
            assert lay["scale"][0] == pytest.approx(
                src.scale[0] * _FILL_S, abs=0.1), (
                f"{src.name} must keep FILL cover behavior in scene mode")
            checked += 1
        assert checked >= 2, "fixture must exercise at least 2 FILL layers"

    def test_camera_scales_uniformly_with_scene(self):
        """The 'still doesn't look right' follow-up from the ground-truth
        session: layers' z scaled by S while the depth-animated camera
        scaled by K (×1.78) — a 216% divergence that dollied the lens
        against the scene. Scene-preserve must force the camera to S so
        the whole scene shares one reference frame."""
        manifest = _load(_FRESH)
        engine, result = _conform(_FRESH, "scene")
        assert engine.camera_depth_mode == "S"
        cam_src = _src_layer(manifest, "Camera 1")
        cam_dst = _layer(result, "Camera 1")["conformed_transforms"]["position"]
        assert cam_dst[2] == pytest.approx(cam_src.position[2] * _S, abs=0.5), (
            "camera z must scale by S in scene mode, not K")

    def test_tags_mode_keeps_auto_detected_camera_mode(self):
        """The K→S forcing is scene-only — tag layout keeps the
        depth-animation auto-detection (K for this fixture)."""
        engine, _ = _conform(_FRESH, "tags")
        assert engine.camera_depth_mode == "K"

    def test_nested_precomp_layers_pass_through(self):
        """Nesting dolls (2026-07-02 session): precomps are sealed units.
        The root wrapper layer carries the whole transform; layers INSIDE
        nested comps — at any depth — must keep their source values
        (Babysitter mirrors those comps at source dimensions)."""
        manifest = _load(_FRESH)
        _, result = _conform(_FRESH, "scene")
        root_cid = manifest.layers[0].containing_comp_id
        checked = 0
        for src in manifest.layers:
            cid = src.containing_comp_id
            if cid is None or cid == root_cid:
                continue
            lay = _layer(result, src.name)["conformed_transforms"]
            src_pos = src.position or [0, 0, 0]
            src_scl = src.scale or [100, 100, 100]
            assert lay["position"][0] == pytest.approx(src_pos[0], abs=0.01), src.name
            assert lay["position"][1] == pytest.approx(src_pos[1], abs=0.01), src.name
            assert lay["scale"][0] == pytest.approx(src_scl[0], abs=0.01), src.name
            checked += 1
        assert checked >= 5, "fixture must exercise nested layers"

    def test_nested_layers_still_conform_in_tags_mode(self):
        """Legacy recursive conform unchanged: tags mode still remaps
        nested layers (mirrors are target-dimensioned there)."""
        manifest = _load(_FRESH)
        _, result = _conform(_FRESH, "tags")
        src = _src_layer(manifest, "Cyan Solid 2")
        lay = _layer(result, "Cyan Solid 2")["conformed_transforms"]
        assert abs(lay["position"][0] - src.position[0]) > 1, (
            "tags mode must keep remapping nested layers")

    def test_lockup_stays_together(self):
        """The core ground-truth complaint: outlines and logo must land in
        the same mid-frame band (like the artist's hand-built reference),
        not split into a top zone and a center zone."""
        _, result = _conform(_FRESH, "scene")
        ys = []
        for l in result["layers"]:
            if l["name"].endswith("Outlines") or \
                    l["name"] == "87N_Reels_DEV_87Neon_HD_10_mc":
                ys.append(l["conformed_transforms"]["position"][1])
        assert len(ys) >= 6
        assert max(ys) - min(ys) < 120, (
            f"lockup fragmented: y range {min(ys):.0f}–{max(ys):.0f}")
        for y in ys:
            assert 700 < y < 1200, f"lockup member outside mid-frame: y={y:.0f}"
