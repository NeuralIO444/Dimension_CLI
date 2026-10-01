# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_stage4_reconstruction.py
Stage 4 — tests for `core/reconstruction.py` (plan_reconstruction,
emit_reconstruction_plan_as_sidecar) and the stage's zero-regression
contract.

Per CLAUDE.md's "checklist for tagging/gravity/surveyor changes" rule 5,
uses both synthetic minimal manifests (fast, precise) AND the 87N real-comp
fixture to avoid the "synthetic fixtures mask production bugs" trap.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import types
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


from core.reconstruction import (
    ReconstructionPlan,
    TargetSpec,
    emit_reconstruction_plan_as_sidecar,
    plan_reconstruction,
)
from models.scrape_manifest import ScrapeManifest


_87N_FIXTURE = (
    Path(__file__).parent / "fixtures" / "bug_l" / "87n_source_manifest.json"
)


def _load_87n_manifest() -> ScrapeManifest:
    with open(_87N_FIXTURE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return ScrapeManifest.model_validate(data)


def _layer_ns(tag="CENTER", idx=1, comp_id=1, x=960, y=540, w=400, h=100):
    """SimpleNamespace layer — matches the duck-typed access in plan_reconstruction."""
    return types.SimpleNamespace(
        index=idx,
        name=f"Layer {idx}",
        layer_kind="text",
        containing_comp_id=comp_id,
        content_tag=tag,
        content_tag_source="heuristic",
        world_bounds={"l": x - w // 2, "t": y - h // 2,
                      "r": x + w // 2, "b": y + h // 2, "w": w, "h": h},
        position=[x, y],
        scale=[100.0, 100.0],
        is_guide=False,
        is_3d=False,
    )


def _make_manifest_ns(layers, width=1920, height=1080, duration=30.0):
    """SimpleNamespace manifest — avoids full Pydantic validation for planner tests.
    plan_reconstruction uses getattr duck-typing throughout; this is sufficient."""
    project_info = types.SimpleNamespace(
        width=width, height=height, duration=duration, name="Test"
    )
    return types.SimpleNamespace(
        layers=layers,
        project_info=project_info,
        active_style_profile_id=None,
        style_proposals=None,
    )


# ── Synthetic planner tests ──────────────────────────────────────────────────


class TestPlanReconstructionSynthetic:
    def test_basic_plan_creation(self):
        t = TargetSpec(1080, 1920, 60.0, 24.0, "Vertical Social")
        plan = plan_reconstruction(None, target_spec=t)
        assert isinstance(plan, ReconstructionPlan)
        assert plan.target.width == 1080
        assert plan.target.height == 1920
        assert plan.target.duration == 60.0

    def test_new_comps_always_populated(self):
        """Even a None manifest must yield at least one new_comp entry."""
        t = TargetSpec(1080, 1920)
        plan = plan_reconstruction(None, target_spec=t)
        assert len(plan.new_comps) >= 1
        comp = plan.new_comps[0]
        assert comp["width"] == 1080
        assert comp["height"] == 1920

    def test_new_comp_name_includes_target_spec(self):
        t = TargetSpec(1080, 1920, name="TikTok")
        plan = plan_reconstruction(None, target_spec=t)
        name = plan.new_comps[0]["name"]
        assert "TikTok" in name or "1080" in name



    def test_timing_scale_note_when_duration_differs(self):
        manifest = _make_manifest_ns([_layer_ns()], width=1920, height=1080, duration=30.0)
        t = TargetSpec(1080, 1920, duration=15.0)
        plan = plan_reconstruction(manifest, target_spec=t)
        # editable_notes must mention the duration scale
        assert "Duration scaled" in plan.editable_notes or any(
            "timing" in d.notes for d in plan.decisions
        )

    def test_no_timing_note_when_no_duration(self):
        manifest = _make_manifest_ns([_layer_ns()], width=1920, height=1080)
        t = TargetSpec(1080, 1920, duration=None)
        plan = plan_reconstruction(manifest, target_spec=t)
        assert "Duration scaled" not in plan.editable_notes

    def test_vertical_target_bias_shifts_center_to_top(self):
        """Tall targets (H > 1.5×W) should shift center-anchored groups to top."""
        manifest = _make_manifest_ns([_layer_ns(tag="CENTER")])
        t = TargetSpec(1080, 1920)  # H/W = 1.78 — triggers vertical bias
        plan = plan_reconstruction(manifest, target_spec=t)
        if plan.decisions:
            notes_text = " ".join(d.notes for d in plan.decisions)
            anchors = [d.suggested_anchor for d in plan.decisions]
            assert "vertical target bias" in notes_text or any(
                a == "top" for a in anchors
            )

    def test_fallback_target_spec_when_none_provided(self):
        """When target_spec is omitted, planner must not crash."""
        manifest = _make_manifest_ns([_layer_ns()])
        plan = plan_reconstruction(manifest)
        assert isinstance(plan, ReconstructionPlan)

    def test_none_manifest_none_target_does_not_crash(self):
        plan = plan_reconstruction(None)
        assert isinstance(plan, ReconstructionPlan)

    def test_decisions_have_group_key_strings(self):
        manifest = _make_manifest_ns(
            [_layer_ns(tag="TOP", idx=1), _layer_ns(tag="CENTER", idx=2)]
        )
        t = TargetSpec(1080, 1920)
        plan = plan_reconstruction(manifest, target_spec=t)
        for d in plan.decisions:
            assert isinstance(d.group_key, str)
            assert len(d.group_key) > 0


class TestFramerateEngineWiring:
    """#416 -- FramerateEngine.plan_conformance() wired into the duration
    branch when target_spec.fps is set. Only engages on that condition;
    the primary 265-target catalog path (neither fps nor duration set)
    must stay byte-identical -- see test_primary_path_unaffected_by_fps_wiring."""

    def test_frame_snapped_note_appears_when_fps_set(self):
        manifest = _make_manifest_ns([_layer_ns()], width=1920, height=1080, duration=30.0)
        t = TargetSpec(1080, 1920, duration=15.0, fps=29.97)
        plan = plan_reconstruction(manifest, target_spec=t)
        assert "frame-snapped" in plan.editable_notes
        assert "29.97" in plan.editable_notes or "29.970" in plan.editable_notes

    def test_drop_frame_flagged_for_2997(self):
        manifest = _make_manifest_ns([_layer_ns()], width=1920, height=1080, duration=30.0)
        t = TargetSpec(1080, 1920, duration=15.0, fps=29.97)
        plan = plan_reconstruction(manifest, target_spec=t)
        assert "drop-frame" in plan.editable_notes

    def test_non_drop_frame_not_flagged_for_24fps(self):
        manifest = _make_manifest_ns([_layer_ns()], width=1920, height=1080, duration=30.0)
        t = TargetSpec(1080, 1920, duration=15.0, fps=24.0)
        plan = plan_reconstruction(manifest, target_spec=t)
        assert "drop-frame" not in plan.editable_notes

    def test_no_target_fps_falls_back_to_legacy_naive_ratio(self):
        """target_spec.fps is None -- the pre-#416 naive-ratio note must
        appear verbatim, with no frame-snapped/drop-frame language."""
        manifest = _make_manifest_ns([_layer_ns()], width=1920, height=1080, duration=30.0)
        t = TargetSpec(1080, 1920, duration=15.0)
        plan = plan_reconstruction(manifest, target_spec=t)
        assert "Duration scaled 0.50x" in plan.editable_notes
        assert "frame-snapped" not in plan.editable_notes

    def test_layer_stretch_never_applied_invariant_carries_through(self):
        """ADR-02's invariant: the snapped duration comes from the SOURCE's
        own real-time length re-quantized to the target fps raster, not
        from target_spec.duration -- so scale stays ~1.0 (only frame-
        rounding drift), never the arbitrary ratio a literal stretch to
        target_spec.duration would imply."""
        manifest = _make_manifest_ns([_layer_ns()], width=1920, height=1080, duration=30.0)
        t = TargetSpec(1080, 1920, duration=15.0, fps=30.0)
        plan = plan_reconstruction(manifest, target_spec=t)
        import re
        m = re.search(r"Duration scaled ([\d.]+)x", plan.editable_notes)
        assert m is not None
        scale = float(m.group(1))
        assert abs(scale - 1.0) < 0.05, (
            f"scale={scale} -- frame-snapping must preserve source real-time "
            "duration, not stretch toward target_spec.duration=15.0 (which "
            "would show ~0.50x)"
        )

    def test_primary_path_unaffected_by_fps_wiring(self):
        """The primary 265-target catalog path sets neither duration nor
        fps on TargetSpec -- editable_notes must be byte-identical to the
        pre-#416 baseline (no Duration/frame-snapped text at all)."""
        manifest = _make_manifest_ns([_layer_ns()], width=1920, height=1080, duration=30.0)
        t = TargetSpec(1080, 1920, name="TikTok")
        plan = plan_reconstruction(manifest, target_spec=t)
        assert "Duration scaled" not in plan.editable_notes
        assert "frame-snapped" not in plan.editable_notes
        assert plan.editable_notes == "Stage 4 — reconstruction produces fresh comp(s) for editability."


# ── 87N real-manifest tests ──────────────────────────────────────────────────


class TestPlanReconstructionReal:
    def test_real_manifest_produces_decisions(self):
        manifest = _load_87n_manifest()
        t = TargetSpec(1080, 1920, name="TikTok")
        plan = plan_reconstruction(manifest, target_spec=t)
        assert len(plan.decisions) > 0

    def test_real_manifest_new_comps_populated(self):
        manifest = _load_87n_manifest()
        t = TargetSpec(1080, 1920)
        plan = plan_reconstruction(manifest, target_spec=t)
        assert len(plan.new_comps) >= 1
        assert plan.new_comps[0]["width"] == 1080
        assert plan.new_comps[0]["height"] == 1920

    def test_real_manifest_structural_layers_excluded(self):
        """Camera and light layers must not appear in any decision's group_key."""
        manifest = _load_87n_manifest()
        t = TargetSpec(1080, 1920)
        plan = plan_reconstruction(manifest, target_spec=t)
        structural_tags = {"camera", "light", "null", "CAMERA", "LIGHT", "NULL"}
        for d in plan.decisions:
            # group_key format is "{comp_id}:{tag}"
            tag_part = d.group_key.split(":", 1)[-1].lower()
            assert tag_part not in {"camera", "light"}, (
                f"Structural tag leaked into decisions: {d.group_key}"
            )

    def test_does_not_mutate_manifest(self):
        manifest = _load_87n_manifest()
        before = json.dumps(manifest.model_dump(), sort_keys=True)
        t = TargetSpec(1080, 1920)
        plan_reconstruction(manifest, target_spec=t)
        after = json.dumps(manifest.model_dump(), sort_keys=True)
        assert before == after, "plan_reconstruction mutated the input manifest"

    def test_plan_is_serialisable(self):
        """The plan must survive JSON serialisation (required for sidecar write)."""
        manifest = _load_87n_manifest()
        t = TargetSpec(1080, 1920)
        plan = plan_reconstruction(manifest, target_spec=t)
        data = {
            "target": plan.target.__dict__,
            "decisions": [d.__dict__ for d in plan.decisions],
            "new_comps": plan.new_comps,
            "notes": plan.editable_notes,
        }
        serialised = json.dumps(data, default=str)
        assert len(serialised) > 0


# ── Sidecar emission tests ───────────────────────────────────────────────────


class TestSidecarEmission:
    def test_sidecar_created_next_to_manifest(self, tmp_path):
        manifest_path = str(tmp_path / "scrape_manifest.json")
        (tmp_path / "scrape_manifest.json").write_text("{}")
        t = TargetSpec(1080, 1920, name="IG_Story")
        plan = plan_reconstruction(None, target_spec=t)
        out = emit_reconstruction_plan_as_sidecar(manifest_path, plan)
        expected = str(tmp_path / "scrape_manifest_recon_plan.json")
        assert out == expected
        assert Path(out).exists()

    def test_sidecar_is_valid_json(self, tmp_path):
        manifest_path = str(tmp_path / "scrape_manifest.json")
        t = TargetSpec(1080, 1920)
        plan = plan_reconstruction(None, target_spec=t)
        out = emit_reconstruction_plan_as_sidecar(manifest_path, plan)
        with open(out) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_sidecar_contains_required_keys(self, tmp_path):
        manifest_path = str(tmp_path / "scrape_manifest.json")
        t = TargetSpec(1080, 1920)
        plan = plan_reconstruction(None, target_spec=t)
        out = emit_reconstruction_plan_as_sidecar(manifest_path, plan)
        with open(out) as f:
            data = json.load(f)
        for key in ("target", "decisions", "new_comps", "notes"):
            assert key in data, f"Missing key in sidecar: {key}"

    def test_sidecar_target_width_height_correct(self, tmp_path):
        manifest_path = str(tmp_path / "scrape_manifest.json")
        t = TargetSpec(3840, 2160, name="4K")
        plan = plan_reconstruction(None, target_spec=t)
        out = emit_reconstruction_plan_as_sidecar(manifest_path, plan)
        with open(out) as f:
            data = json.load(f)
        assert data["target"]["width"] == 3840
        assert data["target"]["height"] == 2160

    def test_sidecar_overwrite_is_idempotent(self, tmp_path):
        manifest_path = str(tmp_path / "scrape_manifest.json")
        t = TargetSpec(1080, 1920)
        plan = plan_reconstruction(None, target_spec=t)
        emit_reconstruction_plan_as_sidecar(manifest_path, plan)
        out2 = emit_reconstruction_plan_as_sidecar(manifest_path, plan)
        assert Path(out2).exists()

    def test_sidecar_with_real_manifest(self, tmp_path):
        manifest = _load_87n_manifest()
        manifest_path = str(tmp_path / "scrape_manifest.json")
        t = TargetSpec(1080, 1920)
        plan = plan_reconstruction(manifest, target_spec=t)
        out = emit_reconstruction_plan_as_sidecar(manifest_path, plan)
        with open(out) as f:
            data = json.load(f)
        assert len(data["decisions"]) > 0
        assert len(data["new_comps"]) >= 1


# ── Zero-regression proof ────────────────────────────────────────────────────


class TestZeroRegressionProof:
    """Stage 4 planner must be inert relative to the conform pipeline.

    The reconstruction plan is produced AFTER the conform pass and is
    written to a sidecar — it must never influence ScaleEngine's output.
    """

    def test_conform_output_unchanged_by_reconstruction_plan(self):
        from core.scale_engine import ScaleEngine

        manifest_plain = _load_87n_manifest()
        manifest_with_recon = _load_87n_manifest()

        # Attach reconstruction artefacts to the second manifest (simulating
        # what the orchestrator does when is_arbitrary is True).
        t = TargetSpec(1080, 1920, duration=15.0, fps=30.0)
        plan = plan_reconstruction(manifest_with_recon, target_spec=t)
        # Stash plan on manifest as the orchestrator does
        manifest_with_recon.target_spec = t.__dict__  # type: ignore[attr-defined]

        tw, th = 1080, 1920
        result_plain = ScaleEngine(manifest_plain, tw, th, "Fit", 0.05).conform()
        result_recon = ScaleEngine(manifest_with_recon, tw, th, "Fit", 0.05).conform()

        plain_json = json.dumps(result_plain, sort_keys=True)
        recon_json = json.dumps(result_recon, sort_keys=True)

        assert plain_json == recon_json, (
            "ScaleEngine output changed when reconstruction plan was attached — "
            "Stage 4 data must not reach the conform pipeline."
        )

    def test_reconstruction_module_does_not_import_scale_engine_or_gravity(self):
        """reconstruction.py must not import from scale_engine* or gravity.

        If it did, changes to the conform pipeline would silently affect the
        planner, and the planner's 'pure function' guarantee would be broken.
        """
        recon_path = (
            Path(__file__).parent.parent / "core" / "reconstruction.py"
        )
        source = recon_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {"scale_engine", "scale_engine_narrow", "scale_engine_edr", "gravity"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                elif isinstance(node, ast.Import):
                    names.extend(alias.name for alias in node.names)
                for name in names:
                    base = name.split(".")[0]
                    assert base not in forbidden, (
                        f"reconstruction.py imports '{name}' — "
                        "planner must stay decoupled from the conform pipeline."
                    )


# ── _build_layer_payloads tests (Task 5) ────────────────────────────────────


def _make_conformed_result(layers_data):
    """Build a minimal conformed_result dict matching ScaleEngine.conform() output."""
    return {"layers": layers_data}


def _make_conformed_layer(
    uid="abc123",
    index=1,
    layer_kind="av",
    parent_index=-1,
    containing_comp_id=None,
    position=None,
    scale=None,
    anchor=None,
    rotation=0.0,
    is_root=None,
):
    """Build a minimal conformed layer dict with conformed_transforms."""
    ct = {
        "position": position or [540.0, 960.0, 0.0],
        "scale": scale or [100.0, 100.0, 100.0],
        "anchor": anchor or [200.0, 50.0, 0.0],
        "rotation": rotation,
    }
    if is_root is not None:
        ct["is_root"] = is_root
    return {
        "uid": uid,
        "index": index,
        "layer_kind": layer_kind,
        "parent_index": parent_index,
        "containing_comp_id": containing_comp_id,
        "conformed_transforms": ct,
    }


def _make_plan_with_comps(comp_names=None):
    """Build a minimal ReconstructionPlan with the given comp names."""
    from core.reconstruction import ReconstructionPlan, TargetSpec
    if comp_names is None:
        comp_names = ["Recon_1080x1920"]
    plan = ReconstructionPlan(
        target=TargetSpec(1080, 1920),
        new_comps=[
            {"name": name, "width": 1080, "height": 1920}
            for name in comp_names
        ],
    )
    return plan


class TestLayerPayloadBuilder:
    """Unit tests for _build_layer_payloads in orchestrator.py.

    This function is a pure helper — no I/O, no AE contact. Tests use
    synthetic conformed layers. The 87N real-manifest test exercises
    the full ScaleEngine → _build_layer_payloads chain.
    """

    def _import_builder(self):
        import importlib
        import importlib.util
        # Import the orchestrator module by path (it lives at python/orchestrator.py
        # but registers as __main__ when run via python -m python; import by spec).
        spec = importlib.util.spec_from_file_location(
            "_orchestrator_test",
            str(Path(__file__).parent.parent / "orchestrator.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._build_layer_payloads

    def test_payload_contains_uid_and_index(self):
        build = self._import_builder()
        manifest = _make_manifest_ns([], width=1920, height=1080)
        manifest.project_info.name = "TestComp"
        layer = _make_conformed_layer(uid="deadbeef", index=3, is_root=True)
        conformed_result = _make_conformed_result([layer])
        plan = _make_plan_with_comps(["Recon_1080x1920"])

        result = build(manifest, conformed_result, plan)
        payloads = result.get("Recon_1080x1920", [])
        assert len(payloads) == 1
        assert payloads[0]["uid"] == "deadbeef"
        assert payloads[0]["source_index"] == 3

    def test_root_layers_included_with_is_root_true(self):
        """A layer with parent_index=-1 must yield is_root=True and skip_transforms=False."""
        build = self._import_builder()
        manifest = _make_manifest_ns([], width=1920, height=1080)
        manifest.project_info.name = "TestComp"
        layer = _make_conformed_layer(uid="root1", index=1, parent_index=-1, is_root=True)
        conformed_result = _make_conformed_result([layer])
        plan = _make_plan_with_comps(["Recon_1080x1920"])

        result = build(manifest, conformed_result, plan)
        payloads = result["Recon_1080x1920"]
        assert len(payloads) == 1
        assert payloads[0]["is_root"] is True
        assert payloads[0]["skip_transforms"] is False

    def test_child_layers_included_with_skip_transforms(self):
        """A layer with parent_index=2 (child) must yield skip_transforms=True."""
        build = self._import_builder()
        manifest = _make_manifest_ns([], width=1920, height=1080)
        manifest.project_info.name = "TestComp"
        # Child: parent_index >= 1, is_root=False from conformed_transforms
        layer = _make_conformed_layer(uid="child1", index=2, parent_index=1, is_root=False)
        conformed_result = _make_conformed_result([layer])
        plan = _make_plan_with_comps(["Recon_1080x1920"])

        result = build(manifest, conformed_result, plan)
        payloads = result["Recon_1080x1920"]
        assert len(payloads) == 1
        assert payloads[0]["is_root"] is False
        assert payloads[0]["skip_transforms"] is True

    def test_structural_layers_included_with_kind_flag(self):
        """Camera layers must be included in the payload with layer_kind='camera'."""
        build = self._import_builder()
        manifest = _make_manifest_ns([], width=1920, height=1080)
        manifest.project_info.name = "TestComp"
        layer = _make_conformed_layer(uid="cam1", index=1, layer_kind="camera", is_root=True)
        conformed_result = _make_conformed_result([layer])
        plan = _make_plan_with_comps(["Recon_1080x1920"])

        result = build(manifest, conformed_result, plan)
        payloads = result["Recon_1080x1920"]
        assert len(payloads) == 1
        assert payloads[0]["layer_kind"] == "camera"

    def test_payload_positions_come_from_conformed_transforms(self):
        """Payload position must come from conformed_transforms, not source position."""
        build = self._import_builder()
        manifest = _make_manifest_ns([], width=1920, height=1080)
        manifest.project_info.name = "TestComp"
        # Conformed position is different from the source default
        conformed_pos = [540.0, 960.0, 0.0]
        conformed_scale = [56.25, 56.25, 100.0]
        conformed_anchor = [100.0, 25.0, 0.0]
        layer = _make_conformed_layer(
            uid="hero1", index=1, is_root=True,
            position=conformed_pos, scale=conformed_scale, anchor=conformed_anchor,
            rotation=0.0,
        )
        # Source position (layer-level, not conformed) would be different
        # — we just check the payload reads from conformed_transforms
        conformed_result = _make_conformed_result([layer])
        plan = _make_plan_with_comps(["Recon_1080x1920"])

        result = build(manifest, conformed_result, plan)
        payloads = result["Recon_1080x1920"]
        assert payloads[0]["position"] == conformed_pos
        assert payloads[0]["scale"] == conformed_scale
        assert payloads[0]["anchor"] == conformed_anchor
        assert payloads[0]["rotation_z"] == 0.0

    def test_empty_conformed_result_yields_empty_payloads(self):
        """No layers → empty payload list, no crash."""
        build = self._import_builder()
        manifest = _make_manifest_ns([], width=1920, height=1080)
        manifest.project_info.name = "TestComp"
        conformed_result = _make_conformed_result([])
        plan = _make_plan_with_comps(["Recon_1080x1920"])

        result = build(manifest, conformed_result, plan)
        assert result["Recon_1080x1920"] == []

    def test_87n_manifest_payload_count_matches_layer_count(self):
        """87N fixture: payload count must equal total manifest layer count.

        All 19 layers (including camera and structural) should be included
        in layer_payloads (Phase 1: include everything, flag cameras by kind).
        """
        build = self._import_builder()

        from core.scale_engine import ScaleEngine

        manifest = _load_87n_manifest()
        engine = ScaleEngine(manifest, 1080, 1920, "Fit", 0.0)
        conformed_result = engine.conform()

        plan = _make_plan_with_comps(["Recon_TikTok_1080x1920"])

        result = build(manifest, conformed_result, plan)
        payloads = result.get("Recon_TikTok_1080x1920", [])

        # All 19 layers from the root comp (containing_comp_id=None) must appear
        assert len(payloads) == len(manifest.layers), (
            f"Expected {len(manifest.layers)} payloads, got {len(payloads)}"
        )
        # Every payload must have a uid
        for p in payloads:
            assert p["uid"], f"Missing uid in payload: {p}"
        # Every payload must have source_comp_name matching the manifest
        for p in payloads:
            assert p["source_comp_name"] == manifest.project_info.name
