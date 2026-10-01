# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_gravity.py
v5.2.2 regression coverage for `core.gravity.apply_gravity`.

Each test fixes the source frame at 1920×1080 with a layer at the
source center, applies a different gravity, and asserts the resulting
position lands inside the expected gravity zone of a 1080×1920 (TikTok)
target. The exact coordinates are second-order — what matters is that
each gravity drives the layer to a deterministic, distinguishable
spot.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
))

from core.gravity import apply_gravity
from models.studio_profile import ProfileRule, SafeArea


def _rule(gravity: str, scale: float = 1.0, weight: str = "normal") -> ProfileRule:
    return ProfileRule(match="X_", tag="TT", gravity=gravity,
                       scale=scale, weight=weight)


@pytest.fixture
def context():
    """Source 1920×1080, target 1080×1920 (TikTok), layer at center."""
    return {
        "src_pos": [960.0, 540.0, 0.0],
        "src_center": (960.0, 540.0),
        "tgt_size": (1080, 1920),
        "safe_area": SafeArea(top=0.05, right=0.05, bottom=0.05, left=0.05),
        "base_scale": 0.5625,                # TikTok Fit ratio
        "fill_scale": 1080.0 / 1920.0 * 1920.0 / 1920.0,  # placeholder
        "scale_z": False,
    }


# Helper to call apply_gravity with a single gravity name.
def _go(ctx, gravity: str, scale_override: float | None = None,
        rule_scale: float = 1.0):
    rule = _rule(gravity, scale=rule_scale)
    base = scale_override if scale_override is not None else ctx["base_scale"]
    return apply_gravity(
        rule, ctx["src_pos"], ctx["src_center"], ctx["tgt_size"],
        ctx["safe_area"], base_scale=base,
        fill_scale=ctx["fill_scale"], scale_z=ctx["scale_z"],
    )


# ── Default / center ────────────────────────────────────────────────


class TestDefaultCenter:
    def test_default_gravity_center_remap(self, context):
        # "center" places at safe-area center. With 5% insets on a
        # 1080×1920 target, safe center should be exactly comp center.
        p, _ = _go(context, "center")
        assert abs(p[0] - 540.0) < 1e-6
        assert abs(p[1] - 960.0) < 1e-6

    def test_unknown_gravity_rejected_by_schema(self):
        # ProfileRule's gravity field is a Literal — invalid values
        # are caught at validate time, never reach apply_gravity.
        # This test pins that contract: schema is the gate, not the
        # function's defensive fallthrough.
        with pytest.raises(Exception):
            _rule("northwest")


# ── Edge gravities ──────────────────────────────────────────────────


class TestTopBottom:
    def test_top_pins_high(self, context):
        p, _ = _go(context, "top")
        # Should land in the top quarter of the target frame.
        assert p[1] < 1920 * 0.30, f"y={p[1]} should be high"

    def test_bottom_pins_low(self, context):
        p, _ = _go(context, "bottom")
        assert p[1] > 1920 * 0.70, f"y={p[1]} should be low"

    def test_topC_centers_horizontally(self, context):
        p, _ = _go(context, "topC")
        assert abs(p[0] - 540.0) < 1.0
        assert p[1] < 1920 * 0.30

    def test_bottomC_centers_horizontally(self, context):
        p, _ = _go(context, "bottomC")
        assert abs(p[0] - 540.0) < 1.0
        assert p[1] > 1920 * 0.70


# ── Horizontal-only gravities ──────────────────────────────────────


class TestCenterH:
    def test_centerH_x_centered_y_remap(self, context):
        p, _ = _go(context, "centerH")
        # X must be safe-area center (≈ comp center for symmetric SA).
        assert abs(p[0] - 540.0) < 1.0


class TestSideMid:
    def test_leftMid_anchors_left(self, context):
        p, _ = _go(context, "leftMid")
        # Should land in the left third of the safe area.
        sa_left_edge = 1080 * 0.05
        assert sa_left_edge <= p[0] < 1080 * 0.30
        # Y should be near the safe-area vertical center.
        assert abs(p[1] - 960.0) < 50

    def test_rightMid_anchors_right(self, context):
        p, _ = _go(context, "rightMid")
        sa_right_edge = 1080 * (1 - 0.05)
        assert 1080 * 0.70 < p[0] <= sa_right_edge
        assert abs(p[1] - 960.0) < 50


# ── Fill ────────────────────────────────────────────────────────────


class TestFill:
    def test_fill_uses_fill_scale_multiplier(self, context):
        # Fill gravity returns the fill_scale multiplier, NOT base_scale.
        ctx = dict(context)
        ctx["fill_scale"] = 1.85   # arbitrary distinct value
        rule = _rule("fill")
        p, mul = apply_gravity(
            rule, ctx["src_pos"], ctx["src_center"], ctx["tgt_size"],
            ctx["safe_area"], base_scale=ctx["base_scale"],
            fill_scale=ctx["fill_scale"], scale_z=False,
        )
        assert abs(mul - 1.85) < 1e-6

    def test_fill_keeps_centered_layer_centered(self, context):
        # A layer at the source center stays at the target center under fill.
        ctx = dict(context)
        ctx["fill_scale"] = 1.0
        rule = _rule("fill")
        p, _ = apply_gravity(
            rule, ctx["src_pos"], ctx["src_center"], ctx["tgt_size"],
            ctx["safe_area"], base_scale=ctx["base_scale"],
            fill_scale=ctx["fill_scale"], scale_z=False,
        )
        assert abs(p[0] - 540.0) < 1e-6
        assert abs(p[1] - 960.0) < 1e-6


# ── Off-center source preserves layout ──────────────────────────────


class TestOffCenterPreserves:
    def test_top_preserves_x_center_remap(self, context):
        # Layer to the right of source center should stay right-side
        # under "top" gravity (top constrains Y only).
        ctx = dict(context)
        ctx["src_pos"] = [1500.0, 540.0, 0.0]   # 540 right of source center
        rule = _rule("top")
        p, _ = apply_gravity(
            rule, ctx["src_pos"], ctx["src_center"], ctx["tgt_size"],
            ctx["safe_area"], base_scale=0.5625, fill_scale=1.0,
        )
        # X should be remapped: (1500 - 960) * 0.5625 + 540 ≈ 843.75
        assert p[0] > 540.0, "rightward layer should remain right-of-center"

    def test_centerH_overrides_x_off_center(self, context):
        # centerH ignores source X — forces to safe-area horizontal center.
        ctx = dict(context)
        ctx["src_pos"] = [1500.0, 540.0, 0.0]
        rule = _rule("centerH")
        p, _ = apply_gravity(
            rule, ctx["src_pos"], ctx["src_center"], ctx["tgt_size"],
            ctx["safe_area"], base_scale=0.5625, fill_scale=1.0,
        )
        assert abs(p[0] - 540.0) < 1.0


# ── ScaleEngine integration ────────────────────────────────────────


class TestScaleEngineProfileWiring:
    """The engine accepts a profile and the gravity_applied list grows
    when tagged root layers route through apply_gravity."""

    def _build(self, profile=None):
        from models.scrape_manifest import ScrapeManifest
        from core.scale_engine import ScaleEngine
        manifest = ScrapeManifest.model_validate({
            "status": "OK",
            "project_info": {
                "name": "TestComp",
                "width": 1920, "height": 1080, "fps": 24.0,
                "duration": 10.0,
            },
            "layers": [
                # Root layer with TITLE_ prefix — should be picked up by
                # any profile with TITLE_ rule (default, social, etc).
                {
                    "index": 1, "name": "TITLE_MAIN", "uid": "u1",
                    "parent_index": -1, "layer_kind": "av",
                    "transform": {
                        "position": [960.0, 540.0],
                        "scale": [100.0, 100.0],
                        "rotation": 0.0, "anchor": [0.0, 0.0],
                    },
                },
            ],
        })
        return ScaleEngine(manifest, 1080, 1920, "Fit", 0.0,
                           profile=profile)

    def test_engine_accepts_profile_kwarg(self):
        from logic.studio_profile_registry import StudioProfileRegistry
        from pathlib import Path
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp(prefix="dim_se_grav_"))
        try:
            reg = StudioProfileRegistry(
                user_dir=tmp / "profiles",
                baselines_dir=Path(__file__).resolve().parents[2]
                / "config" / "profiles",
            )
            # Slot 11.5 Phase 2 — `default` is the new master baseline
            # in the 9→4 reduction. Carries TITLE_ → TT/center.
            profile = reg.get("default")
            engine = self._build(profile=profile)
            assert engine.profile is profile
            engine.conform()
            assert "TITLE_MAIN" in engine.gravity_applied
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_engine_no_profile_no_gravity(self):
        engine = self._build(profile=None)
        engine.conform()
        assert engine.gravity_applied == []


# ── v5.5 Tier A: BASELINE_RULES per-tag fallback ────────────────────


class TestBaselineRules:
    """v5.5 Tier A — every recognised tag must have a baseline rule
    so heuristic-classified layers route through apply_gravity even
    without a studio profile match. Closes the field-test gap where
    BG plates letterboxed instead of filling on TIKTOK conforms.
    """

    def test_studio_profile_vocab_has_rules(self):
        from core.gravity import baseline_rule_for
        # v6.0 canonical 4-tag vocabulary has baselines.
        for tag in ("FILL", "CENTER", "TOP", "BOTTOM", "DISC"):
            rule = baseline_rule_for(tag)
            assert rule is not None, f"{tag} has no baseline rule"
            assert rule.tag == tag
        # Legacy tags still have baselines for backward compat.
        for tag in ("TT", "HERO", "LGL", "BG"):
            rule = baseline_rule_for(tag)
            assert rule is not None, f"{tag} has no baseline rule"

    def test_heuristic_vocab_has_rules(self):
        """Surveyor heuristic emits these tags — they MUST have
        baselines or the field-test bug repeats."""
        from core.gravity import baseline_rule_for
        for tag in ("TYPE", "ARTWORK", "ANIMATION", "BACKGROUND",
                    "BOXART", "KEYART", "LEGALS"):
            rule = baseline_rule_for(tag)
            assert rule is not None, f"{tag} has no baseline rule"

    def test_hero_centres(self):
        from core.gravity import baseline_rule_for
        # v6.0: CENTER is the canonical tag (HERO is a legacy alias in gravity)
        rule = baseline_rule_for("CENTER")
        assert rule.gravity == "center"
        assert rule.scale == 1.0
        # Legacy HERO still works
        rule2 = baseline_rule_for("HERO")
        assert rule2.gravity == "center"

    def test_bg_fills(self):
        """The screenshot bug: FILL must be `fill`, not `center`."""
        from core.gravity import baseline_rule_for
        rule = baseline_rule_for("FILL")
        assert rule.gravity == "fill"
        # Legacy BG still works
        rule2 = baseline_rule_for("BG")
        assert rule2.gravity == "fill"

    def test_background_alias_fills(self):
        """Heuristic emits BACKGROUND, profile emits FILL. Both fill."""
        from core.gravity import baseline_rule_for
        assert baseline_rule_for("BACKGROUND").gravity == "fill"
        assert baseline_rule_for("FILL").gravity == "fill"

    def test_legals_pin_bottom_with_shrink(self):
        from core.gravity import baseline_rule_for
        # v6.0: BOTTOM is the canonical bottom-pin tag
        rule = baseline_rule_for("BOTTOM")
        assert rule.gravity == "bottom"
        assert rule.scale == 0.85
        # Legacy LGL still works
        rule2 = baseline_rule_for("LGL")
        assert rule2.gravity == "bottom"
        assert rule2.scale == 0.85
        # Heuristic LEGALS alias.
        rule3 = baseline_rule_for("LEGALS")
        assert rule3.gravity == "bottom"
        assert rule3.scale == 0.85

    def test_guide_has_no_baseline(self):
        """GUIDE is structural — never moves; absence is the contract."""
        from core.gravity import baseline_rule_for
        assert baseline_rule_for("GUIDE") is None

    def test_unknown_tag_returns_none(self):
        from core.gravity import baseline_rule_for
        assert baseline_rule_for("DOES_NOT_EXIST") is None
        assert baseline_rule_for(None) is None
        assert baseline_rule_for("") is None

    def test_baseline_case_insensitive(self):
        """Surveyor sometimes lowercases tags — baseline shouldn't care."""
        from core.gravity import baseline_rule_for
        assert baseline_rule_for("hero") is not None
        assert baseline_rule_for("HERO") is not None
        assert baseline_rule_for("Hero") is not None

    def test_baseline_rule_duck_types_profile_rule(self):
        """The baseline rule must work everywhere ProfileRule does."""
        from core.gravity import baseline_rule_for
        rule = baseline_rule_for("CENTER")
        # Must have the four ProfileRule attributes apply_gravity reads.
        assert hasattr(rule, "tag")
        assert hasattr(rule, "gravity")
        assert hasattr(rule, "scale")
        assert hasattr(rule, "weight")


class TestScaleEngineBaselineFallback:
    """v5.5 Tier A — ScaleEngine routes heuristic-tagged layers
    through apply_gravity when no profile matches. Closes the field-
    test gap where the 87N TIKTOK conform letterboxed BG plates."""

    def _engine(self, profile, content_tag="HERO", layer_name="GenericLayer"):
        """Build a 1-layer ScaleEngine. Source 1920×1080 → 1080×1920."""
        from core.scale_engine import ScaleEngine
        from models.scrape_manifest import ScrapeManifest
        manifest = ScrapeManifest.model_validate({
            "status": "OK",
            "project_info": {
                "name": "T", "width": 1920, "height": 1080, "fps": 24.0,
                "duration": 1.0,
            },
            "layers": [
                {
                    "uid": "u-1",
                    "name": layer_name,
                    "index": 1,
                    "layer_kind": "av",
                    "match_name": "ADBE Vector Layer",
                    "content_tag": content_tag,
                    "content_tag_source": "heuristic",
                    "content_tag_confidence": 0.7,
                    "transforms": {
                        "raw": {
                            "position": [960.0, 540.0],
                            "scale":    [100.0, 100.0],
                            "anchor_point": [50.0, 50.0],
                        },
                    },
                },
            ],
        })
        return ScaleEngine(manifest, 1080, 1920, "Fit", 0.0,
                           profile=profile)

    def test_heuristic_hero_routes_through_gravity(self):
        """Profile is None, layer tagged CENTER. Baseline rule must fire
        — the layer must end up in `gravity_applied`. Closes the v5.4
        gap where heuristic-tagged layers got Fit-fallback math
        instead of gravity routing. v6.0: HERO→CENTER."""
        engine = self._engine(profile=None, content_tag="CENTER",
                              layer_name="GenericLayer")
        engine.conform()
        assert "GenericLayer" in engine.gravity_applied, (
            "CENTER layer must route through apply_gravity via baseline "
            "even when no studio profile matches the name"
        )

    def test_heuristic_background_routes_through_gravity(self):
        """The screenshot bug: FILL plate routes through gravity now
        (baseline rule has gravity=fill). Pre-v5.5 the layer fell
        through to Fit math and letterboxed. v6.0: BACKGROUND→FILL."""
        engine = self._engine(profile=None, content_tag="BACKGROUND",
                              layer_name="Contrast_BW_Grunge_4.jpg")
        engine.conform()
        assert "Contrast_BW_Grunge_4.jpg" in engine.gravity_applied, (
            "BACKGROUND layer must route through apply_gravity via "
            "baseline (gravity=fill) — was Fit-letterboxing in v5.4"
        )

    def test_profile_match_still_wins_over_baseline(self):
        """Baseline must NOT override a profile prefix match. Profile
        tag wins and uses profile.safe_area + type_overrides."""
        from logic.studio_profile_registry import StudioProfileRegistry
        from pathlib import Path
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp(prefix="dim_baseline_"))
        try:
            reg = StudioProfileRegistry(
                user_dir=tmp / "profiles",
                baselines_dir=Path(__file__).resolve().parents[2]
                / "config" / "profiles",
            )
            # v6.0 — `default` carries the HERO_ prefix
            # rule (CENTER/center) in the new four-profile baseline.
            profile = reg.get("default")
            engine = self._engine(profile=profile, content_tag="CENTER",
                                  layer_name="HERO_PRODUCT")
            engine.conform()
            assert "HERO_PRODUCT" in engine.gravity_applied
            # HERO_PRODUCT matches `default`'s "HERO_" prefix → profile.
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── v5.5 Tier B: Filename-pattern + coverage signals ────────────────


class TestFilenameSignals:
    """v5.5 Tier B — filename-pattern boosts catch evidence the
    structural lexicon misses (`87N.mov` → ANIMATION; `Grunge.jpg`
    → BACKGROUND; `Outlines` → TYPE; etc.)."""

    def test_video_extension_detected(self):
        from core.surveyor import _filename_signals
        for name in ("hero.mov", "loop.mp4", "anim.m4v", "intro.MOV"):
            sig = _filename_signals(name.lower())
            assert sig["is_video"], f"{name} should be detected as video"

    def test_image_extension_detected(self):
        from core.surveyor import _filename_signals
        for name in ("plate.jpg", "logo.png", "shot.tif", "key.psd"):
            sig = _filename_signals(name.lower())
            assert sig["is_image"], f"{name} should be detected as image"

    def test_grunge_pattern_routes_to_background(self):
        from core.surveyor import _filename_signals
        sig = _filename_signals("contrast_bw_grunge_4.jpg")
        # v6.0 — _FILENAME_PATTERNS migrated to 4-tag canonical (FILL, was BG).
        assert sig["pattern_tag"] == "FILL"
        assert sig["pattern_boost"] >= 0.30

    def test_outline_pattern_routes_to_type(self):
        from core.surveyor import _filename_signals
        sig = _filename_signals("everything outlines")
        # v6.0 — TOP, was TT.
        assert sig["pattern_tag"] == "TOP"

    def test_logo_pattern_routes_to_boxart(self):
        from core.surveyor import _filename_signals
        sig = _filename_signals("logo_lockup_v3")
        # v6.0 — CENTER, was BOXART.
        assert sig["pattern_tag"] == "CENTER"

    def test_legal_pattern_routes_to_legals(self):
        from core.surveyor import _filename_signals
        sig = _filename_signals("rating_disclaimer_us")
        # Either "rating" or "disclaimer" hits BOTTOM — both are listed.
        # v6.0 — BOTTOM, was LGL.
        assert sig["pattern_tag"] == "BOTTOM"

    def test_unknown_name_returns_no_pattern(self):
        from core.surveyor import _filename_signals
        sig = _filename_signals("randomblob")
        assert sig["pattern_tag"] is None
        assert sig["pattern_boost"] == 0.0


class TestSurveyorTierB:
    """End-to-end: feed `_score_layer` synthetic layers and assert the
    surveyor classifies them correctly with the v5.5 boosts active."""

    def _layer(self, name, source_rect=None, **flag_kwargs):
        from models.scrape_manifest import LayerModel
        d = {
            "uid": "u-x", "name": name, "index": 1,
            "layer_kind": "av", "match_name": "ADBE Vector Layer",
            "transforms": {
                "raw": {
                    "position": [0, 0], "scale": [100, 100],
                    "anchor_point": [50, 50],
                },
            },
        }
        if source_rect is not None:
            d["source_rect"] = source_rect
        if flag_kwargs:
            d["flags"] = flag_kwargs
        return LayerModel.model_validate(d)

    def test_video_only_falls_back_to_animation(self):
        """`*.mov` with no other signal → CENTER. v6.0: was ANIMATION."""
        from core.surveyor import _score_layer
        layer = self._layer("87N.mov")
        result = _score_layer(layer, layer_index=1, total_layers=5)
        assert result.tag == "CENTER"

    def test_grunge_image_classifies_background(self):
        from core.surveyor import _score_layer
        layer = self._layer("Contrast_BW_Grunge_4.jpg")
        result = _score_layer(layer, layer_index=4, total_layers=5)
        # v6.0 — surveyor emits canonical (FILL, was BG).
        assert result.tag == "FILL"

    def test_outline_classifies_type(self):
        from core.surveyor import _score_layer
        layer = self._layer("EVERYTHING Outlines")
        result = _score_layer(layer, layer_index=2, total_layers=10)
        # v6.0 — TOP, was TT.
        assert result.tag == "TOP"

    def test_coverage_promotes_to_background(self):
        """Layer covering ≥70% on both axes via world_bounds → FILL.
        world_bounds is a dict {top,right,bottom,left} in comp space.
        v6.0: was BG."""
        from core.surveyor import _score_layer
        from models.scrape_manifest import LayerModel
        d = {
            "uid": "u-x", "name": "Backdrop", "index": 5,
            "layer_kind": "av", "match_name": "ADBE Vector Layer",
            "transforms": {
                "raw": {
                    "position": [960, 540], "scale": [100, 100],
                    "anchor_point": [50, 50],
                },
            },
            # Schema uses single-letter keys (see LayerModel validator).
            "world_bounds": {"l": 0.0, "t": 0.0, "r": 1920.0, "b": 1080.0},
        }
        layer = LayerModel.model_validate(d)
        result = _score_layer(layer, layer_index=5, total_layers=5,
                              comp_width=1920, comp_height=1080)
        # v6.0 — FILL, was BG.
        assert result.tag == "FILL"

    def test_logo_lockup_classifies_as_logo_class(self):
        """Logo lockups should classify as CENTER (was HERO or BOXART
        pre-v6.0). v6.0: all graphic content merges into CENTER."""
        from core.surveyor import _score_layer
        layer = self._layer("Studio_Logo_Lockup_v2")
        result = _score_layer(layer, layer_index=3, total_layers=10)
        assert result.tag == "CENTER", (
            f"logo lockup should classify as CENTER, got {result.tag}"
        )


# ── Group-aware gravity (v1.0.x patch for multi-layer same-tag stacks) ──


class TestGroupAwareGravity:
    """Regression coverage for the group-aware gravity patch.
    Promoted from v1.5 compositional roadmap to immediate v1.0.x fix
    after the 5-line CENTER stack collapse was observed in client work.

    Core contract:
    - group of 1 (or no centroid passed) produces byte-identical output
      to legacy single-layer pinning.
    - N>1: group's centroid is moved to the gravity anchor; each member's
      relative offset from centroid (in source) is scaled and added back.
    - Keyed by (containing_comp_id, tag) so precomps are isolated.
    """

    def test_single_layer_matches_legacy_when_centroid_omitted_or_self(self):
        """N=1 must be identical to pre-patch behavior."""
        ctx = {
            "src_pos": [960.0, 540.0, 0.0],
            "src_center": (960.0, 540.0),
            "tgt_size": (1080, 1920),
            "safe_area": SafeArea(top=0.05, right=0.05, bottom=0.05, left=0.05),
            "base_scale": 0.5625,
            "fill_scale": 1.0,
            "scale_z": False,
        }
        rule = _rule("center")

        p1, m1 = apply_gravity(
            rule, ctx["src_pos"], ctx["src_center"], ctx["tgt_size"],
            ctx["safe_area"], base_scale=ctx["base_scale"],
            fill_scale=ctx["fill_scale"], scale_z=False,
            # legacy path: no group_centroid
        )
        p2, m2 = apply_gravity(
            rule, ctx["src_pos"], ctx["src_center"], ctx["tgt_size"],
            ctx["safe_area"], base_scale=ctx["base_scale"],
            fill_scale=ctx["fill_scale"], scale_z=False,
            group_centroid=(960.0, 540.0),  # same as self
        )
        assert abs(p1[0] - p2[0]) < 1e-9 and abs(p1[1] - p2[1]) < 1e-9
        assert abs(m1 - m2) < 1e-9
        # For CENTER on centered layer → safe center
        assert abs(p1[0] - 540.0) < 1e-6
        assert abs(p1[1] - 960.0) < 1e-6

    def test_multi_layer_center_stack_preserves_relative_spacing(self):
        """Classic 5-line CENTER stack (e.g. synced text reveal).
        Source positions vertically spaced; after gravity the deltas
        must be S-scaled versions of source deltas from group centroid.
        """
        # 5 lines in HD, vertically stacked around center, 80px apart
        src_positions = [
            [960.0, 300.0],
            [960.0, 380.0],
            [960.0, 460.0],
            [960.0, 540.0],
            [960.0, 620.0],
        ]
        # Centroid Y = 460
        group_cy = 460.0
        group_cx = 960.0

        ctx = {
            "src_center": (960.0, 540.0),
            "tgt_size": (1080, 1920),
            "safe_area": SafeArea(top=0.05, right=0.05, bottom=0.05, left=0.05),
            "base_scale": 0.5625,
            "fill_scale": 1.0,
        }
        rule = _rule("center")

        results = []
        for pos in src_positions:
            p, _ = apply_gravity(
                rule,
                [pos[0], pos[1], 0.0],
                ctx["src_center"],
                ctx["tgt_size"],
                ctx["safe_area"],
                base_scale=ctx["base_scale"],
                fill_scale=ctx["fill_scale"],
                group_centroid=(group_cx, group_cy),
            )
            results.append(p)

        S = ctx["base_scale"]
        safe_cy = (0.05 + (1 - 0.05 - 0.05) / 2) * 1920   # 0.5 for symmetric

        # The group's centroid should land at safe center
        # First member's delta from group in source: 300 - 460 = -160
        expected_y0 = safe_cy + (-160.0) * S
        assert abs(results[0][1] - expected_y0) < 0.1

        # Spacing between consecutive members must be 80 * S
        expected_dy = 80.0 * S
        for i in range(1, 5):
            observed_dy = results[i][1] - results[i-1][1]
            assert abs(observed_dy - expected_dy) < 0.1, \
                f"Line {i} spacing {observed_dy} != expected {expected_dy}"

        # All X should be safe_cx (since group cx was at source center)
        safe_cx = (0.05 + (1 - 0.05 - 0.05) / 2) * 1080
        for r in results:
            assert abs(r[0] - safe_cx) < 0.1

    def test_mixed_tags_same_comp_are_independent_groups(self):
        """A CENTER stack and a TOP stack in the same comp must each
        form their own group; tags do not bleed into each other.
        """
        # One TOP layer and two CENTER layers
        ctx = {
            "src_center": (960.0, 540.0),
            "tgt_size": (1080, 1920),
            "safe_area": SafeArea(top=0.05, right=0.05, bottom=0.05, left=0.05),
            "base_scale": 0.5625,
            "fill_scale": 1.0,
        }

        # TOP group of 1 at y=200
        p_top, _ = apply_gravity(
            _rule("top"), [960.0, 200.0, 0], ctx["src_center"],
            ctx["tgt_size"], ctx["safe_area"], 0.5625, 1.0,
            group_centroid=(960.0, 200.0)
        )

        # CENTER group of 2
        p_c1, _ = apply_gravity(
            _rule("center"), [960.0, 400.0, 0], ctx["src_center"],
            ctx["tgt_size"], ctx["safe_area"], 0.5625, 1.0,
            group_centroid=(960.0, 420.0)
        )
        p_c2, _ = apply_gravity(
            _rule("center"), [960.0, 440.0, 0], ctx["src_center"],
            ctx["tgt_size"], ctx["safe_area"], 0.5625, 1.0,
            group_centroid=(960.0, 420.0)
        )

        # TOP should be high (top gravity ~ top of safe + its offset)
        assert p_top[1] < 1920 * 0.30
        # CENTERs should be near middle
        assert 1920 * 0.45 < p_c1[1] < 1920 * 0.55
        assert 1920 * 0.45 < p_c2[1] < 1920 * 0.55
        # The two CENTERs preserve their 40px source delta (scaled)
        assert abs((p_c2[1] - p_c1[1]) - (40 * 0.5625)) < 0.5

    def test_different_comps_independent_groups(self):
        """containing_comp_id scoping: same tag in two different comps
        are separate groups (precomp isolation).
        """
        ctx = {
            "src_center": (960.0, 540.0),
            "tgt_size": (1080, 1920),
            "safe_area": SafeArea(top=0.05, right=0.05, bottom=0.05, left=0.05),
            "base_scale": 0.5625,
            "fill_scale": 1.0,
        }
        rule = _rule("center")

        # Comp A: two layers
        pa1, _ = apply_gravity(rule, [960, 300, 0], ctx["src_center"], ctx["tgt_size"], ctx["safe_area"], 0.5625, 1.0,
                               group_centroid=(960, 340))
        pa2, _ = apply_gravity(rule, [960, 380, 0], ctx["src_center"], ctx["tgt_size"], ctx["safe_area"], 0.5625, 1.0,
                               group_centroid=(960, 340))

        # Comp B (different containing_comp_id): its own group centroid
        pb, _ = apply_gravity(rule, [960, 500, 0], ctx["src_center"], ctx["tgt_size"], ctx["safe_area"], 0.5625, 1.0,
                              group_centroid=(960, 500))  # alone in its comp

        # In A the members are 80px apart source → scaled delta
        assert abs((pa2[1] - pa1[1]) - 80 * 0.5625) < 0.5
        # B should be treated as its own singleton (pinned, no extra offset)
        # just sanity that it didn't use A's centroid
        assert abs(pb[1] - 960.0) < 30   # near safe center since its own centroid was used


class TestVerticalTextStackZSpread:
    """Hammer test for audit fix: vertical targets + z-depth text stacks
    (same xy, different z like 87N outlines) must spread in y when using
    group centroid + z logic. Prevents bunching at same dst_y."""

    def test_flat_y_z_stack_spreads_on_tall_target(self):
        """Layers with near-identical y but varying z, tall target, top gravity:
        must get distinct dst_y using z contribution."""
        from core.gravity import apply_gravity
        import types

        rule = types.SimpleNamespace(gravity="top", tag="TT")
        safe = types.SimpleNamespace(top=0.05, right=0.04, bottom=0.06, left=0.04)
        src_c = (960., 540.)
        tgt = (1080, 1920)  # tall
        S = 0.8
        centroid = (960, 533.9)

        cases = [
            [960, 533.9, 800.0],
            [960, 533.9, 721.4],
            [960, 533.9, 407.0],
            [960, 533.9, -1764.0],
        ]
        ys = []
        for pos in cases:
            p, _ = apply_gravity(rule, pos, src_c, tgt, safe, S, 1.0, group_centroid=centroid)
            ys.append(p[1])

        spreads = [abs(ys[i] - ys[j]) for i in range(len(ys)) for j in range(i+1, len(ys))]
        assert max(spreads) > 100, f"Expected >100px spread for z-stack, got spreads {spreads}, ys={ys}"
        assert len(set(round(y, 1) for y in ys)) >= 3, "At least 3 distinct y positions for 4 z layers"

    def test_non_flat_y_no_extra_z_pollution(self):
        """If source y varies meaningfully, z-spread should not be applied (compat).
        Use shared centroid so offsets reflect y delta; z should not extra-pollute."""
        from core.gravity import apply_gravity
        import types
        rule = types.SimpleNamespace(gravity="top", tag="TT")
        safe = types.SimpleNamespace(top=0.05, right=0.04, bottom=0.06, left=0.04)
        src_c = (960., 540.)
        tgt = (1080, 1920)
        S = 0.8
        centroid = (960, 450)  # shared
        p1, _ = apply_gravity(rule, [960, 400, 100], src_c, tgt, safe, S, 1.0, group_centroid=centroid)
        p2, _ = apply_gravity(rule, [960, 500, 100], src_c, tgt, safe, S, 1.0, group_centroid=centroid)
        delta = abs(p2[1] - p1[1])
        assert delta >= 80, f"Expected y delta from source ~100* S preserved, got {delta}"
        # no extra from z since non-flat condition not met for spread code
        assert delta < 120, "No excessive z addition on non-flat y"

