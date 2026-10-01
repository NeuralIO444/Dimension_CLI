# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_social_channel_assignment.py

Programmatic safe-zones Phase 1 (2026-09-05, see
docs/knowledge/2026-09-05-programmatic-safe-zones-research.md).

`config/channels.yaml`'s "social" rule models platform UI chrome
(caption bar + action buttons) on a vertical 9:16 VIDEO frame. Before
this change, all 42 social catalog targets had `channel=None` despite
that rule existing and being tested -- 66% of the full 265-target
catalog got no safe zone at all, and social's coverage was 6/42 (PNG
only).

Scope is deliberately narrow: only the 9 targets that are genuinely
9:16 vertical video/motion-context formats get `channel="social"`.
The other 33 (profile pictures, cover photos, banners, landscape/square
static posts) are NOT tagged -- a caption-bar occlusion model is
semantically meaningless on a static profile picture and would nudge
layers away from a "chrome zone" that doesn't exist there, distorting
real client conforms for no reason. Matt confirmed this exact 9-target
list before implementation.

Of the 9, 5 already resolved via a legacy per-subcategory/per-target
PNG (instagram_story, tiktok_video, tiktok_carousel, tiktok_thumb,
youtube_shorts) -- Leg 1b/Leg 2 of the resolver ladder wins before the
new channel rule (Leg 3) ever runs, so tagging them is correct/future-
proofing but does not change today's resolved mask. Only 4 targets see
genuinely NEW SOE protection: instagram_reel_thumb, facebook_story,
threads_video, pinterest_idea.
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.target_catalog import BUILTIN_TARGETS, SOCIAL
from logic.safe_zone_resolver import resolve_mask_for_target

_EXPECTED_SOCIAL_CHANNEL_IDS = {
    "builtin:instagram_story",
    "builtin:instagram_reel_thumb",
    "builtin:facebook_story",
    "builtin:tiktok_video",
    "builtin:tiktok_carousel",
    "builtin:tiktok_thumb",
    "builtin:threads_video",
    "builtin:pinterest_idea",
    "builtin:youtube_shorts",
}

# Of the 9, only these actually pick up a NEW mask from the channel
# rule -- the rest were already covered by a legacy PNG that resolves
# first in the ladder.
_NEWLY_PROTECTED_IDS = {
    "builtin:instagram_reel_thumb",
    "builtin:facebook_story",
    "builtin:threads_video",
    "builtin:pinterest_idea",
}


class TestSocialChannelScope:
    def test_exactly_nine_social_targets_have_a_channel(self):
        tagged = {t.id for t in SOCIAL if t.channel}
        assert tagged == _EXPECTED_SOCIAL_CHANNEL_IDS

    def test_every_tagged_target_is_vertical_9x16(self):
        by_id = {t.id: t for t in SOCIAL}
        for target_id in _EXPECTED_SOCIAL_CHANNEL_IDS:
            t = by_id[target_id]
            assert t.width == 1080 and t.height == 1920, (
                f"{target_id} is {t.width}x{t.height} -- the social "
                "channel rule models a 9:16 vertical video frame; "
                "tagging a non-9:16 target here would be a scope error"
            )

    def test_tagged_targets_use_the_social_channel_specifically(self):
        by_id = {t.id: t for t in SOCIAL}
        for target_id in _EXPECTED_SOCIAL_CHANNEL_IDS:
            assert by_id[target_id].channel == "social"

    def test_no_other_social_target_was_touched(self):
        """The 33 static-format social targets (profile pictures, cover
        photos, banners, landscape/square posts) must remain
        channel=None -- this is the scope boundary the whole point of
        narrowing to 9 targets depends on."""
        untouched = {t.id for t in SOCIAL if not t.channel}
        assert len(untouched) == 42 - len(_EXPECTED_SOCIAL_CHANNEL_IDS)
        assert untouched.isdisjoint(_EXPECTED_SOCIAL_CHANNEL_IDS)

    def test_catalog_totals_unchanged(self):
        """Pure data/tagging change -- must not add, remove, or resize
        any target."""
        assert len(SOCIAL) == 42
        assert len(BUILTIN_TARGETS) == 265


class TestSocialChannelResolution:
    """Integration check against the REAL catalog and the REAL
    config/safe_zones/ directory on disk -- not a synthetic tmp_path
    fixture, per CLAUDE.md's anti-pattern on trusting synthetic
    fixtures for integration contracts."""

    def _by_id(self, target_id):
        for t in BUILTIN_TARGETS:
            if t.id == target_id:
                return t
        raise KeyError(target_id)

    def test_newly_protected_targets_resolve_via_the_channel_rule(self):
        for target_id in _NEWLY_PROTECTED_IDS:
            mask = resolve_mask_for_target(self._by_id(target_id))
            assert isinstance(mask, np.ndarray), (
                f"{target_id} should resolve via the new channel-derived "
                f"vector rule (an ndarray), got {type(mask)!r}"
            )

    def test_legacy_png_covered_targets_still_resolve_via_their_png(self):
        """These 5 were already protected before this change -- confirms
        tagging them didn't break their existing (higher-precedence)
        PNG resolution, it's just inert/future-proofing for them today."""
        legacy_covered = _EXPECTED_SOCIAL_CHANNEL_IDS - _NEWLY_PROTECTED_IDS
        for target_id in legacy_covered:
            mask = resolve_mask_for_target(self._by_id(target_id))
            assert mask is not None
            assert not isinstance(mask, np.ndarray), (
                f"{target_id} was expected to resolve via a legacy PNG "
                f"(Path), not the new channel rule (ndarray) -- if this "
                f"now fails, the PNG this target used to resolve via may "
                f"have been removed"
            )

    def test_untagged_static_social_target_now_resolves_via_insets(self):
        """Spot-check a representative untouched target (a static
        profile picture) -- with D2 channel-first (2026-10-01) it now
        resolves via the Leg 2.4 numeric-insets fallback instead of
        reaching MASK_MISSING. Intended widening: the target has no PNG
        asset and no channel rule, so insets are its last synthesized
        fallback."""
        profile = self._by_id("builtin:instagram_profile")
        assert profile.channel is None
        mask = resolve_mask_for_target(profile)
        assert isinstance(mask, np.ndarray), (
            "expected the Leg 2.4 numeric-insets fallback (ndarray), got "
            f"{type(mask)!r}"
        )
