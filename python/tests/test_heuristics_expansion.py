# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_heuristics_expansion.py
v5.6.0 regression coverage for the heuristics.json keyword expansion.

Locks every new keyword group added in Part A against synthetic
LayerModel instances. Asserts that:
  - new keywords route to the expected tag against the BASE
    heuristics (no profile applied)
  - existing keywords still classify the same way (no regressions)

The base classifier is `core.surveyor.classify_layer(layer, idx,
total_layers, profile=None)`. Confidence ≥ 0.5 on a clean keyword
match per the surveyor's `min_score_threshold`.

v5.10.1 — assertions migrated from the legacy vocabulary
(TYPE/LEGALS/BACKGROUND/KEYART) to the canonical vocabulary
(TT/LGL/BG/HERO) so they match the new heuristic emission.
v6.0 — assertions migrated to 4-tag canonical vocabulary
(TOP/BOTTOM/FILL/CENTER). Old tag names are aliases and normalize
to the new canonical form.
"""

from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


def _layer(name: str, *, idx: int = 1):
    """Build a minimal LayerModel for surveyor classification."""
    from models.scrape_manifest import LayerModel
    return LayerModel.model_validate({
        "uid":        f"u-{idx}",
        "name":       name,
        "index":      idx,
        "layer_kind": "av",
        "match_name": "ADBE Vector Layer",
        "transforms": {
            "raw": {
                "position":     [0, 0],
                "scale":        [100, 100],
                "anchor_point": [50, 50],
            },
        },
    })


def _classify(name: str) -> str:
    """Run the base surveyor (no profile) and return the tag."""
    from core.surveyor import classify_layer
    layer = _layer(name)
    result = classify_layer(layer, layer_index=1, total_layers=10,
                            profile=None)
    return result.tag


# ── TOP (was TT/TYPE) expansions ──────────────────────────────────


class TestTypeExpansion:
    def test_supertitle_classifies_as_type(self):
        assert _classify("supertitle_main") == "TOP"

    def test_l3_classifies_as_type(self):
        assert _classify("L3_anchor_name") == "TOP"

    def test_kicker_classifies_as_type(self):
        assert _classify("kicker_text") == "TOP"

    def test_locator_classifies_as_type(self):
        assert _classify("locator_dataline") == "TOP"

    def test_intertitle_classifies_as_type(self):
        assert _classify("intertitle_act_two") == "TOP"


# ── PROTECT expansions ─────────────────────────────────────────────
#
# PROTECT is structural-classifier-only in the surveyor (camera /
# light / null / adjustment / guide flags). The base `_score_layer`
# does NOT emit PROTECT — that path requires either a profile prefix
# rule or a manual override. v5.6.0 added PROTECT keywords to the
# dictionary so a future engine pass can promote them.
#
# Slot 11.5 Phase 2 collapsed the 9-profile baseline to 4
# (default / social / theatrical / ooh). The broadcast-specific
# routing (SCOREBUG_ / TICKER_ / CRAWL_) now lives inside whichever
# new profile owns persistent-UI prefixes. These tests retarget
# `sports_broadcast` / `news_broadcast` callers to the surviving
# ids; they remain RED until the green phase ships YAMLs that
# carry those prefixes through to PROTECT.


class TestProtectViaProfile:
    """Slot 11.5 — PROTECT keywords land in profile rules, not base
    heuristics. These tests now target the v6 profile set."""

    def _classify_with_profile(self, name: str, profile_id: str = "social") -> str:
        from core.surveyor import classify_layer
        from logic.studio_profile_registry import StudioProfileRegistry
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            reg = StudioProfileRegistry(
                user_dir=Path(tmp) / "profiles",
                baselines_dir=Path(__file__).resolve().parents[2]
                / "config" / "profiles",
            )
            profile = reg.get(profile_id)
            assert profile is not None, f"profile {profile_id!r} not found"
            layer = _layer(name)
            return classify_layer(layer, layer_index=1, total_layers=10,
                                  profile=profile).tag

    def test_scorebug_classifies_via_social_profile(self):
        # Phase 2 — broadcast persistent-UI prefixes route through
        # the `social` profile under the new four-profile baseline.
        assert self._classify_with_profile("SCOREBUG_main",
                                            profile_id="social") == "PROTECT"

    def test_ticker_classifies_via_social_profile(self):
        assert self._classify_with_profile("TICKER_main",
                                            profile_id="social") == "PROTECT"

    def test_crawl_classifies_via_social_profile(self):
        assert self._classify_with_profile("CRAWL_market_data",
                                            profile_id="social") == "PROTECT"

    def test_protect_keywords_present_in_dictionary(self):
        """PROTECT keywords are present in heuristics.json so a
        future engine pass can score them. Locks the data-shape
        contract even though the surveyor currently can't emit
        PROTECT from heuristic scoring."""
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "data" / "heuristics.json"
        data = json.loads(path.read_text())
        protect_kws = data["keywords"]["PROTECT"]
        for kw in ("scorebug", "score_bug", "ticker", "crawl",
                   "locked_position", "no_conform", "fixed_position"):
            assert kw in protect_kws, f"{kw!r} missing from PROTECT keywords"


# ── CENTER (was HERO/KEYART) expansions ───────────────────────────


class TestKeyartExpansion:
    def test_brand_lockup_classifies_as_keyart(self):
        assert _classify("BRAND_lockup_v3") == "CENTER"

    def test_endframe_logo_classifies_as_keyart(self):
        assert _classify("endframe_logo") == "CENTER"

    def test_show_lockup_classifies_as_keyart(self):
        assert _classify("show_lockup_main") == "CENTER"

    def test_team_logo_classifies_as_keyart(self):
        assert _classify("team_logo_lockup") == "CENTER"


# ── BOTTOM (was LGL/LEGALS) expansions ────────────────────────────


class TestLegalsExpansion:
    def test_billing_block_classifies_as_legals(self):
        assert _classify("billing_block_main") == "BOTTOM"

    def test_talent_credits_classifies_as_legals(self):
        assert _classify("talent_credits_v2") == "BOTTOM"

    def test_rating_card_classifies_as_legals(self):
        assert _classify("rating_card_pg13") == "BOTTOM"

    def test_mpaa_slate_classifies_as_legals(self):
        assert _classify("mpaa_slate_final") == "BOTTOM"

    def test_terms_classifies_as_legals(self):
        assert _classify("terms_disclosure") == "BOTTOM"


# ── CENTER (was BOXART) expansions ────────────────────────────────


class TestBoxartExpansion:
    def test_hero_product_classifies_as_boxart(self):
        assert _classify("hero_product_v2") == "CENTER"

    def test_beauty_shot_classifies_as_boxart(self):
        assert _classify("beauty_shot_final") == "CENTER"

    def test_packshot_clean_classifies_as_boxart(self):
        assert _classify("packshot_clean_a") == "CENTER"


# ── CENTER (was ARTWORK) expansions ───────────────────────────────


class TestArtworkExpansion:
    def test_l3_panel_classifies(self):
        # "l3_panel_bg" carries multiple signals: l3_panel → CENTER,
        # bg → FILL. Both are sensible; lock either as the contract so
        # a future tuning doesn't silently regress.
        assert _classify("l3_panel_bg") in ("CENTER", "FILL")

    def test_ticker_bg_classifies(self):
        # `ticker_bg_strip` carries ticker (PROTECT/keyword) +
        # ticker_bg (CENTER) + bg (FILL) signals. All three are valid
        # landing spots; the contract is "not unclassified".
        result = _classify("ticker_bg_strip")
        assert result in ("CENTER", "PROTECT", "FILL")

    def test_panel_keyword_classifies_as_artwork(self):
        assert _classify("info_panel_a") == "CENTER"


# ── CENTER (was ANIMATION) expansions ─────────────────────────────


class TestAnimationExpansion:
    def test_stinger_anim_classifies_as_animation(self):
        assert _classify("stinger_anim_main") == "CENTER"

    def test_act_break_classifies_as_animation(self):
        assert _classify("act_break_three") == "CENTER"

    def test_whoosh_classifies_as_animation(self):
        assert _classify("whoosh_transition") == "CENTER"


# ── FILL (was BG/BACKGROUND) expansions ───────────────────────────


class TestBackgroundExpansion:
    def test_studio_bg_classifies_as_background(self):
        assert _classify("studio_bg_news") == "FILL"

    def test_stadium_bg_classifies_as_background(self):
        assert _classify("stadium_bg_wide") == "FILL"


# ── GUIDE expansions ───────────────────────────────────────────────


class TestGuideExpansion:
    def test_title_safe_classifies_as_guide(self):
        assert _classify("title_safe_overlay") == "GUIDE"

    def test_pillarbox_classifies_as_guide(self):
        assert _classify("pillarbox_overlay") == "GUIDE"


# ── No-regression sweep ────────────────────────────────────────────


class TestNoRegressions:
    """The original heuristics.json keywords must still classify
    identically. If a v5.6 expansion accidentally outranks one of
    them on a shared-substring match, this catches it.

    v5.10.1: expected tag values migrated to canonical (TT/LGL/HERO/BG)
    so the canonical-emission contract is what's pinned. The legacy
    keyword *names* (title_main, legal_pin, etc.) are unchanged —
    only the asserted RESULT tag is canonical."""

    def test_pre_v56_type_keywords_still_classify(self):
        for name in ("headline_top", "subtitle_card",
                     "tagline_v2"):
            assert _classify(name) == "TOP", (
                f"{name!r} regressed off TOP classification"
            )

    def test_pre_v56_legals_keywords_still_classify(self):
        for name in ("legal_pin", "disclaimer_pad", "copyright_2026",
                     "trademark_block"):
            assert _classify(name) == "BOTTOM", (
                f"{name!r} regressed off BOTTOM classification"
            )

    def test_pre_v56_keyart_keywords_still_classify(self):
        for name in ("logo_main", "lockup_top", "wordmark_v3"):
            assert _classify(name) == "CENTER", (
                f"{name!r} regressed off CENTER classification"
            )

    def test_pre_v56_boxart_keywords_still_classify(self):
        for name in ("packshot_v1", "boxart_a", "poster_one"):
            assert _classify(name) == "CENTER", (
                f"{name!r} regressed off CENTER classification"
            )

    def test_pre_v56_background_keywords_still_classify(self):
        for name in ("bg_plate", "background_solid", "backdrop_grad"):
            assert _classify(name) == "FILL", (
                f"{name!r} regressed off FILL classification"
            )
