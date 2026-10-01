# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_output_comp_name_wiring.py — Track B / B2 (2026-08-26).

Coverage for the two hops `output_name_template` must survive between
the preset a user picked and the `output_comp_name` Babysitter reads:

  1. `stages.conform_io.resolve_conform_target` — pulls
     `Preset.output_name_template` onto `ConformTarget` when a preset
     is used; `None` when conforming via raw --width/--height (no
     preset to pull a template from).
  2. `logic.exporter.PayloadSlicer.slice_and_export` — resolves
     `output_name_template` (or `DEFAULT_OUTPUT_NAME_TEMPLATE` when
     None) into `output_comp_name` on the legacy single-comp path
     (`mirror_tree` absent); the field must NOT appear when a
     mirror_tree is present (Babysitter takes the N-comp path instead).

This is a regression test for a real crash caught during Track B: the
CLI orchestrator's legacy single-comp path called
`resolve_output_name(..., template=target.output_name_template, ...)`
before `ConformTarget` carried that field, raising
`AttributeError: 'ConformTarget' object has no attribute
'output_name_template'` on every preset-driven conform
(`test_unit_invariant.py`'s subprocess-based fixture caught it first).
"""

from __future__ import annotations

import json
import os
import sys


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
))

from logic.exporter import PayloadSlicer  # noqa: E402
from models.target import Target  # noqa: E402
from stages.conform import ConformConfig  # noqa: E402
from stages.conform_io import resolve_conform_target  # noqa: E402


def _minimal_conformed_layer(idx=1, name="L"):
    return {
        "index": idx, "name": name, "uid": f"u_{idx}",
        "parent_index": -1, "layer_kind": "av",
        "position": [0, 0, 0], "scale": [100, 100, 100],
        "rotation_z": 0, "anchor": [0, 0, 0],
        "conformed_transforms": {
            "position": [0, 0, 0], "scale": [100, 100, 100],
            "rotation": 0, "anchor": [0, 0, 0],
            "is_root": True,
        },
    }


# ---------------------------------------------------------------------------
# 1. resolve_conform_target — output_name_template propagation
# ---------------------------------------------------------------------------

class TestResolveConformTargetTemplate:
    def test_preset_driven_target_carries_output_name_template(self):
        config = ConformConfig(source="unused", preset="builtin:tiktok_video")
        target = resolve_conform_target(config)
        assert target.output_name_template is not None
        assert isinstance(target.output_name_template, str)

    def test_raw_dimensions_target_has_no_template(self):
        """No preset → nothing to pull a template from. None, not a
        crash, and not a silently-wrong default."""
        config = ConformConfig(source="unused", width=1080, height=1920)
        target = resolve_conform_target(config)
        assert target.output_name_template is None


# ---------------------------------------------------------------------------
# 2. slice_and_export — output_comp_name resolution
# ---------------------------------------------------------------------------

class TestSliceAndExportOutputCompName:
    def test_custom_template_resolves_into_output_comp_name(self, tmp_path):
        out_dir = str(tmp_path / "Chunks")
        slicer = PayloadSlicer(output_dir=out_dir)
        manifest_path = slicer.slice_and_export(
            [_minimal_conformed_layer()],
            expected_comp_name="Hero",
            target_width=1080,
            target_height=1920,
            preset_label="TikTok",
            output_name_template="{source}_DOOH_{width}x{height}",
        )
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert manifest["output_comp_name"] == "Hero_DOOH_1080x1920"

    def test_no_template_falls_back_to_default_token_form(self, tmp_path):
        """None (e.g. raw --width/--height, no preset) must still
        resolve — via DEFAULT_OUTPUT_NAME_TEMPLATE — not crash and not
        skip the field. Babysitter's hardcoded [DIMENSION] fallback
        only fires when output_comp_name is absent entirely."""
        out_dir = str(tmp_path / "Chunks")
        slicer = PayloadSlicer(output_dir=out_dir)
        manifest_path = slicer.slice_and_export(
            [_minimal_conformed_layer()],
            expected_comp_name="Hero",
            target_width=1080,
            target_height=1920,
            preset_label="TikTok",
        )
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert manifest["output_comp_name"] == "Hero_TikTok"

    def test_collision_with_existing_comp_bumps_version(self, tmp_path):
        """Audit fix (2026-08-27) regression test — existing_comp_names
        used to be silently hardcoded empty, so re-conforming the same
        source comp to the same preset always resolved to the
        identical name with no _v2 bump, even though the resolver
        exists specifically to prevent that."""
        out_dir = str(tmp_path / "Chunks")
        slicer = PayloadSlicer(output_dir=out_dir)
        manifest_path = slicer.slice_and_export(
            [_minimal_conformed_layer()],
            expected_comp_name="Hero",
            target_width=1080,
            target_height=1920,
            preset_label="TikTok",
            existing_comp_names={"Hero_TikTok"},
        )
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert manifest["output_comp_name"] == "Hero_TikTok_v2"

    def test_no_existing_comp_names_degrades_to_no_collision_check(self, tmp_path):
        """Omitting existing_comp_names (e.g. no project_structure.json
        on disk) must still resolve a name, not raise — same as
        pre-fix behavior, just without collision protection."""
        out_dir = str(tmp_path / "Chunks")
        slicer = PayloadSlicer(output_dir=out_dir)
        manifest_path = slicer.slice_and_export(
            [_minimal_conformed_layer()],
            expected_comp_name="Hero",
            target_width=1080,
            target_height=1920,
            preset_label="TikTok",
        )
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert manifest["output_comp_name"] == "Hero_TikTok"

    def test_mirror_tree_path_omits_output_comp_name(self, tmp_path):
        """The field is scoped to the legacy single-comp path — when a
        mirror_tree is present, Babysitter takes the N-comp path and
        reads per-entry output_name instead."""
        from models.conformed_manifest import MirrorTreeEntry

        out_dir = str(tmp_path / "Chunks")
        slicer = PayloadSlicer(output_dir=out_dir)
        layer = _minimal_conformed_layer()
        layer["containing_comp_id"] = 1
        manifest_path = slicer.slice_and_export(
            [layer],
            expected_comp_name="Hero",
            target_width=1080,
            target_height=1920,
            preset_label="TikTok",
            output_name_template="{source}_DOOH_{width}x{height}",
            mirror_tree=[MirrorTreeEntry(
                source_comp_id=1, source_comp_name="Hero",
                output_name="Hero_DOOH", is_root=True,
                keep_source_dims=False,
            )],
        )
        with open(manifest_path) as f:
            manifest = json.load(f)
        assert "output_comp_name" not in manifest


# ---------------------------------------------------------------------------
# 3. Target.make — output_name_template pass-through (Save as Preset)
# ---------------------------------------------------------------------------

class TestTargetMakeOutputNameTemplate:
    def _kwargs(self, **overrides):
        base = dict(
            id="custom:test", label="Test", category="custom_signage",
            subcategory="user", width=1080, height=1920,
            aspect_label="9:16", source="custom",
        )
        base.update(overrides)
        return base

    def test_explicit_template_is_used(self):
        t = Target.make(**self._kwargs(
            output_name_template="{source}_DOOH_{width}x{height}",
        ))
        assert t.output_name_template == "{source}_DOOH_{width}x{height}"

    def test_omitted_template_uses_schema_default(self):
        """None must not override the field's own default — a caller
        (e.g. target_cli.py's `add` with no --output-name-template)
        that omits the arg gets the v6 default, not a null template."""
        t = Target.make(**self._kwargs())
        assert t.output_name_template == "{source}_{preset}"

    def test_explicit_none_also_uses_schema_default(self):
        t = Target.make(**self._kwargs(output_name_template=None))
        assert t.output_name_template == "{source}_{preset}"
