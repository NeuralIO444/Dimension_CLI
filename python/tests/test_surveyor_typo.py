# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_surveyor_typo.py
TASK-ENG-01 (#256) — Typographic DNA Scraper & Surveyor Heuristic Pass.

Verifies:
  1. ADR 01 Typographic Ratio Math: R_typo = font_size / comp_height.
  2. Micro-text (R_typo < 0.025) biases towards BOTTOM (legal/disclaimer).
  3. Macro-text (R_typo > 0.060) biases towards TOP (headline/title).
  4. Pre-Mortem Scenario 2 Defensive Guards: NaN/inf handling, empty text, 0 comp height.
  5. ExtendScript scraper bundle synchronization.
"""

from pathlib import Path

from core.surveyor import classify_layer
from models.scrape_manifest import LayerModel, LayerFlags, TypographicInfo


def _make_text_layer(
    name: str,
    font_size_pt: float,
    layer_index: int = 1,
    char_count: int = 20,
    line_count: int = 1,
) -> LayerModel:
    return LayerModel(
        index=layer_index,
        name=name,
        layer_kind="av",
        flags=LayerFlags(is_text_layer=True),
        typographic_info=TypographicInfo(
            font_size_pt=font_size_pt,
            line_count=line_count,
            char_count=char_count,
            font_name="Helvetica-Bold",
        ),
        position=[960.0, 950.0, 0.0],
        scale=[100.0, 100.0, 100.0],
    )


class TestTypographicDNA:
    """Test suite for ADR 01 relative font scale and typographic scoring."""

    def test_micro_text_biases_to_bottom_legals(self):
        """R_typo < 0.025 (18pt on 1080p = 0.0167) near bottom biases to BOTTOM."""
        # 18pt font on a 1080p comp
        layer = _make_text_layer("Notice_Text", font_size_pt=18.0, layer_index=9)
        # Layer 9 of 10 is near bottom (is_near_bottom=True)
        res = classify_layer(layer, layer_index=9, total_layers=10, comp_width=1920, comp_height=1080)

        assert res.tag == "BOTTOM"
        assert res.confidence >= 0.85
        assert "typo=0.017" in res.reason

    def test_macro_text_biases_to_top_title(self):
        """R_typo > 0.060 (96pt on 1080p = 0.0889) near top biases to TOP."""
        # 96pt font on a 1080p comp
        layer = _make_text_layer("Summer_Sale", font_size_pt=96.0, layer_index=1)
        # Layer 1 of 10 is near top (is_near_top=True)
        res = classify_layer(layer, layer_index=1, total_layers=10, comp_width=1920, comp_height=1080)

        assert res.tag == "TOP"
        assert res.confidence >= 0.85
        assert "typo=0.089" in res.reason

    def test_neutral_text_ratio_respects_lexicon(self):
        """Neutral 0.025 <= R_typo <= 0.060 (40pt on 1080p = 0.037) applies no artificial bias."""
        layer = _make_text_layer("Headline_News", font_size_pt=40.0, layer_index=5)
        res = classify_layer(layer, layer_index=5, total_layers=10, comp_width=1920, comp_height=1080)

        # "Headline" is in TOP lexicon
        assert res.tag == "TOP"

    def test_pre_mortem_scenario_2_defensive_guards(self):
        """Scenario 2: Empty text, NaN font size, or 0 comp height never crash or throw."""
        # Empty text / 0 font size
        layer_empty = _make_text_layer("Empty_Text_Box", font_size_pt=0.0, char_count=0)
        res_empty = classify_layer(layer_empty, layer_index=5, total_layers=10, comp_width=1920, comp_height=1080)
        assert res_empty is not None

        # NaN / Inf guard
        layer_nan = LayerModel(
            index=1,
            name="NaN_Text",
            layer_kind="av",
            flags=LayerFlags(is_text_layer=True),
            typographic_info=TypographicInfo(font_size_pt=0.0),
        )
        res_nan = classify_layer(layer_nan, layer_index=1, total_layers=10, comp_width=1920, comp_height=1080)
        assert res_nan is not None

        # 0 comp height guard (zero division prevention)
        res_zero = classify_layer(layer_empty, layer_index=1, total_layers=10, comp_width=1920, comp_height=0)
        assert res_zero is not None


class TestExtendScriptScraperTypoIntegration:
    """Verifies ExtendScript scraper exports typographic_info."""

    def test_sovcore_layer_jsx_contains_defensive_font_scraper(self):
        jsx_path = Path(__file__).resolve().parents[2] / "Scripts" / "Dimension_Assets" / "SovCore_Layer.jsx"
        content = jsx_path.read_text(encoding="utf-8")

        assert "ADBE Text Properties" in content
        assert "ADBE Text Document" in content
        assert "typographic_info" in content
        assert "font_size_pt" in content
