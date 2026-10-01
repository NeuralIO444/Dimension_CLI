# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_artwork_bounds_gravity.py
Unit tests for Hard Problem 1 (HP-01): Artwork Bounds, Optical Centroid & Safe-Width Clamping.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.gravity import apply_gravity, baseline_rule_for, DEFAULT_SAFE_AREA
from core.scale_engine import ScaleEngine
from models.scrape_manifest import ArtworkBounds, LayerModel, ProjectInfo, ScrapeManifest
from models.studio_profile import ProfileRule, StudioProfile


def test_artwork_bounds_optical_centroid_shift():
    """Verify that a layer with an off-center optical centroid is shifted to true visual center.

    Bug #436 (2026-09-04): this fixture originally used `centroid=[960.0, 440.0]`
    -- a value that only makes sense as a WORLD-space coordinate (near the
    comp's own center). Live AE ground truth confirmed the real producer
    (SovCore_Layer.jsx) emits `centroid` in LAYER-LOCAL space, relative to
    the layer's own anchor -- e.g. a shape with content 100px "above" its own
    anchor reports `centroid=[0, -100]`, regardless of where that layer sits
    in the comp. The old fixture encoded the same wrong coordinate-space
    assumption as the bug it was meant to catch, which is exactly why it
    passed while the real pipeline produced [2246, 1910] for an ordinary
    layer (see the #436 wrap-up). Corrected to local-space semantics here.
    """
    rule = baseline_rule_for("CENTER")
    src_pos = [960.0, 540.0, 0.0]
    src_center = (960.0, 540.0)
    tgt_size = (1080, 1920)

    # Standard gravity without bounds
    pos_standard, s_standard = apply_gravity(
        rule=rule,
        src_pos=src_pos,
        src_center=src_center,
        tgt_size=tgt_size,
        safe_area=DEFAULT_SAFE_AREA,
        base_scale=0.5625,
        fill_scale=1.7778,
    )

    # Local-space bounds: visual content sits 100px ABOVE this layer's own
    # anchor (centroid y = -100, local), regardless of the layer's position.
    bounds = ArtworkBounds(
        left=-360.0,
        top=-240.0,
        width=720.0,
        height=280.0,
        centroid=[0.0, -100.0]
    )

    pos_optical, s_optical = apply_gravity(
        rule=rule,
        src_pos=src_pos,
        src_center=src_center,
        tgt_size=tgt_size,
        safe_area=DEFAULT_SAFE_AREA,
        base_scale=0.5625,
        fill_scale=1.7778,
        artwork_bounds=bounds,
    )

    # The visual mass sits ABOVE the anchor (negative local y), so the
    # anchor must be pinned LOWER than standard to pull the mass into
    # center -- Y increases.
    assert pos_optical[0] == pytest.approx(pos_standard[0], abs=0.01)
    assert pos_optical[1] > pos_standard[1]
    assert pos_optical[1] == pytest.approx(pos_standard[1] + 100.0 * 0.5625, abs=0.01)


def test_artwork_bounds_symmetric_centroid_produces_zero_delta_regardless_of_position():
    """Issue #436 regression: a shape whose local content is centered on its
    own anchor (centroid == [0, 0]) must produce NO optical adjustment,
    regardless of where the layer sits in the comp.

    Before the fix, this required the layer to coincidentally sit at the
    comp's own center to produce zero delta -- any other position produced
    a large bogus displacement (reproduced live: [400, 400] -> [2246, 1910]
    against a 1080x1920 narrow target). A symmetric shape is the common
    case (most simple logos/badges are centered on their own anchor), so
    this was not a rare edge case.
    """
    rule = baseline_rule_for("CENTER")
    src_center = (960.0, 540.0)
    tgt_size = (1080, 1920)
    safe_area = DEFAULT_SAFE_AREA
    base_scale = 0.5625

    symmetric_bounds = ArtworkBounds(
        left=-100.0, top=-100.0, width=200.0, height=200.0, centroid=[0.0, 0.0],
    )

    # Off-center source position -- exactly the class of layer that
    # triggered #436 (the comp's own center is the ONE position the old
    # bug happened not to corrupt).
    for src_pos in ([400.0, 400.0, 0.0], [1520.0, 800.0, 0.0], [100.0, 1000.0, 0.0]):
        pos_with_bounds, _ = apply_gravity(
            rule=rule, src_pos=src_pos, src_center=src_center, tgt_size=tgt_size,
            safe_area=safe_area, base_scale=base_scale, fill_scale=1.7778,
            artwork_bounds=symmetric_bounds,
        )
        pos_without_bounds, _ = apply_gravity(
            rule=rule, src_pos=src_pos, src_center=src_center, tgt_size=tgt_size,
            safe_area=safe_area, base_scale=base_scale, fill_scale=1.7778,
            artwork_bounds=None,
        )
        assert pos_with_bounds == pytest.approx(pos_without_bounds, abs=0.01), (
            f"src_pos={src_pos}: a symmetric local centroid must not move the "
            f"pin at all -- got {pos_with_bounds} vs baseline {pos_without_bounds}"
        )
        # Also pin down the actual failure mode: neither coordinate should
        # be anywhere near the old bug's magnitude (well outside the target
        # frame entirely).
        assert abs(pos_with_bounds[0]) < tgt_size[0] * 2
        assert abs(pos_with_bounds[1]) < tgt_size[1] * 2


def test_artwork_bounds_rotation_90_projects_x_to_y():
    """Issue #439: A shape layer rotated 90°, local centroid = [200, 0],
    position = [960, 540], conformed to 1080x1920:

    The local X-offset should project onto the world Y-axis for a 90° rotation.
    The optical shift should be entirely on Y, leaving X unchanged.
    """
    rule = baseline_rule_for("CENTER")
    src_pos = [960.0, 540.0, 0.0]
    src_center = (960.0, 540.0)
    tgt_size = (1080, 1920)
    base_scale = 0.5625

    bounds = ArtworkBounds(
        left=100.0, top=-50.0, width=200.0, height=100.0, centroid=[200.0, 0.0]
    )

    pos_baseline, _ = apply_gravity(
        rule=rule,
        src_pos=src_pos,
        src_center=src_center,
        tgt_size=tgt_size,
        safe_area=DEFAULT_SAFE_AREA,
        base_scale=base_scale,
        fill_scale=1.7778,
    )

    pos_rotated, _ = apply_gravity(
        rule=rule,
        src_pos=src_pos,
        src_center=src_center,
        tgt_size=tgt_size,
        safe_area=DEFAULT_SAFE_AREA,
        base_scale=base_scale,
        fill_scale=1.7778,
        artwork_bounds=bounds,
        layer_anchor=[0.0, 0.0, 0.0],
        layer_scale=[100.0, 100.0, 100.0],
        layer_rotation=90.0,
    )

    # 90° clockwise rotation: (200, 0) -> (0, 200) world offset
    # world_art_cx = 960 + 0 = 960 -> opt_delta_x = 0
    # world_art_cy = 540 + 200 = 740 -> opt_delta_y = (540 - 740) * S = -200 * S
    assert pos_rotated[0] == pytest.approx(pos_baseline[0], abs=0.01)
    assert pos_rotated[1] == pytest.approx(pos_baseline[1] - 200.0 * base_scale, abs=0.01)


def test_artwork_bounds_multiple_rotation_angles():
    """Issue #439: Validate optical centroid calculation across 0°, 90°, 180°, 270°, and 45°."""
    import math

    rule = baseline_rule_for("CENTER")
    src_pos = [500.0, 500.0, 0.0]
    src_center = (960.0, 540.0)
    tgt_size = (1080, 1920)
    base_scale = 0.5

    bounds = ArtworkBounds(
        left=-50.0, top=-50.0, width=100.0, height=100.0, centroid=[100.0, 0.0]
    )

    pos_baseline, _ = apply_gravity(
        rule=rule, src_pos=src_pos, src_center=src_center, tgt_size=tgt_size,
        safe_area=DEFAULT_SAFE_AREA, base_scale=base_scale, fill_scale=1.7778,
    )

    for deg, expected_dx, expected_dy in [
        (0.0, -100.0, 0.0),
        (90.0, 0.0, -100.0),
        (180.0, 100.0, 0.0),
        (270.0, 0.0, 100.0),
        (45.0, -100.0 * math.cos(math.radians(45.0)), -100.0 * math.sin(math.radians(45.0))),
    ]:
        pos_rot, _ = apply_gravity(
            rule=rule, src_pos=src_pos, src_center=src_center, tgt_size=tgt_size,
            safe_area=DEFAULT_SAFE_AREA, base_scale=base_scale, fill_scale=1.7778,
            artwork_bounds=bounds,
            layer_anchor=[0.0, 0.0, 0.0],
            layer_scale=[100.0, 100.0, 100.0],
            layer_rotation=deg,
        )
        assert pos_rot[0] == pytest.approx(pos_baseline[0] + expected_dx * base_scale, abs=0.01)
        assert pos_rot[1] == pytest.approx(pos_baseline[1] + expected_dy * base_scale, abs=0.01)


def test_artwork_bounds_safe_width_clamping():
    """Verify that an oversized artwork bounding box is clamped to the safe area width."""
    rule = baseline_rule_for("CENTER")
    src_pos = [960.0, 540.0, 0.0]
    src_center = (960.0, 540.0)
    tgt_size = (1080, 1920)
    base_scale = 0.5625

    # Safe width in 1080x1920 with 0.04 left/right is 1080 * 0.92 = 993.6px
    # An artwork with width 1800px scaled by 0.5625 = 1012.5px (> 993.6px)
    bounds = ArtworkBounds(
        left=60.0,
        top=300.0,
        width=1800.0,
        height=400.0,
        centroid=[960.0, 500.0]
    )

    pos, s_mul = apply_gravity(
        rule=rule,
        src_pos=src_pos,
        src_center=src_center,
        tgt_size=tgt_size,
        safe_area=DEFAULT_SAFE_AREA,
        base_scale=base_scale,
        fill_scale=1.7778,
        artwork_bounds=bounds,
    )

    # Scale multiplier should be clamped so width * s_mul <= safe_width
    sw = 1080 * (1.0 - DEFAULT_SAFE_AREA.left - DEFAULT_SAFE_AREA.right)
    assert s_mul < base_scale
    assert (bounds.width * s_mul) == pytest.approx(sw, abs=0.01)


def test_artwork_bounds_accounts_for_nonzero_layer_anchor():
    """Second-pass fix, caught by a stress test targeting the first fix's
    remaining gap (Case 2 of the 2026-09-04 gravity-coordinate stress
    test): `centroid` is local to the layer's own coordinate space, and so
    is `anchorPoint` -- but the first pass of the #436 fix
    (`world = px + art_cx`) implicitly assumed anchor sat at the local
    origin. It does not have to.

    Live AE ground truth: a 300x100 rect drawn at local position [150, 50]
    (rect position IS the geometric center of a parametric AE rect) inside
    a layer with anchorPoint=[50, -50] and position=[1700, 950], anchor's
    own coordinate space confirmed identical to sourceRect's (AE reports
    sourceRect=[0,0,300,100] regardless of the anchor value -- anchor does
    not shift where content bounds are reported, it only shifts what
    "position" means). Correct world centroid, matching
    `core/occlusion/mask_solver.py::compute_world_bounds`'s already-proven
    formula: `px + (local - anchor) * scale`:

        world_x = 1700 + (150 - 50) * 1.0 = 1800
        world_y =  950 + (50 - (-50)) * 1.0 = 1050

    The pre-this-test formula computed (1850, 1000) -- wrong by exactly
    the ignored anchor offset (50, -50).
    """
    rule = baseline_rule_for("CENTER")
    src_pos = [1700.0, 950.0, 0.0]
    src_center = (960.0, 540.0)
    tgt_size = (1080, 1920)
    base_scale = 0.5

    bounds = ArtworkBounds(left=0.0, top=0.0, width=300.0, height=100.0, centroid=[150.0, 50.0])

    pos_baseline, _ = apply_gravity(
        rule=rule, src_pos=src_pos, src_center=src_center, tgt_size=tgt_size,
        safe_area=DEFAULT_SAFE_AREA, base_scale=base_scale, fill_scale=1.7778,
    )
    pos, _ = apply_gravity(
        rule=rule, src_pos=src_pos, src_center=src_center, tgt_size=tgt_size,
        safe_area=DEFAULT_SAFE_AREA, base_scale=base_scale, fill_scale=1.7778,
        artwork_bounds=bounds,
        layer_anchor=[50.0, -50.0, 0.0],
        layer_scale=[100.0, 100.0, 100.0],
    )

    # world_art_cx = 1700 + (150-50)*1.0 = 1800 -> opt_delta_x = (1700-1800)*S = -100*S
    # world_art_cy =  950 + (50-(-50))*1.0 = 1050 -> opt_delta_y = (950-1050)*S = -100*S
    assert pos[0] == pytest.approx(pos_baseline[0] - 100.0 * base_scale, abs=0.01)
    assert pos[1] == pytest.approx(pos_baseline[1] - 100.0 * base_scale, abs=0.01)


def test_artwork_bounds_accounts_for_layers_own_scale():
    """Case 3 of the same stress test: a layer scaled 400% with a local
    centroid of [-100, -100]. `sourceRectAtTime` (the source of
    `centroid`) reports content bounds PRE-transform -- confirmed live: a
    50x50 rect on a layer scaled to 400% still reports sourceRect width 50,
    not 200. So the layer's own scale must be applied when converting the
    local centroid to world space, or the delta comes out 4x too small.

    Correct: world_x = 200 + (-100 - 0) * 4.0 = -200
    Pre-this-test formula computed 100 -- wrong by 300 (3x the local
    offset), because it silently assumed 100% scale.
    """
    rule = baseline_rule_for("CENTER")
    src_pos = [200.0, 200.0, 0.0]
    src_center = (960.0, 540.0)
    tgt_size = (1080, 1920)
    base_scale = 0.5

    bounds = ArtworkBounds(left=-125.0, top=-125.0, width=50.0, height=50.0, centroid=[-100.0, -100.0])

    pos_baseline, _ = apply_gravity(
        rule=rule, src_pos=src_pos, src_center=src_center, tgt_size=tgt_size,
        safe_area=DEFAULT_SAFE_AREA, base_scale=base_scale, fill_scale=1.7778,
    )
    pos, _ = apply_gravity(
        rule=rule, src_pos=src_pos, src_center=src_center, tgt_size=tgt_size,
        safe_area=DEFAULT_SAFE_AREA, base_scale=base_scale, fill_scale=1.7778,
        artwork_bounds=bounds,
        layer_anchor=[0.0, 0.0, 0.0],
        layer_scale=[400.0, 400.0, 100.0],
    )

    # world_art_cx = 200 + (-100-0)*4.0 = -200 -> opt_delta_x = (200-(-200))*S = 400*S
    assert pos[0] == pytest.approx(pos_baseline[0] + 400.0 * base_scale, abs=0.01)
    assert pos[1] == pytest.approx(pos_baseline[1] + 400.0 * base_scale, abs=0.01)


def test_artwork_bounds_anchor_and_scale_default_to_identity_when_omitted():
    """A caller that doesn't pass layer_anchor/layer_scale (every call site
    that existed before this second pass) must get exactly the first-pass
    #436 fix's behavior -- anchor=[0,0], scale=100% -- not a crash or a
    silently different result."""
    rule = baseline_rule_for("CENTER")
    src_pos = [400.0, 400.0, 0.0]
    src_center = (960.0, 540.0)
    tgt_size = (1080, 1920)
    bounds = ArtworkBounds(left=-100.0, top=-100.0, width=200.0, height=200.0, centroid=[0.0, 0.0])

    pos_omitted, _ = apply_gravity(
        rule=rule, src_pos=src_pos, src_center=src_center, tgt_size=tgt_size,
        safe_area=DEFAULT_SAFE_AREA, base_scale=0.5625, fill_scale=1.7778,
        artwork_bounds=bounds,
    )
    pos_explicit_identity, _ = apply_gravity(
        rule=rule, src_pos=src_pos, src_center=src_center, tgt_size=tgt_size,
        safe_area=DEFAULT_SAFE_AREA, base_scale=0.5625, fill_scale=1.7778,
        artwork_bounds=bounds, layer_anchor=[0.0, 0.0, 0.0], layer_scale=[100.0, 100.0, 100.0],
    )
    assert pos_omitted == pos_explicit_identity


def test_artwork_bounds_none_backward_compatible():
    """Verify that when artwork_bounds is None, output is 100% byte-identical to standard math."""
    rule = baseline_rule_for("TOP")
    src_pos = [960.0, 200.0, 0.0]
    src_center = (960.0, 540.0)
    tgt_size = (1080, 1920)

    pos1, s1 = apply_gravity(rule, src_pos, src_center, tgt_size, DEFAULT_SAFE_AREA, 0.5625, 1.7778)
    pos2, s2 = apply_gravity(rule, src_pos, src_center, tgt_size, DEFAULT_SAFE_AREA, 0.5625, 1.7778, artwork_bounds=None)

    assert pos1 == pos2
    assert s1 == s2


class TestArtworkBoundsThroughRealConform:
    """Issue #336 -- the gravity-level unit tests above prove apply_gravity()'s
    HP-01 math is correct in isolation, but not that a layer's artwork_bounds
    field actually SURVIVES a real ScaleEngine.conform() call and reaches
    that math. This closes that gap: hand-built manifests run through the
    real conform() entry point (core.scale_engine_narrow's rule set, since
    the safe-width clamp only fires on narrow-aspect targets), not
    apply_gravity() called directly."""

    def _manifest(self, artwork_bounds):
        return ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(name="ClampWiring", width=1920, height=1080, fps=30.0),
            layers=[
                LayerModel(
                    index=1,
                    name="Wide_Lockup",
                    content_tag="CENTER",
                    position=[960.0, 540.0, 0.0],
                    scale=[100.0, 100.0, 100.0],
                    artwork_bounds=artwork_bounds,
                ),
            ],
        )

    def _conform(self, artwork_bounds):
        manifest = self._manifest(artwork_bounds)
        engine = ScaleEngine(manifest, target_width=1080, target_height=1920, scale_mode="Fit", bleed_pct=0.0)
        result = engine.conform()
        assert result["status"] == "SAFE"
        layer = result["layers"][0]
        return layer["conformed_transforms"]["scale"], layer["conformed_transforms"]["position"]

    def test_wide_artwork_bounds_clamps_through_real_conform(self):
        """A layer whose real (source-space) artwork is much wider than its
        source_rect/world_bounds would suggest -- e.g. a wide horizontal
        lockup on an otherwise-square bounding box -- must come out of a
        REAL conform() call scaled down further than the unclamped base
        scale, because the wiring threads artwork_bounds all the way from
        LayerModel through scale_engine_narrow.py into gravity.py's clamp."""
        wide_bounds = ArtworkBounds(
            left=60.0, top=440.0, width=1800.0, height=200.0,
            centroid=[960.0, 540.0],
        )
        scale_clamped, _ = self._conform(wide_bounds)

        scale_unclamped, _ = self._conform(None)

        # The clamp must have actually reduced the scale below what an
        # unbounded conform would produce -- proves the real wiring path
        # reached gravity.py's clamp, not just that conform() ran without
        # erroring.
        assert scale_clamped[0] < scale_unclamped[0]
        assert scale_clamped[1] < scale_unclamped[1]

    def test_narrow_artwork_bounds_does_not_clamp_through_real_conform(self):
        """A layer whose artwork_bounds is already narrow enough to fit the
        safe corridor must produce byte-identical scale to the no-bounds
        case -- the clamp only ever activates when actually needed.

        Bug #436: `centroid=[960.0, 540.0]` (this layer's own position) was
        the old fixture's way of saying "no optical adjustment needed" --
        which only worked because the old (buggy) math computed the delta
        against the comp's world center, and this layer happened to sit
        exactly there. Under local-space semantics (the real producer's
        actual convention), "no adjustment needed" is `centroid=[0, 0]` --
        content centered on the layer's own anchor -- independent of where
        the layer sits. Corrected so this test still asserts what it always
        meant to (no clamp, no positional side effect), on the right
        contract.
        """
        narrow_bounds = ArtworkBounds(
            left=-100.0, top=-100.0, width=200.0, height=200.0,
            centroid=[0.0, 0.0],
        )
        scale_narrow, pos_narrow = self._conform(narrow_bounds)
        scale_none, pos_none = self._conform(None)

        assert scale_narrow == scale_none
        assert pos_narrow == pos_none

    def test_rotated_artwork_bounds_through_real_conform(self):
        """Issue #439: A rotated layer conformed via ScaleEngine.conform()
        must thread its rotation_z into apply_gravity() and calculate the
        correct optical shift along the rotated axis."""
        bounds = ArtworkBounds(
            left=100.0, top=-50.0, width=200.0, height=100.0, centroid=[200.0, 0.0]
        )
        manifest = ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(name="RotateConform", width=1920, height=1080, fps=30.0),
            layers=[
                LayerModel(
                    index=1,
                    name="Rotated_Layer",
                    content_tag="CENTER",
                    position=[960.0, 540.0, 0.0],
                    scale=[100.0, 100.0, 100.0],
                    rotation_z=90.0,
                    artwork_bounds=bounds,
                ),
            ],
        )
        engine = ScaleEngine(manifest, target_width=1080, target_height=1920, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()
        assert res["status"] == "SAFE"
        conformed_layer = res["layers"][0]
        pos = conformed_layer["conformed_transforms"]["position"]

        # Baseline without artwork bounds:
        manifest_base = ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(name="RotateConformBase", width=1920, height=1080, fps=30.0),
            layers=[
                LayerModel(
                    index=1,
                    name="Rotated_Layer_Base",
                    content_tag="CENTER",
                    position=[960.0, 540.0, 0.0],
                    scale=[100.0, 100.0, 100.0],
                    rotation_z=90.0,
                ),
            ],
        )
        res_base = ScaleEngine(manifest_base, target_width=1080, target_height=1920, scale_mode="Fit", bleed_pct=0.0).conform()
        pos_base = res_base["layers"][0]["conformed_transforms"]["position"]

        # 90° rotation means X offset in local centroid projects to Y in world space
        assert pos[0] == pytest.approx(pos_base[0], abs=0.01)
        assert pos[1] == pytest.approx(pos_base[1] - 200.0 * (1080.0 / 1920.0), abs=0.01)


class TestUseArtworkBoundaryOptIn:
    """Issue #331 -- ProfileRule.use_artwork_boundary. A custom studio
    profile's placement is deliberately hand-tuned per client, so HP-01
    must not shift it just because artwork_bounds happens to be present;
    it needs an explicit per-rule opt-in. Baseline tags (_BaselineRule)
    default this True instead, matching the already-shipped, already-
    tested behavior every other test in this file proves (Matt's
    2026-09-06 decision on #331: "fixed automatically" for the default/
    baseline path, true opt-in only for custom profile rules)."""

    OFF_CENTER_BOUNDS = ArtworkBounds(
        left=1700.0, top=980.0, width=200.0, height=100.0, centroid=[200.0, 100.0],
    )

    def _manifest(self):
        return ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(name="ProfileOptIn", width=1920, height=1080, fps=30.0),
            layers=[
                LayerModel(
                    index=1,
                    name="LOGO_Corner",
                    position=[960.0, 540.0, 0.0],
                    scale=[100.0, 100.0, 100.0],
                    artwork_bounds=self.OFF_CENTER_BOUNDS,
                ),
            ],
        )

    def _conform(self, profile):
        engine = ScaleEngine(
            self._manifest(), target_width=1080, target_height=1920,
            scale_mode="Fit", bleed_pct=0.0, profile=profile,
        )
        result = engine.conform()
        assert result["status"] == "SAFE"
        return result["layers"][0]["conformed_transforms"]["position"]

    def test_profile_rule_without_opt_in_ignores_artwork_bounds(self):
        """The current default: a profile rule that never mentions
        use_artwork_boundary (every real profile YAML today) must
        produce byte-identical placement whether or not the layer
        happens to carry artwork_bounds -- proves the gate, not just
        that conform() runs."""
        profile = StudioProfile(
            id="test_profile",
            prefixes=[ProfileRule(match="LOGO_", tag="LOGO", gravity="center")],
        )
        pos_with_bounds = self._conform(profile)

        no_bounds_manifest = self._manifest()
        no_bounds_manifest.layers[0].artwork_bounds = None
        engine = ScaleEngine(
            no_bounds_manifest, target_width=1080, target_height=1920,
            scale_mode="Fit", bleed_pct=0.0, profile=profile,
        )
        pos_without_bounds = engine.conform()["layers"][0]["conformed_transforms"]["position"]

        assert pos_with_bounds == pos_without_bounds

    def test_profile_rule_with_explicit_opt_in_applies_hp01(self):
        """Setting use_artwork_boundary=True on the rule is what makes a
        custom profile's placement react to artwork_bounds at all."""
        profile_off = StudioProfile(
            id="test_profile_off",
            prefixes=[ProfileRule(match="LOGO_", tag="LOGO", gravity="center",
                                   use_artwork_boundary=False)],
        )
        profile_on = StudioProfile(
            id="test_profile_on",
            prefixes=[ProfileRule(match="LOGO_", tag="LOGO", gravity="center",
                                   use_artwork_boundary=True)],
        )
        pos_off = self._conform(profile_off)
        pos_on = self._conform(profile_on)

        assert pos_on != pos_off

    def test_default_false_matches_omitting_the_field_entirely(self):
        """Sanity check on the Pydantic default itself, independent of
        ScaleEngine -- use_artwork_boundary=False is what you get whether
        you explicitly set it or just never mention it."""
        explicit = ProfileRule(match="X_", tag="TT", use_artwork_boundary=False)
        implicit = ProfileRule(match="X_", tag="TT")
        assert explicit.use_artwork_boundary is implicit.use_artwork_boundary is False

    def test_baseline_rule_defaults_true(self):
        """The other half of the split: baseline_rule_for(...) (no
        profile matched) must default use_artwork_boundary=True,
        preserving HP-01's already-shipped unconditional behavior for
        every tag covered elsewhere in this file."""
        rule = baseline_rule_for("CENTER")
        assert rule.use_artwork_boundary is True

