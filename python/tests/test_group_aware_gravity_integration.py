# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_group_aware_gravity_integration.py

End-to-end coverage for the group-aware gravity fix (2026-06-29),
running through the real `ScaleEngine.conform()` pipeline rather than
calling `apply_gravity()` directly (see test_gravity.py::TestGroupAwareGravity
for the unit-level coverage of that function).

Background: the 87N HD→TikTok conform has five HERO-tagged text-body
layers ("EVERYTHING", "YOU WANT", "IS ON THE", "OTHER SIDE", "OF FEAR")
that form a sequential reveal 454px apart in the HD source. Before this
fix, `gravity.py`'s "center" branch snapped every CENTER/HERO-tagged
layer to the identical safe-area-center pixel — all five collapsed onto
one point, destroying the reveal. This test pins the fixed behavior
using the real 87N source manifest (not a synthetic fixture) per the
project's "no synthetic fixtures for JSX↔Python contract bugs" rule —
this is a Python-internal contract, but the real manifest is what
originally surfaced the bug, so it's the canonical regression guard.
"""

from __future__ import annotations

import json
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.scale_engine import ScaleEngine
from models.scrape_manifest import ScrapeManifest

_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "bug_l" / "87n_source_manifest.json"
)

# The five sequentially-revealed text-body layers, in HD Y-position order.
_TEXT_BODY_LAYERS = (
    "EVERYTHING ",
    "YOU WANT",
    "IS ON THE",
    "OTHER SIDE",
    "OF FEAR",
)


def _load_manifest() -> ScrapeManifest:
    with open(_FIXTURE_PATH) as f:
        return ScrapeManifest.model_validate(json.load(f))


def _conform_to_tiktok() -> dict:
    manifest = _load_manifest()
    engine = ScaleEngine(
        manifest=manifest,
        target_width=1080,
        target_height=1920,
        scale_mode="Fit",
        bleed_pct=0.05,
    )
    return engine.conform()


class TestFiveLineStackIntegration:
    """The real bug, fixed: five text-body layers must land at distinct,
    spaced Y positions in the TikTok conform — not collapse onto one
    pixel."""

    def test_five_text_layers_land_at_distinct_y_positions(self):
        result = _conform_to_tiktok()
        by_name = {l["name"]: l for l in result["layers"]}

        y_positions = []
        for name in _TEXT_BODY_LAYERS:
            layer = by_name.get(name)
            assert layer is not None, f"Layer {name!r} not found in conform output"
            tf = layer.get("conformed_transforms") or {}
            pos = tf.get("position")
            assert pos is not None, f"{name}: no conformed position"
            y_positions.append(pos[1])

        # The core regression: pre-fix, all five Y values were identical
        # (950.4). Post-fix they must all differ.
        assert len(set(round(y, 3) for y in y_positions)) == 5, (
            f"Five text layers collapsed onto fewer than 5 distinct Y "
            f"positions: {y_positions}"
        )

    def test_five_text_layers_preserve_source_ordering(self):
        """The relative vertical ORDER of the five lines (which one is
        visually above which) must survive the conform — group-aware
        gravity preserves offsets, but a sign error would scramble the
        order even while keeping positions distinct."""
        manifest = _load_manifest()
        src_by_name = {l.name: l for l in manifest.layers}
        src_order = sorted(
            _TEXT_BODY_LAYERS,
            key=lambda n: src_by_name[n].position[1],
        )

        result = _conform_to_tiktok()
        dst_by_name = {l["name"]: l for l in result["layers"]}
        dst_order = sorted(
            _TEXT_BODY_LAYERS,
            key=lambda n: dst_by_name[n]["conformed_transforms"]["position"][1],
        )

        assert src_order == dst_order, (
            f"Source vertical order {src_order} does not match "
            f"conformed order {dst_order} — group offsets were applied "
            f"with a sign or axis error."
        )

    def test_five_text_layers_spacing_scales_by_uniform_S(self):
        """Spacing between consecutive lines must be the HD spacing
        scaled by the conform's uniform S — not collapsed (0px) and not
        left unscaled (full HD spacing)."""
        manifest = _load_manifest()
        S = ScaleEngine(
            manifest=_load_manifest(), target_width=1080, target_height=1920,
            scale_mode="Fit", bleed_pct=0.05,
        ).conform()["scale"]["S"]

        src_by_name = {l.name: l for l in manifest.layers}
        result = _conform_to_tiktok()
        dst_by_name = {l["name"]: l for l in result["layers"]}

        ordered = sorted(
            _TEXT_BODY_LAYERS,
            key=lambda n: src_by_name[n].position[1],
        )
        for a, b in zip(ordered, ordered[1:]):
            src_dy = src_by_name[b].position[1] - src_by_name[a].position[1]
            dst_dy = (
                dst_by_name[b]["conformed_transforms"]["position"][1]
                - dst_by_name[a]["conformed_transforms"]["position"][1]
            )
            expected_dy = src_dy * S
            assert abs(dst_dy - expected_dy) < 0.5, (
                f"{a}->{b}: expected spacing {expected_dy:.2f} "
                f"(src {src_dy:.2f} * S={S:.4f}), got {dst_dy:.2f}"
            )

    def test_single_camera_layer_unaffected_by_grouping(self):
        """Sanity: the camera (group size 1, no baseline gravity rule —
        structural) is untouched by the group-aware gravity change.
        Pins the Bug L K-mode camera Z math stays exactly as before:
        static position.Z scales by K (87N has animated depth, so the
        camera auto-detects K mode — see test_camera_depth_mode.py)."""
        manifest = _load_manifest()
        src_cam = next(l for l in manifest.layers if l.layer_kind == "camera")
        result = _conform_to_tiktok()
        cam = next(l for l in result["layers"] if l.get("layer_kind") == "camera")
        pos = cam["conformed_transforms"]["position"]
        K = max(1080 / 1920, 1920 / 1080)
        assert abs(pos[2] - (src_cam.position[2] * K)) < 0.5


class TestGravityGroupSizeDiagnostic:
    """`gravity_group_size` (diagnostic-only, PR #121 follow-up) must
    reflect the same grouping the offset math above already uses — and
    must be byte-irrelevant to the transform math itself, since this is
    bookkeeping on top of an already-computed value, not new computation."""

    def test_five_text_layers_carry_consistent_group_size(self):
        """The group-aware gravity pre-pass groups by
        (containing_comp_id, canonical_tag) only — not by any visual/
        logical sub-grouping. The 87N root comp has 11 layers that
        canonicalize to CENTER (the 5 HERO text lines, 4 BOXART overlay
        plates, 1 BOXART background plate, 1 ARTWORK video), so all 11
        share one group and one offset-anchored centroid — not just the
        5 text lines a human would visually group together. This is the
        known, documented scope of the v1.0.x minimal version (see
        docs/roadmap/deferred/TODO-compositional-reformat-system.md);
        cross-tag/sub-group pairing is Stage 2, not this fix."""
        result = _conform_to_tiktok()
        by_name = {l["name"]: l for l in result["layers"]}

        for name in _TEXT_BODY_LAYERS:
            layer = by_name[name]
            assert layer.get("gravity_group_size") == 11, (
                f"{name}: expected gravity_group_size 11 (full root-comp "
                f"CENTER group), got {layer.get('gravity_group_size')!r}"
            )

    def test_structural_camera_carries_no_group_size(self):
        """Cameras never reach the group-aware gravity branch (no
        resolved gravity rule) — gravity_group_size must stay None, not
        be coerced to 0 or 1."""
        result = _conform_to_tiktok()
        cam = next(l for l in result["layers"] if l.get("layer_kind") == "camera")
        assert cam.get("gravity_group_size") is None

    def test_group_size_does_not_change_conformed_positions(self):
        """Adding the diagnostic field must not perturb the transform
        math it's reporting on — positions for the five-line group must
        match the values pinned by TestFiveLineStackIntegration."""
        result = _conform_to_tiktok()
        by_name = {l["name"]: l for l in result["layers"]}
        y_positions = [
            by_name[name]["conformed_transforms"]["position"][1]
            for name in _TEXT_BODY_LAYERS
        ]
        assert len(set(round(y, 3) for y in y_positions)) == 5
