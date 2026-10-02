# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_fixes_1_3_4.py — Regression tests for Fixes 1, 3, and 4.

Fix 1  — surveyor.py pre-pass for adjustment layers:
         Adjustment layers bypass manual tags. A stale AE label color
         (e.g. BOXART) on an adjustment layer must NOT survive Pass 1.
         The pre-pass fires before classify_layer's Pass 1 check.

Fix 3  — heuristics.json BG keyword expansion:
         "efx", "fx", "noise", "vignette", "overlay" etc. moved from
         ANIMATION keywords (or added new) to BG keywords. A layer
         named "efx_layer" should classify as BG, not ANIMATION.

Fix 4  — OVERLAY tag in registry, gravity, and scale_engine:
         OVERLAY is a valid canonical tag (in all_ids()), normalizes
         to itself, appears in BASELINE_RULES with center gravity, and
         scale_engine applies S (not fill_S) when the tag is OVERLAY.
"""

from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.scrape_manifest import LayerModel, LayerFlags, ScrapeManifest, ProjectInfo
from core.surveyor import classify_layer


# ── helpers ──────────────────────────────────────────────────────────────────

def _adj_layer(name: str, blend_mode: str = "5212",
               content_tag: str | None = None,
               content_tag_source: str | None = None) -> LayerModel:
    """Build an adjustment-layer LayerModel. blend_mode is an AE integer string."""
    return LayerModel(
        uid=f"u-{name}",
        name=name,
        index=1,
        layer_kind="av",
        content_tag=content_tag,
        content_tag_source=content_tag_source,
        flags=LayerFlags(adjustment=True, blend_mode=blend_mode),
    )


def _plain_layer(name: str) -> LayerModel:
    """Build a plain AV LayerModel with no flags."""
    return LayerModel(
        uid=f"u-{name}",
        name=name,
        index=3,
        layer_kind="av",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 1 — Adjustment layer pre-pass
# ═══════════════════════════════════════════════════════════════════════════════

class TestFix1AdjustmentPrePass:
    """Pre-pass fires before Pass 1 (manual tag) so stale manual
    tags on adjustment layers cannot override the structural rule."""

    def test_adj_with_stale_boxart_manual_label_returns_bg(self):
        """Adjustment layer with content_tag='BOXART' (manual_label) → FILL.
        The pre-pass overrides the manual tag before Pass 1 can return it.
        v6.0: BG is now FILL."""
        layer = _adj_layer(
            "Adjustment Layer 1",
            blend_mode="5212",   # normal — not a compositing blend
            content_tag="BOXART",
            content_tag_source="manual_label",
        )
        result = classify_layer(layer, 1, 5)
        assert result.tag == "FILL", f"Expected FILL, got {result.tag!r}"
        assert result.source == "heuristic"
        assert result.confidence >= 0.97
        assert "pre-pass" in result.reason.lower() or "adjustment" in result.reason.lower()

    def test_adj_with_screen_blend_returns_overlay(self):
        """Adjustment layer with Screen blend (5222) → OVERLAY, conf 0.90.
        Screen blend on an adjustment = FX compositing overlay (grain, grade)."""
        layer = _adj_layer(
            "Grain Adjustment",
            blend_mode="5222",   # screen
        )
        result = classify_layer(layer, 1, 5)
        assert result.tag == "OVERLAY", f"Expected OVERLAY, got {result.tag!r}"
        assert result.source == "heuristic"
        assert abs(result.confidence - 0.90) < 0.01

    def test_adj_with_add_blend_returns_overlay(self):
        """Add blend (5220) on an adjustment layer → OVERLAY (Add is in
        _COMPOSITING_BLEND_MODES)."""
        layer = _adj_layer("Light Leak Adj", blend_mode="5220")  # add
        result = classify_layer(layer, 1, 5)
        assert result.tag == "OVERLAY"

    def test_adj_with_multiply_blend_returns_overlay(self):
        """Multiply blend (5216) on adjustment layer → OVERLAY."""
        layer = _adj_layer("Shadow Adj", blend_mode="5216")  # multiply
        result = classify_layer(layer, 1, 5)
        assert result.tag == "OVERLAY"

    def test_adj_normal_blend_no_manual_tag_returns_bg(self):
        """Adjustment layer with normal blend, no prior tag → FILL 0.97.
        v6.0: BG is now FILL."""
        layer = _adj_layer("Color Correction Adj", blend_mode="5212")  # normal
        result = classify_layer(layer, 1, 5)
        assert result.tag == "FILL"
        assert result.source == "heuristic"

    def test_adj_pre_pass_fires_before_profile(self):
        """Pre-pass should return before any profile check.
        Pass 2 (profile) runs after Pass 1 but the pre-pass is before both.
        v6.0: BG is now FILL."""
        layer = _adj_layer(
            "Adj Layer With Tag",
            content_tag="TT",
            content_tag_source="manual_comment",
        )
        # Regardless of manual_comment tag, adjustment layer → FILL/OVERLAY
        result = classify_layer(layer, 1, 5)
        assert result.tag in ("FILL", "OVERLAY"), \
            f"Pre-pass should override manual_comment, got {result.tag!r}"

    def test_non_adjustment_layer_not_caught_by_pre_pass(self):
        """A regular layer with BOXART manual_label must NOT be overridden.
        The pre-pass is only for adjustment=True layers."""
        layer = LayerModel(
            uid="u-normal",
            name="Package Shot",
            index=1,
            layer_kind="av",
            content_tag="BOXART",
            content_tag_source="manual_label",
            flags=LayerFlags(adjustment=False),
        )
        result = classify_layer(layer, 1, 5)
        assert result.tag == "BOXART", \
            f"Non-adjustment layer must keep manual_label, got {result.tag!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 3 — BG keyword expansion
# ═══════════════════════════════════════════════════════════════════════════════

class TestFix3BGKeywords:
    """Layers with FX/compositing names must classify as FILL, not CENTER.
    v6.0: BG is now FILL, ANIMATION is now CENTER."""

    def test_efx_layer_name_gives_bg(self):
        """Layer named 'efx_layer' → FILL (efx in FILL keywords). v6.0: was BG."""
        layer = _plain_layer("efx_layer")
        result = classify_layer(layer, 3, 5)
        assert result.tag == "FILL", f"Expected FILL for 'efx_layer', got {result.tag!r}"

    def test_fx_prefix_layer_gives_bg(self):
        """Layer named 'fx_layer' → FILL (fx in FILL keywords). v6.0: was BG."""
        layer = _plain_layer("fx_layer")
        result = classify_layer(layer, 3, 5)
        assert result.tag == "FILL", f"Expected FILL for 'fx_layer', got {result.tag!r}"

    def test_noise_overlay_gives_bg(self):
        """Layer named 'noise_overlay' → FILL (noise in FILL keywords). v6.0: was BG."""
        layer = _plain_layer("noise_overlay")
        result = classify_layer(layer, 3, 5)
        assert result.tag == "FILL", f"Expected FILL for 'noise_overlay', got {result.tag!r}"

    def test_vignette_gives_bg(self):
        """Layer named 'vignette' → FILL (vignette in FILL keywords). v6.0: was BG."""
        layer = _plain_layer("vignette")
        result = classify_layer(layer, 3, 5)
        assert result.tag == "FILL", f"Expected FILL for 'vignette', got {result.tag!r}"

    def test_color_grade_gives_bg(self):
        """Layer named 'color_grade_01' → FILL. v6.0: was BG."""
        layer = _plain_layer("color_grade_01")
        result = classify_layer(layer, 3, 5)
        assert result.tag == "FILL", f"Expected FILL for 'color_grade_01', got {result.tag!r}"

    def test_effect_gives_bg(self):
        """Layer named 'effect_layer' → FILL. v6.0: was BG."""
        layer = _plain_layer("effect_layer")
        result = classify_layer(layer, 3, 5)
        assert result.tag == "FILL", f"Expected FILL for 'effect_layer', got {result.tag!r}"

    def test_exposure_layer_gives_bg(self):
        """Layer named 'exposure_comp' → FILL. v6.0: was BG."""
        layer = _plain_layer("exposure_comp")
        result = classify_layer(layer, 3, 5)
        assert result.tag == "FILL", f"Expected FILL for 'exposure_comp', got {result.tag!r}"

    def test_animation_keyword_still_gives_animation(self):
        """Ensure moving fx/efx/noise didn't break the CENTER category.
        A layer named 'animation_loop' should still be CENTER. v6.0: ANIMATION→CENTER."""
        layer = _plain_layer("animation_loop")
        result = classify_layer(layer, 3, 5)
        assert result.tag == "CENTER", \
            f"Expected CENTER for 'animation_loop', got {result.tag!r}"

    def test_vfx_keyword_remains_animation(self):
        """vfx was NOT moved to FILL — should still route to CENTER. v6.0: ANIMATION→CENTER."""
        layer = _plain_layer("vfx_element")
        result = classify_layer(layer, 3, 5)
        assert result.tag == "CENTER", \
            f"Expected CENTER for 'vfx_element', got {result.tag!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# Fix 4 — OVERLAY tag in registry, gravity, and scale_engine
# ═══════════════════════════════════════════════════════════════════════════════

class TestFix4OverlayTag:
    """OVERLAY tag must exist in the registry, normalize correctly, appear
    in BASELINE_RULES with center gravity, and be handled in scale_engine
    with S (not fill_S)."""

    def test_overlay_in_registry_all_ids(self):
        """OVERLAY must be in the canonical tag registry."""
        from core.tag_registry import REGISTRY
        assert "OVERLAY" in REGISTRY.all_ids(), \
            "OVERLAY must be listed in tag_registry.yaml"

    def test_overlay_normalizes_to_itself(self):
        """REGISTRY.normalize('OVERLAY') must return 'OVERLAY'."""
        from core.tag_registry import REGISTRY
        assert REGISTRY.normalize("OVERLAY") == "OVERLAY"

    def test_overlay_in_valid_tags(self):
        """OVERLAY must be in surveyor.VALID_TAGS."""
        from core.surveyor import VALID_TAGS
        assert "OVERLAY" in VALID_TAGS

    def test_overlay_in_baseline_rules(self):
        """OVERLAY must have a BASELINE_RULES entry in gravity.py."""
        from core.gravity import BASELINE_RULES
        assert "OVERLAY" in BASELINE_RULES, \
            "OVERLAY missing from BASELINE_RULES"

    def test_overlay_baseline_gravity_is_center(self):
        """OVERLAY baseline gravity must be 'center' (not 'fill')."""
        from core.gravity import BASELINE_RULES
        rule = BASELINE_RULES["OVERLAY"]
        assert rule.gravity == "center", \
            f"OVERLAY gravity expected 'center', got {rule.gravity!r}"

    def test_overlay_scale_engine_uses_S_not_fill_S(self):
        """Root OVERLAY layer must be scaled by S, not fill_S.

        Test setup: 16:9 (1920×1080) → 9:16 (1080×1920) TikTok conform.
        S  = min(1080/1920, 1920/1080) = 0.5625
        fill_S = max(1080/1920, 1920/1080) = 1.7778

        An OVERLAY layer at comp center (960, 540) with scale (100, 100)
        should land at target center (540, 960) with scale ~56.25%, NOT
        ~177.78%.
        """
        from core.scale_engine import ScaleEngine

        src_w, src_h = 1920, 1080
        tgt_w, tgt_h = 1080, 1920

        layers = [
            LayerModel(
                uid="u-overlay-1",
                name="Grain Overlay",
                index=1,
                layer_kind="av",
                parent_index=-1,
                position=[float(src_w / 2), float(src_h / 2), 0.0],
                scale=[100.0, 100.0, 100.0],
                anchor_point=[0.0, 0.0, 0.0],
                content_tag="OVERLAY",
                content_tag_source="heuristic",
                content_tag_confidence=0.90,
            )
        ]
        manifest = ScrapeManifest(
            status="OK",
            project_info=ProjectInfo(
                width=src_w, height=src_h, frame_rate=23.976, duration=10.0,
                name="Test",
            ),
            layers=layers,
        )

        engine = ScaleEngine(
            manifest=manifest,
            target_width=tgt_w,
            target_height=tgt_h,
            scale_mode="fit",
            bleed_pct=0.0,
        )
        result = engine.conform()

        conformed_layers = result["layers"]
        assert len(conformed_layers) == 1
        ct = conformed_layers[0]["conformed_transforms"]

        S = min(tgt_w / src_w, tgt_h / src_h)          # 0.5625
        fill_S = max(tgt_w / src_w, tgt_h / src_h)     # 1.7778

        sx = ct["scale"][0]
        # Should be S * 100 (≈56.25), definitely NOT fill_S * 100 (≈177.78)
        assert abs(sx - S * 100) < 0.5, \
            f"OVERLAY scale should use S ({S:.4f}×100={S*100:.2f}), " \
            f"got {sx:.2f} (fill_S×100 would be {fill_S*100:.2f})"

    def test_overlay_jsx_mirror_contains_overlay(self):
        """The generated JSX mirror must include OVERLAY."""
        jsx_path = os.path.join(
            os.path.dirname(__file__),
            "..", "..", "Scripts", "Dimension_Assets", "tag_registry.jsx"
        )
        jsx_path = os.path.normpath(jsx_path)
        assert os.path.isfile(jsx_path), f"JSX mirror not found at {jsx_path}"
        content = open(jsx_path, encoding="utf-8").read()
        assert "OVERLAY" in content, "OVERLAY missing from JSX tag mirror"

    def test_overlay_js_mirror_contains_overlay(self):
        """The generated JS mirror must include OVERLAY."""
        js_path = os.path.join(
            os.path.dirname(__file__),
            "..", "..", "cep", "js", "tag_registry.js"
        )
        js_path = os.path.normpath(js_path)
        assert os.path.isfile(js_path), f"JS mirror not found at {js_path}"
        content = open(js_path, encoding="utf-8").read()
        assert "OVERLAY" in content, "OVERLAY missing from JS tag mirror"
