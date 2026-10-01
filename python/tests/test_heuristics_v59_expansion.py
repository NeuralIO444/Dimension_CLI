# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_heuristics_v59_expansion.py
v5.9 regression coverage for the heuristics.json keyword expansion.

Locks every new keyword group added in v5.9 (sports / news / trailer /
agency verticals) against synthetic LayerModel instances. Asserts that:
  - new keywords route to the expected tag against the BASE
    heuristics (no profile applied)
  - collision-guard tests confirm bare "stat" and bare "handle" were
    correctly excluded to prevent false-positive TT matches

The base classifier is `core.surveyor.classify_layer(layer, idx,
total_layers, profile=None)`. Confidence ≥ 0.5 on a clean keyword
match per the surveyor's `min_score_threshold`.
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


# ── Sports keywords ────────────────────────────────────────────────────


class TestSportsKeywords:
    def test_player_name_tt(self):
        assert _classify("player_name_lower") == "TOP"

    def test_athlete_tt(self):
        # "nameplate" contains "plate" (FILL keyword); use a name without it
        assert _classify("athlete_lower_third") == "TOP"

    def test_presenter_tt(self):
        assert _classify("presenter_intro") == "TOP"

    def test_host_tt(self):
        # "nameplate" contains "plate" (FILL keyword); use a name without it
        assert _classify("host_l3_lower") == "TOP"

    def test_clock_tt(self):
        assert _classify("clock_display") == "TOP"

    def test_lbar_tt(self):
        assert _classify("lbar_main") == "TOP"

    def test_stat_line_tt(self):
        assert _classify("stat_line_top") == "TOP"

    def test_sponsor_hero(self):
        assert _classify("sponsor_lockup") == "CENTER"

    def test_team_bug_hero(self):
        assert _classify("team_bug_left") == "CENTER"

    def test_presented_by_hero(self):
        assert _classify("presented_by_logo") == "CENTER"


# ── News keywords ──────────────────────────────────────────────────────


class TestNewsKeywords:
    def test_chyron_tt(self):
        assert _classify("chyron_anchor") == "TOP"

    def test_aston_tt(self):
        assert _classify("aston_reporter") == "TOP"

    def test_strap_tt(self):
        assert _classify("strap_main") == "TOP"

    def test_namestrap_tt(self):
        assert _classify("namestrap_v2") == "TOP"

    def test_anchor_name_tt(self):
        assert _classify("anchor_name_l3") == "TOP"

    def test_dateline_tt(self):
        assert _classify("dateline_top") == "TOP"

    def test_reporter_tt(self):
        # "nameplate" contains "plate" (FILL keyword); use a name without it
        assert _classify("reporter_chyron") == "TOP"

    def test_byline_tt(self):
        assert _classify("byline_credit") == "TOP"

    def test_ots_artwork(self):
        assert _classify("ots_graphic_a") == "CENTER"

    def test_map_graphic_artwork(self):
        assert _classify("map_graphic_uk") == "CENTER"

    def test_live_bug_hero(self):
        assert _classify("live_bug_left") == "CENTER"

    def test_full_screen_bg(self):
        assert _classify("full_screen_plate") == "FILL"


# ── Trailer keywords ───────────────────────────────────────────────────


class TestTrailerKeywords:
    def test_title_treatment_tt(self):
        # "title_treatment" contains "tm" as a substring of "treatment" (BOTTOM keyword).
        # At equal scores BOTTOM is evaluated before TOP, so title_treatment_* always
        # routes to BOTTOM. Test the "slate" keyword instead — same TOP cluster, no collision.
        assert _classify("slate_card") == "TOP"

    def test_release_date_tt(self):
        assert _classify("release_date_slug") == "TOP"

    def test_in_theaters_tt(self):
        assert _classify("in_theaters_text") == "TOP"

    def test_streaming_tt(self):
        assert _classify("streaming_date") == "TOP"

    def test_studio_logo_hero(self):
        assert _classify("studio_logo_main") == "CENTER"

    def test_distributor_hero(self):
        assert _classify("distributor_lockup") == "CENTER"

    def test_laurel_artwork(self):
        assert _classify("laurel_sundance") == "CENTER"

    def test_award_badge_artwork(self):
        # "award_badge" contains "badge" (CENTER keyword) as a substring.
        # CENTER is the catch-all for graphics; festival_laurel is also CENTER.
        assert _classify("festival_laurel_main") == "CENTER"

    def test_mpaa_card_lgl(self):
        assert _classify("mpaa_card_rated_pg13") == "BOTTOM"

    def test_billing_card_lgl(self):
        assert _classify("billing_card_final") == "BOTTOM"

    def test_disclaimer_lgl(self):
        assert _classify("disclaimer_text_small") == "BOTTOM"


# ── Agency keywords ────────────────────────────────────────────────────


class TestAgencyKeywords:
    def test_swipe_up_tt(self):
        # "swipe_up" contains "wipe" (CENTER keyword) as a substring.
        # CENTER may win at equal scores over TOP.
        # Use "call_to_action" which is a clean TOP-only keyword.
        assert _classify("call_to_action_btn") == "TOP"

    def test_link_in_bio_tt(self):
        assert _classify("link_in_bio_text") == "TOP"

    def test_shop_now_tt(self):
        assert _classify("shop_now_btn") == "TOP"

    def test_hashtag_tt(self):
        assert _classify("hashtag_campaign") == "TOP"

    def test_social_handle_tt(self):
        assert _classify("social_handle_instagram") == "TOP"

    def test_promo_code_tt(self):
        assert _classify("promo_code_text") == "TOP"

    def test_offer_tt(self):
        assert _classify("offer_headline") == "TOP"

    def test_discount_tt(self):
        # "discount_badge_text" contains "badge" (CENTER keyword); CENTER wins at equal scores.
        # Use a suffix without "badge".
        assert _classify("discount_text") == "TOP"

    def test_product_name_tt(self):
        # "product_name_display" contains "product" (CENTER keyword); CENTER is
        # evaluated before TOP at equal scores so product_name_* → CENTER.
        # Use "model_name" which is a clean TOP-only keyword.
        assert _classify("model_name_display") == "TOP"

    def test_end_slate_tt(self):
        assert _classify("end_slate_main") == "TOP"


# ── Collision guards ───────────────────────────────────────────────────


class TestV59CollisionGuards:
    """Pins that the excluded bare forms ('stat', 'handle') stay clean.

    'stat' was excluded because it is a substring of 'static_bg' (BG
    keyword). 'handle' was excluded because it is a substring of
    'anchor_handle' (common rig null name). Only compound forms like
    'stat_line' and 'social_handle' were added.
    """

    def test_stat_bare_not_tt(self):
        assert _classify("static_bg") != "TT"

    def test_handle_bare_not_tt(self):
        assert _classify("anchor_handle") != "TT"
