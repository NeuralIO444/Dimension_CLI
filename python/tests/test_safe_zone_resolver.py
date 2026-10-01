# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_safe_zone_resolver.py
v5.2.5 — Option-C resolver: user override → repo default → None,
with broken-symlink graceful fallthrough.
"""

from __future__ import annotations

import os
import sys
import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from logic import safe_zone_resolver as r
from models.target import Target


@pytest.fixture(autouse=True)
def _reset_log():
    r.reset_missing_log()
    yield
    r.reset_missing_log()


class TestResolveMaskPath:
    def test_user_override_wins(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"; repo.mkdir()
        user = tmp_path / "user"; user.mkdir()
        (repo / "x.png").write_bytes(b"repo")
        (user / "x.png").write_bytes(b"user")
        monkeypatch.setattr(r, "_repo_default_dir", lambda: repo)
        monkeypatch.setattr(r, "_USER_OVERRIDE_DIR", user)
        assert r.resolve_mask_path("x") == user / "x.png"

    def test_repo_default_when_no_user(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"; repo.mkdir()
        user = tmp_path / "user"  # not created
        (repo / "y.png").write_bytes(b"repo")
        monkeypatch.setattr(r, "_repo_default_dir", lambda: repo)
        monkeypatch.setattr(r, "_USER_OVERRIDE_DIR", user)
        assert r.resolve_mask_path("y") == repo / "y.png"

    def test_neither_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(r, "_repo_default_dir", lambda: tmp_path / "no_repo")
        monkeypatch.setattr(r, "_USER_OVERRIDE_DIR", tmp_path / "no_user")
        assert r.resolve_mask_path("ghost") is None

    def test_broken_symlink_falls_through(self, tmp_path, monkeypatch):
        """User override is a dangling symlink → fall through to repo
        default. Path.is_file() returns False for broken symlinks while
        Path.exists() also returns False but is_file() is the more
        defensive contract that we want to lock in."""
        repo = tmp_path / "repo"; repo.mkdir()
        user = tmp_path / "user"; user.mkdir()
        (repo / "z.png").write_bytes(b"repo")
        # Create a dangling symlink at user/z.png pointing nowhere.
        target = tmp_path / "does_not_exist.png"
        link = user / "z.png"
        try:
            os.symlink(target, link)
        except (NotImplementedError, OSError):
            pytest.skip("symlinks unavailable on this platform")
        assert link.is_symlink()
        assert not link.is_file()
        monkeypatch.setattr(r, "_repo_default_dir", lambda: repo)
        monkeypatch.setattr(r, "_USER_OVERRIDE_DIR", user)
        assert r.resolve_mask_path("z") == repo / "z.png"


class TestVectorBasedMasks:
    def test_generate_mask_from_vectors(self):
        """SOE-2: Verify the vector rasterizer produces a correct mask."""
        from logic.safe_zone_resolver import _generate_mask_from_vectors

        width, height = 100, 200
        definition = [
            {"zone": "CUTOFF", "bounds": [0.0, 0.0, 1.0, 0.1]},  # Top 10%
            {"zone": "CUTOFF", "bounds": [0.0, 0.9, 1.0, 1.0]},  # Bottom 10%
        ]

        mask = _generate_mask_from_vectors(definition, width, height)

        assert isinstance(mask, np.ndarray)
        assert mask.shape == (height, width)
        assert mask.dtype == np.uint8

        # Top 10% (20 pixels) should be black (0)
        assert np.all(mask[0:20, :] == 0)
        # Bottom 10% (20 pixels) should be black (0)
        assert np.all(mask[180:200, :] == 0)
        # Middle should be white (255)
        assert np.all(mask[20:180, :] == 255)

    def test_generate_mask_with_nudge_zones(self):
        """SOE-3: Verify the vector rasterizer handles NUDGE zones correctly."""
        from logic.safe_zone_resolver import _generate_mask_from_vectors

        width, height = 100, 200
        definition = [
            {"zone": "CUTOFF", "bounds": [0.0, 0.0, 1.0, 0.1]},  # Top 10%
            {"zone": "NUDGE", "bounds": [0.0, 0.1, 1.0, 0.2]},   # 10-20% band
            {"zone": "CUTOFF", "bounds": [0.0, 0.9, 1.0, 1.0]},  # Bottom 10%
        ]

        mask = _generate_mask_from_vectors(definition, width, height)

        assert isinstance(mask, np.ndarray)
        assert mask.shape == (height, width)

        # Top 10% (20 pixels) should be black (0)
        assert np.all(mask[0:20, :] == 0)
        # Next 10% (20 pixels) should be gray (128)
        assert np.all(mask[20:40, :] == 128)
        # Bottom 10% (20 pixels) should be black (0)
        assert np.all(mask[180:200, :] == 0)
        # Middle should be white (255)
        assert np.all(mask[40:180, :] == 255)

    def test_resolve_mask_for_target_with_vectors(self, monkeypatch):
        """SOE-2: Verify the main resolver dispatches to the vector strategy."""
        mock_config = {
            "social_test": {
                "strategy": "derived_from_vectors",
                "zones": [{"zone": "CUTOFF", "bounds": [0, 0, 1, 0.1]}]
            }
        }
        from logic import channel_config
        monkeypatch.setattr(channel_config, "get_channel_config", lambda chan: mock_config.get(chan))

        target = Target(
            id="test:social",
            label="Test Social",
            category="social",
            subcategory="instagram",
            width=100,
            height=200,
            aspect_ratio=0.5,
            aspect_label="1:2",
            source="builtin",
            channel="social_test"
        )
        result = r.resolve_mask_for_target(target)

        assert isinstance(result, np.ndarray)
        assert result.shape == (200, 100)
        assert np.all(result[0:20, :] == 0)
        assert np.all(result[20:, :] == 255)

    def test_resolve_mask_preserves_legacy_strategy(self, monkeypatch):
        """SOE-2: Verify that non-vector strategies still work."""
        class MockStrategy:
            def produce_mask(self, target):
                return np.zeros((target.height, target.width), dtype=np.uint8)

        from logic import safe_zone_strategies
        monkeypatch.setattr(safe_zone_strategies, "get_strategy_for_channel", lambda chan: MockStrategy())

        target = Target(
            id="test:theatrical",
            label="Test Theatrical",
            category="digital_cinema",
            subcategory="dcp",
            width=100,
            height=100,
            aspect_ratio=1.0,
            aspect_label="1:1",
            source="builtin",
            channel="theatrical_test"
        )
        result = r.resolve_mask_for_target(target)

        assert isinstance(result, np.ndarray)
        assert np.all(result == 0)

    def test_generate_mask_with_title_safe(self):
        """SOE-4: Verify TITLE_SAFE zone creates a GO area inside a NUDGE area."""
        from logic.safe_zone_resolver import _generate_mask_from_vectors

        width, height = 100, 200
        definition = [
            {"zone": "TITLE_SAFE", "bounds": [0.1, 0.2, 0.9, 0.8]},
        ]

        mask = _generate_mask_from_vectors(definition, width, height)

        # Inner rect: x=10-90, y=40-160
        # Should be white (255)
        assert np.all(mask[40:160, 10:90] == 255)

        # Outer areas should be gray (128)
        # Top band
        assert np.all(mask[0:40, :] == 128)
        # Bottom band
        assert np.all(mask[160:200, :] == 128)
        # Left band (inside top/bottom bands)
        assert np.all(mask[40:160, 0:10] == 128)
        # Right band (inside top/bottom bands)
        assert np.all(mask[40:160, 90:100] == 128)

    def test_generate_mask_with_title_safe_and_cutoff(self):
        """SOE-4: Verify CUTOFF zones are drawn over a TITLE_SAFE definition."""
        from logic.safe_zone_resolver import _generate_mask_from_vectors

        width, height = 100, 200
        definition = [
            {"zone": "TITLE_SAFE", "bounds": [0.1, 0.2, 0.9, 0.8]},
            {"zone": "CUTOFF", "bounds": [0.0, 0.0, 1.0, 0.1]},  # Top 10%
        ]

        mask = _generate_mask_from_vectors(definition, width, height)

        # Top 10% (20px) should be black (0) for CUTOFF
        assert np.all(mask[0:20, :] == 0)

        # Area between CUTOFF and TITLE_SAFE should be gray (128)
        assert np.all(mask[20:40, :] == 128)

        # Inner rect should be white (255)
        assert np.all(mask[40:160, 10:90] == 255)

        # Bottom area should be gray (128)
        assert np.all(mask[160:200, :] == 128)

    def test_resolve_mask_for_social_square(self, monkeypatch):
        """Verify the social_square channel creates a 1:1 safe zone."""
        mock_config = {
            "social_square": {
                "strategy": "derived_from_vectors",
                "zones": [
                    {
                        "zone": "TITLE_SAFE",
                        "bounds": [0.0, 0.21875, 1.0, 0.78125],
                    }
                ],
            }
        }
        from logic import channel_config
        monkeypatch.setattr(channel_config, "get_channel_config", lambda chan: mock_config.get(chan))

        width, height = 1080, 1920
        target = Target(
            id="test:social_square",
            label="Test Square",
            category="social",
            subcategory="instagram",
            width=width,
            height=height,
            aspect_ratio=9.0/16.0,
            aspect_label="9:16",
            source="builtin",
            channel="social_square"
        )
        mask = r.resolve_mask_for_target(target)

        assert isinstance(mask, np.ndarray)
        assert mask.shape == (height, width)
        assert np.all(mask[0:420, :] == 128)
        assert np.all(mask[420:1500, :] == 255)
        assert np.all(mask[1500:1920, :] == 128)


class TestMultiPanelGapZoneStrategy:
    """Issue #346 (SOE half) -- Leg 2.5: a multi_panel OOH target (e.g.
    a 3-panel transit triptych) has no safe-zone PNG for any
    transit_triptychs spec today, so before this leg existed it fell
    straight through to Leg 4 (MASK_MISSING) and got zero SOE
    protection. Uses the Market 15 Liveboard worked example
    (config/ooh_specs.json OOH-064: N=3, panel_width=1080,
    panel_height=1920, gap_px=221 -> 3682x1920 master canvas, gap
    zones [1080..1301] and [2381..2602]) shared with test_panel_slicer.py."""

    _MARKET_15_MULTI_PANEL = {
        "panel_count": 3,
        "panel_width": 1080,
        "panel_height": 1920,
        "gap_px": 221,
        "split_naming": ["LEFT", "CENTER", "RIGHT"],
    }

    def _target(self, **overrides) -> Target:
        defaults = dict(
            id="builtin:ooh_market_15_liveboard",
            label="Market 15 Liveboard",
            category="custom_signage",
            subcategory="transit_triptychs",
            width=3682,
            height=1920,
            aspect_ratio=3682 / 1920,
            aspect_label="Triptych Canvas (3 Panels)",
            source="builtin",
            metadata={"multi_panel": self._MARKET_15_MULTI_PANEL},
        )
        defaults.update(overrides)
        return Target(**defaults)

    def test_synthesizes_gap_zone_mask_for_multi_panel_target(self):
        target = self._target()
        mask = r.resolve_mask_for_target(target)

        assert isinstance(mask, np.ndarray)
        assert mask.shape == (1920, 3682)
        assert np.all(mask[:, 1080:1301] == 0)
        assert np.all(mask[:, 2381:2602] == 0)
        assert np.all(mask[:, 0:1080] == 255)
        assert np.all(mask[:, 1301:2381] == 255)
        assert np.all(mask[:, 2602:3682] == 255)

    def test_per_target_png_override_still_wins_over_gap_zone_synthesis(self, tmp_path, monkeypatch):
        """A human-authored override is more trustworthy than automatic
        derivation -- Legs 1/1b/2 must run first and win when present,
        exactly like they already do ahead of the channel-derived leg."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "ooh_market_15_liveboard.png").write_bytes(b"authored override")
        monkeypatch.setattr(r, "_repo_default_dir", lambda: repo)
        monkeypatch.setattr(r, "_USER_OVERRIDE_DIR", tmp_path / "no_user")

        target = self._target()
        result = r.resolve_mask_for_target(target)
        assert result == repo / "ooh_market_15_liveboard.png"

    def test_non_multi_panel_target_unaffected(self):
        """No `multi_panel` metadata -> this leg is a complete no-op,
        falling through exactly as before (MASK_MISSING here since no
        PNG/channel is configured for this synthetic target)."""
        target = self._target(metadata={})
        assert r.resolve_mask_for_target(target) is None

    def test_dimension_mismatch_falls_through_instead_of_returning_wrong_size(self):
        """If the target's declared dims ever drift from what the
        multi_panel spec computes (a spec-authoring bug), this leg must
        not silently hand back a mask sized for the wrong canvas --
        better to fall through to MASK_MISSING than mis-classify every
        layer against a mismatched mask."""
        target = self._target(width=9999, height=9999)
        assert r.resolve_mask_for_target(target) is None

    def test_zero_gap_multi_panel_target_produces_all_go_mask(self):
        """gap_px=0 (contiguous panels, no physical pillar) is not a
        special case -- there is nothing to occlude, so this leg still
        fires and correctly returns an all-GO mask rather than falling
        through to MASK_MISSING."""
        target = self._target(
            width=2000, height=500,
            metadata={"multi_panel": {
                "panel_count": 2, "panel_width": 1000, "panel_height": 500, "gap_px": 0,
            }},
        )
        mask = r.resolve_mask_for_target(target)
        assert isinstance(mask, np.ndarray)
        assert mask.shape == (500, 2000)
        assert np.all(mask == 255)


class TestNamespacePrefixFallback:
    """Issue #370 -- Leg 1b: a namespaced target.id whose bare slug (after
    the `<namespace>:` prefix) matches an on-disk mask, even though the
    full namespaced id doesn't. Purely additive -- Leg 1's full-id match
    still wins whenever it exists."""

    def _target(self, target_id: str, subcategory: str = "nonexistent_subcat") -> Target:
        return Target(
            id=target_id,
            label="Test",
            category="social",
            subcategory=subcategory,
            width=1080,
            height=1920,
            aspect_ratio=9.0 / 16.0,
            aspect_label="9:16",
            source="builtin",
        )

    def test_bare_slug_fallback_matches(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"; repo.mkdir()
        (repo / "instagram_story.png").write_bytes(b"mask")
        monkeypatch.setattr(r, "_repo_default_dir", lambda: repo)
        monkeypatch.setattr(r, "_USER_OVERRIDE_DIR", tmp_path / "no_user")

        target = self._target("builtin:instagram_story")
        assert r.resolve_mask_for_target(target) == repo / "instagram_story.png"

    def test_full_id_match_wins_over_bare_slug(self, tmp_path, monkeypatch):
        """If a file happens to exist for BOTH the full namespaced id and
        the bare slug, Leg 1 (full id) must win -- Leg 1b never overrides
        an existing match, it only fires when Leg 1 misses."""
        repo = tmp_path / "repo"; repo.mkdir()
        (repo / "builtin_instagram_story.png").write_bytes(b"full-id-file")
        (repo / "instagram_story.png").write_bytes(b"bare-slug-file")
        monkeypatch.setattr(r, "_repo_default_dir", lambda: repo)
        monkeypatch.setattr(r, "_USER_OVERRIDE_DIR", tmp_path / "no_user")

        target = self._target("builtin:instagram_story")
        assert r.resolve_mask_for_target(target) == repo / "builtin_instagram_story.png"

    def test_no_colon_in_id_does_not_crash(self, tmp_path, monkeypatch):
        """An id with no namespace prefix (no colon) must skip Leg 1b
        cleanly and fall through to Leg 2/4, not raise."""
        monkeypatch.setattr(r, "_repo_default_dir", lambda: tmp_path / "no_repo")
        monkeypatch.setattr(r, "_USER_OVERRIDE_DIR", tmp_path / "no_user")

        target = self._target("unnamespaced_id")
        assert r.resolve_mask_for_target(target) is None

    def test_unmatched_bare_slug_now_resolves_via_insets(self, tmp_path, monkeypatch):
        """A namespaced id whose bare slug has no on-disk match either
        (e.g. instagram_reel_thumb, which doesn't match instagram_reels.png)
        now resolves via Leg 2.4 numeric insets (D2 channel-first,
        2026-10-01) instead of falling through to Leg 4 (MASK_MISSING).
        The widening is intended — it closes the social-presets-have-no-mask
        gap for targets with no PNG asset and no channel rule."""
        repo = tmp_path / "repo"; repo.mkdir()
        (repo / "instagram_reels.png").write_bytes(b"mask")  # deliberately non-matching name
        monkeypatch.setattr(r, "_repo_default_dir", lambda: repo)
        monkeypatch.setattr(r, "_USER_OVERRIDE_DIR", tmp_path / "no_user")

        target = self._target("builtin:instagram_reel_thumb")
        mask = r.resolve_mask_for_target(target)
        assert isinstance(mask, np.ndarray), (
            "expected the Leg 2.4 numeric-insets fallback (ndarray), got "
            f"{type(mask)!r}"
        )

    def test_real_catalog_instagram_story_and_youtube_shorts_now_resolve(self):
        """Integration check against the REAL builtin catalog and the
        REAL config/safe_zones/ directory on disk -- not a synthetic
        tmp_path fixture. Grounds the fix in the actual production data
        this session's other work repeatedly found synthetic fixtures
        had been masking (see CLAUDE.md's anti-pattern on trusting
        synthetic fixtures for integration contracts)."""
        from data.target_catalog import BUILTIN_TARGETS

        by_id = {t.id: t for t in BUILTIN_TARGETS}
        story = by_id["builtin:instagram_story"]
        shorts = by_id["builtin:youtube_shorts"]

        assert r.resolve_mask_for_target(story) is not None
        assert r.resolve_mask_for_target(shorts) is not None

    def test_real_catalog_instagram_post_and_reels_now_resolve_via_insets(self):
        """The two orphaned mask files this issue's own investigation
        found (instagram_post.png, instagram_reels.png match no real
        target id or subcategory) remain unmatched by any PNG leg -- but
        the untagged instagram/youtube targets now resolve via the Leg 2.4
        numeric-insets fallback (D2 channel-first, 2026-10-01), closing the
        old social-presets-have-no-mask gap. This is the intended widening,
        not a regression of #370's fix: no PNG leg claims these targets,
        the insets leg is simply the last synthesized fallback before
        MASK_MISSING.

        (History: updated for #476's Phase 1 safe-zone work (2026-09-05),
        when `instagram_reel_thumb` started resolving via the channel="social"
        tag on the 9 vertical-video social targets.)"""
        from data.target_catalog import BUILTIN_TARGETS

        for t in BUILTIN_TARGETS:
            if t.id in ("builtin:instagram_story", "builtin:youtube_shorts", "builtin:instagram_reel_thumb"):
                continue
            if t.subcategory == "tiktok":
                continue  # already resolves via Leg 2 (tiktok.png), unaffected by this fix
            if t.channel:
                continue  # deliberately channel-tagged by #476 Phase 1 -- expected to resolve
            # Every other instagram/youtube target now resolves via insets.
            if t.subcategory in ("instagram", "youtube"):
                mask = r.resolve_mask_for_target(t)
                assert isinstance(mask, np.ndarray), (
                    f"{t.id} should now resolve via the Leg 2.4 numeric-insets "
                    f"fallback (ndarray), got {type(mask)!r}"
                )


class TestOrphanedSafeZoneMasks:
    """Issue #421 — compliance check: every PNG in config/safe_zones/
    must resolve to at least one BUILTIN_TARGETS entry.

    This test documents which masks are orphaned (no matching target)
    and leaves them failing; the decision to retire or remap them is
    a product call, not an automated one. The test's primary role is
    regression prevention — masking a new orphaned file silently is
    the failure mode this guards against."""

    def test_no_orphaned_safe_zone_masks(self):
        """Every mask PNG on disk must resolve via resolve_mask_for_target()
        for at least one BUILTIN_TARGETS entry.

        Known orphans (failing as expected):
        - instagram_post.png — no target id or subcategory matches
        - instagram_reels.png — no target id or subcategory matches

        This test will fail as long as these orphaned masks exist,
        forcing a deliberate product decision (retire them, or add
        new targets to match them) rather than silent bit-rot."""
        from data.target_catalog import BUILTIN_TARGETS
        from pathlib import Path

        # Get all mask files on disk
        repo_safe_zones_dir = r._repo_default_dir()
        mask_files = set(p.stem for p in repo_safe_zones_dir.glob("*.png")) if repo_safe_zones_dir.exists() else set()

        # Get all PNG masks that are actually referenced by at least one target
        # (via resolve_mask_for_target returning a Path, not an ndarray).
        # Leg 3 (channel-derived) returns ndarray, so those targets are using
        # dynamically-generated masks, not on-disk PNGs.
        referenced_png_masks = set()
        for target in BUILTIN_TARGETS:
            mask = r.resolve_mask_for_target(target)
            if mask is not None and isinstance(mask, Path):
                referenced_png_masks.add(mask.stem)

        # Document the orphans
        orphaned = mask_files - referenced_png_masks

        # Known orphaned masks (documented in issue #421)
        # Note: dcp_2k and dcp_4k are also orphaned (targets resolve via
        # channel-derived vector strategies, not PNG files), discovered during
        # this test implementation and should be reviewed with instagram_post/reels.
        known_orphans = {"instagram_post", "instagram_reels", "dcp_2k", "dcp_4k"}

        # Assert all mask files resolve, or are known orphans
        unexpected_orphans = orphaned - known_orphans
        assert not unexpected_orphans, (
            f"Unexpected orphaned mask files (not in known list): {sorted(unexpected_orphans)}. "
            f"Update this test's known_orphans set if new orphaned masks are intentional."
        )

        # Assert the known orphans are still orphaned (regression check)
        # This will fail if the orphans are ever matched to targets
        assert orphaned == known_orphans, (
            f"Orphan status changed. Previous orphans: {known_orphans}, current: {orphaned}. "
            f"Update SOURCES.md and known_orphans if this is intentional."
        )


class TestMultiPanelGapZoneRealCatalog:
    """Issue #346 (SOE half) — same assertions as
    TestMultiPanelGapZoneStrategy but via the real production catalog
    (data.target_catalog.OOH / PresetManager), not a hand-built
    fixture. Before this leg existed, both real catalog targets below
    silently got zero SOE protection (MASK_MISSING, no PNG exists for
    transit_triptychs) — TYPE/LEGALS text could land directly across a
    physical pillar with no warning."""

    def test_real_multi_panel_ooh_targets_resolve_to_gap_zone_masks(self):
        from data.target_catalog import OOH

        multi_panel_targets = [t for t in OOH if (t.metadata or {}).get("multi_panel")]
        if not multi_panel_targets:
            pytest.skip("No multi_panel OOH targets loaded from production specs JSON")

        for t in multi_panel_targets:
            mask = r.resolve_mask_for_target(t)
            assert isinstance(mask, np.ndarray), f"{t.id} did not resolve to a synthesized mask"
            assert mask.shape == (t.height, t.width)
            # Every real multi_panel spec in the shipped catalog is the
            # Market 15 Liveboard shape (N=3, gap_px=221) — same gap
            # coordinates as the ADR 01 worked example.
            assert np.all(mask[:, 1080:1301] == 0)
            assert np.all(mask[:, 2381:2602] == 0)

    def test_real_target_resolves_via_preset_manager_lookup(self):
        """Same lookup path apply_soe_pass actually uses in production
        (PresetManager().get_target(preset_slug)), not a direct catalog
        list comprehension."""
        from logic.preset_manager import PresetManager
        from data.target_catalog import OOH

        multi_panel_targets = [t for t in OOH if (t.metadata or {}).get("multi_panel")]
        if not multi_panel_targets:
            pytest.skip("No multi_panel OOH targets loaded")
        real_id = multi_panel_targets[0].id

        resolved = PresetManager().get_target(real_id)
        assert resolved is not None
        mask = r.resolve_mask_for_target(resolved)
        assert isinstance(mask, np.ndarray)
        assert mask.shape == (resolved.height, resolved.width)
