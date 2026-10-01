# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_comment_gardener.py
PR-E.1 Layer 1 — Pure-Python unit tests for the comment gardener
classifier and scanner in `python/core/comment_gardener.py`.

What these tests cover (and why synthetic strings are appropriate)
-------------------------------------------------------------------
The contract under test here IS the Python classifier — given a
comment string, does it return the right CommentClass? Synthetic
strings are correct at this layer because the JSX→Python wire is
not what's being verified.

Layer 2 (test_comment_gardener_jsx.py — lands later in PR-E.1)
verifies that the manifest's `comment` field actually surfaces
through JSX-written manifests using a real captured fixture.
That's the synthetic-fixture-anti-pattern guard documented in
CLAUDE.md.

Layer 3 (test_comment_gardener_integration.py — lands in PR-E.1
commit 4) verifies the scanner is wired into the scrape flow.

This file covers Layer 1 only.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.comment_gardener import (  # noqa: E402
    CommentClass,
    CommentReport,
    LayerSummary,
    classify_comment,
    scan_comp,
)


# ── Test helpers ─────────────────────────────────────────────────


class _FakeLayer:
    """Minimal duck-type for ScrapeManifest layers — scan_comp uses
    only `index`, `name`, `comment` via getattr, so we don't need a
    real LayerModel here. This mirrors the synthetic-dict pattern
    test_bridge_jobs.py uses for the same Layer 1 reason."""

    def __init__(self, index: int, name: str,
                 comment=None):
        self.index = index
        self.name = name
        self.comment = comment


class _FakeManifest:
    def __init__(self, layers):
        self.layers = layers


# ── classify_comment: EMPTY ──────────────────────────────────────


class TestClassifyEmpty:
    """None, empty, and whitespace-only all classify as EMPTY."""

    def test_none_is_empty(self):
        assert classify_comment(None) is CommentClass.EMPTY

    def test_empty_string_is_empty(self):
        assert classify_comment("") is CommentClass.EMPTY

    def test_whitespace_only_is_empty(self):
        assert classify_comment("   ") is CommentClass.EMPTY
        assert classify_comment("\t\n  \r") is CommentClass.EMPTY


# ── classify_comment: DIMENSION_TAG (canonical) ──────────────────


class TestClassifyDimensionTag:
    """Strict full-match against `\\s*uid:<hex> #<TAG>\\s*` where TAG
    is in REGISTRY.all_ids()."""

    @pytest.mark.parametrize("comment", [
        "uid:abc123 #FILL",
        "uid:0 #CENTER",
        "uid:abcdef0123456789 #TOP",
        "uid:f0_a1_b2 #BOTTOM",   # underscores allowed in uid
        "uid:abc #DISC",
        "uid:abc #OVERLAY",
        "uid:abc #GUIDE",
        "uid:abc #PROTECT",
        "uid:abc #NULL",
        "uid:abc #UNCLASS",
    ])
    def test_canonical_tags_classify_as_dimension_tag(self, comment):
        assert classify_comment(comment) is CommentClass.DIMENSION_TAG

    def test_leading_trailing_whitespace_tolerated(self):
        """`\\s*` flanks let prior trailing-whitespace edits pass."""
        assert (classify_comment("  uid:abc #CENTER  ")
                is CommentClass.DIMENSION_TAG)
        assert (classify_comment("\tuid:abc #TOP\n")
                is CommentClass.DIMENSION_TAG)


class TestClassifyDimensionTagReversedOrder:
    """Issue #371 — a manually-typed `#TAG` hashtag that later gets a
    scrape-time uid stamp appended (`_ensureUidStamped`) ends up as
    `#<TAG> uid:<hex>` (tag, then uid) rather than the +TAG button's
    `uid:<hex> #<TAG>` order. Both orders are legitimate Dimension
    write paths and must classify identically."""

    @pytest.mark.parametrize("comment", [
        "#FILL uid:abc123",
        "#CENTER uid:0",
        "#TOP uid:abcdef0123456789",
        "#BOTTOM uid:f0_a1_b2",
    ])
    def test_reversed_order_classifies_as_dimension_tag(self, comment):
        assert classify_comment(comment) is CommentClass.DIMENSION_TAG

    def test_both_orders_agree_for_same_tag(self):
        assert (classify_comment("uid:1a0632 #TOP")
                is CommentClass.DIMENSION_TAG)
        assert (classify_comment("#TOP uid:1a0632")
                is CommentClass.DIMENSION_TAG)

    def test_reversed_order_whitespace_tolerated(self):
        assert (classify_comment("  #CENTER uid:abc  ")
                is CommentClass.DIMENSION_TAG)

    def test_reversed_order_plus_extra_content_is_mixed(self):
        """Reversed order doesn't relax the strict full-match — extra
        studio content still falls to MIXED, same as forward order."""
        assert (classify_comment("#TOP uid:abc render at 4K")
                is CommentClass.MIXED)

    def test_reversed_order_legacy_alias_still_legacy(self):
        """Order-independence must not change legacy/malformed
        resolution — both orders share the same registry lookup."""
        assert (classify_comment("#HERO uid:abc")
                is CommentClass.DIMENSION_TAG_LEGACY)

    def test_reversed_order_unknown_tag_still_malformed(self):
        assert (classify_comment("#NOTATAG uid:abc")
                is CommentClass.DIMENSION_TAG_MALFORMED)


# ── classify_comment: DIMENSION_TAG_LEGACY ───────────────────────


class TestClassifyLegacy:
    """Strict full-match BUT the tag is a legacy alias from the
    pre-v5.10/v6.0 vocabulary. Read paths normalise these via
    REGISTRY.normalize() — the classifier flags them so the user
    knows there's pre-canonical content."""

    @pytest.mark.parametrize("comment,alias", [
        ("uid:abc #TYPE", "TYPE"),
        ("uid:abc #LEGALS", "LEGALS"),
        ("uid:abc #BACKGROUND", "BACKGROUND"),
        ("uid:abc #KEYART", "KEYART"),
        # v6.0 aliases: old canonical names are now legacy
        ("uid:abc #HERO", "HERO"),
        ("uid:abc #TT", "TT"),
        ("uid:abc #LGL", "LGL"),
        ("uid:abc #BG", "BG"),
        ("uid:abc #LOGO", "LOGO"),
        ("uid:abc #SUP", "SUP"),
        ("uid:abc #BOXART", "BOXART"),
        ("uid:abc #CTA", "CTA"),
        ("uid:abc #BODY", "BODY"),
        ("uid:abc #ARTWORK", "ARTWORK"),
        ("uid:abc #ANIMATION", "ANIMATION"),
    ])
    def test_legacy_aliases_classify_as_legacy(self, comment, alias):
        assert (classify_comment(comment)
                is CommentClass.DIMENSION_TAG_LEGACY), alias


# ── classify_comment: DIMENSION_TAG_MALFORMED ────────────────────


class TestClassifyMalformed:
    """Strict full-match but `<TAG>` is not in the registry —
    typo, removed tag, hand-edit drift. Only the format is
    Dimension; the tag identity isn't."""

    def test_unknown_tag_classifies_as_malformed(self):
        assert (classify_comment("uid:abc #NOTATAG")
                is CommentClass.DIMENSION_TAG_MALFORMED)

    def test_typo_tag_classifies_as_malformed(self):
        # "HEROO" — close-but-wrong tag a user might hand-type
        assert (classify_comment("uid:abc #HEROO")
                is CommentClass.DIMENSION_TAG_MALFORMED)


# ── classify_comment: MIXED ──────────────────────────────────────


class TestClassifyStamped:
    """Bug F C3 — bare uid stamp emitted by scrape-time stamping.
    No tag, no foreign content. Banner must stay quiet on these
    (locked decision Q5)."""

    def test_short_hex_uid(self):
        assert (classify_comment("uid:abc123")
                is CommentClass.DIMENSION_STAMPED)

    def test_realistic_timestamp_uid(self):
        """Sovereign_Core's generateUID emits
        `<11-char-hex-ms>_<6-char-hex-rand>`."""
        assert (classify_comment("uid:19dda20b2be_78889d")
                is CommentClass.DIMENSION_STAMPED)

    def test_with_surrounding_whitespace(self):
        """Leading/trailing whitespace tolerated — the regex uses
        `\\s*` on both ends."""
        assert (classify_comment("  uid:abc123  ")
                is CommentClass.DIMENSION_STAMPED)

    def test_uid_with_underscores_is_stamped(self):
        """Generated UIDs include `_` as a separator (ts_random)."""
        assert (classify_comment("uid:19dda20b2be_78889d")
                is CommentClass.DIMENSION_STAMPED)

    def test_stamp_plus_extra_text_falls_to_mixed(self):
        """The classifier is strict — stamp + any non-whitespace
        text is MIXED, not STAMPED. Studio note preservation: the
        stamper appends to existing comment content, so a layer
        that had `"render at 4K"` becomes `"render at 4K uid:<hex>"`,
        which classifies as MIXED — and that's intentional. The
        artist note still warrants the warning banner."""
        assert (classify_comment("render at 4K uid:abc123")
                is CommentClass.MIXED)
        assert (classify_comment("uid:abc123 render at 4K")
                is CommentClass.MIXED)

    def test_uppercase_hex_not_stamped(self):
        """Stamp regex is `[a-f0-9_]+` — uppercase hex falls
        through. Defensive against any drift in generateUID's
        casing."""
        assert (classify_comment("uid:ABC123")
                is not CommentClass.DIMENSION_STAMPED)


class TestClassifyMixed:
    """Comment contains a `uid:` token AND extra non-whitespace
    content beyond the strict tag pattern. The studio's content
    coexists with our marker."""

    def test_dimension_tag_plus_trailing_note(self):
        """uid:abc #HERO followed by a render note."""
        assert (classify_comment("uid:abc #HERO render at 4K")
                is CommentClass.MIXED)

    def test_dimension_tag_plus_leading_note(self):
        """Render note followed by uid:abc #HERO."""
        assert (classify_comment("render at 4K uid:abc #HERO")
                is CommentClass.MIXED)

    def test_uid_alone_no_hashtag_is_stamped(self):
        """Has `uid:` only, no `#TAG`, no foreign content. Bug F
        (2026-04-29) introduced the DIMENSION_STAMPED class for
        this case — scrape stamps every layer with a bare uid
        before any tag-write, and the banner needs to stay quiet
        on those. Pre-Bug-F this classified as MIXED, which would
        light up the banner on every layer of every comp post-B.
        Locked decision Q4."""
        assert (classify_comment("uid:abc123")
                is CommentClass.DIMENSION_STAMPED)

    def test_uid_plus_two_hashtags_is_mixed(self):
        """Two #TAG-shaped tokens — full-match fails, falls to
        MIXED via the uid: token branch."""
        assert (classify_comment("uid:abc #HERO #LOGO")
                is CommentClass.MIXED)

    def test_uid_in_middle_of_content_is_mixed(self):
        """uid: token surrounded by non-whitespace on both sides."""
        assert (classify_comment("note: uid:abc, status: ok")
                is CommentClass.MIXED)


# ── classify_comment: FOREIGN ────────────────────────────────────


class TestClassifyForeign:
    """Non-empty content with no `uid:` token. The studio's
    content sitting alone in the field. Tag-write would clobber."""

    @pytest.mark.parametrize("comment", [
        "render at 4K",
        "client revision 2",
        "asset_id_2847",
        "color management: rec709",
        "DAM://library/heroes/2024/featured/v3.psd",
        "broken in AE 2024.1, fix scheduled",
        "#HERO",                  # bare hashtag, no uid:
        "#HERO #LOGO",            # multiple hashtags, no uid:
        "Some sentence ending in a period.",
        "1234567890",
    ])
    def test_no_uid_token_classifies_as_foreign(self, comment):
        assert (classify_comment(comment) is CommentClass.FOREIGN), (
            f"expected FOREIGN for {comment!r}"
        )

    def test_lookalike_uid_string_classifies_as_foreign(self):
        """Edge case: comment uses the literal text "uid" without
        the colon — still FOREIGN because there's no `uid:` token.
        Conservative classifier shouldn't be fooled by near-misses."""
        assert classify_comment("note: uid abc") is CommentClass.FOREIGN
        assert classify_comment("uid_abc") is CommentClass.FOREIGN

    def test_negative_proves_test_would_have_caught_pre_fix_drop(self):
        """Same shape as PR #45 / PR #48 negative cases. If a
        future regression made `classify_comment` more permissive
        — e.g. accepting `#HERO` alone as DIMENSION_TAG — this
        test fails because a bare hashtag without the `uid:`
        prefix MUST classify as FOREIGN."""
        assert classify_comment("#HERO") is CommentClass.FOREIGN
        assert classify_comment("#HERO ") is CommentClass.FOREIGN
        assert classify_comment(" #HERO") is CommentClass.FOREIGN


# ── classify_comment: edge cases ─────────────────────────────────


class TestClassifyEdgeCases:
    """Boundary inputs that have surfaced bugs in similar regex
    classifiers historically."""

    def test_uid_no_space_before_hashtag_is_mixed(self):
        """`uid:abc#HERO` — missing the literal space between uid
        and the hashtag. Strict regex requires the space, so this
        falls to MIXED via the uid: token branch.
        """
        assert (classify_comment("uid:abc#HERO")
                is CommentClass.MIXED)

    def test_uid_extra_space_before_hashtag_is_mixed(self):
        """Two spaces between uid and #TAG. Strict regex requires
        exactly one. Falls to MIXED."""
        assert (classify_comment("uid:abc  #HERO")
                is CommentClass.MIXED)

    def test_lowercase_tag_is_mixed(self):
        """`#hero` — lowercase tag. Doesn't full-match the strict
        pattern (which requires `[A-Z]+`). Has uid: → MIXED."""
        assert (classify_comment("uid:abc #hero")
                is CommentClass.MIXED)

    def test_empty_uid_value_is_mixed(self):
        """`uid: #HERO` — colon present but no hex value. Strict
        regex rejects (uid value must be `[a-f0-9_]+`). Has uid:
        token → MIXED."""
        assert (classify_comment("uid: #HERO")
                is CommentClass.MIXED)


# ── scan_comp: report shape ──────────────────────────────────────


class TestScanComp:
    """Walks a manifest and assembles a CommentReport. The Layer 1
    coverage uses synthetic _FakeLayer / _FakeManifest because
    we're testing the scanner's iteration + bookkeeping, not the
    manifest schema itself."""

    def test_empty_manifest_returns_empty_report(self):
        r = scan_comp(_FakeManifest([]))
        assert isinstance(r, CommentReport)
        assert r.total_layers == 0
        assert r.foreign_count == 0
        assert r.has_warnings is False

    def test_clean_dimension_comp_no_warnings(self):
        """All layers carry well-formed Dimension tags. Banner
        should NOT fire — has_warnings is False.
        v6.0: uses canonical names CENTER and FILL (not HERO/BG)."""
        layers = [
            _FakeLayer(1, "TitleLayer", "uid:abc #CENTER"),
            _FakeLayer(2, "BgLayer", "uid:def #FILL"),
            _FakeLayer(3, "EmptyLayer", None),
        ]
        r = scan_comp(_FakeManifest(layers))
        assert r.total_layers == 3
        assert r.foreign_count == 0
        assert r.malformed_count == 0
        assert r.legacy_count == 0
        assert r.mixed_count == 0
        assert r.has_warnings is False
        assert len(r.by_class[CommentClass.DIMENSION_TAG]) == 2
        assert len(r.by_class[CommentClass.EMPTY]) == 1

    def test_mixed_real_world_scrape_counts(self):
        """Realistic scrape with a mix of all six classes.

        NOTE: uid values must be valid hex ([a-f0-9_]+) for the
        strict regex to match. "ghi" looks like a placeholder but
        is NOT hex — the classifier correctly rejects it and
        falls to MIXED via the uid: token branch.
        v6.0: HERO and BG are now legacy aliases → legacy_count = 2."""
        layers = [
            _FakeLayer(1, "TitleLayer", "uid:abc #CENTER"),
            _FakeLayer(2, "Legacy", "uid:def #KEYART"),       # LEGACY
            _FakeLayer(3, "Typo", "uid:abc #HEROO"),          # MALFORMED (hex uid + unknown tag)
            _FakeLayer(4, "Empty", None),
            _FakeLayer(5, "Foreign1", "render at 4K"),
            _FakeLayer(6, "Foreign2", "asset_id_2847"),
            _FakeLayer(7, "Mixed", "uid:abc #CENTER render at 4K"),
        ]
        r = scan_comp(_FakeManifest(layers))
        assert r.total_layers == 7
        assert r.foreign_count == 2
        assert r.malformed_count == 1
        assert r.legacy_count == 1
        assert r.mixed_count == 1
        assert r.has_warnings is True
        assert len(r.by_class[CommentClass.DIMENSION_TAG]) == 1
        assert len(r.by_class[CommentClass.EMPTY]) == 1

    def test_clean_comp_post_stamping_stays_quiet(self):
        """Bug F C3 — once scrape stamps every layer with a bare
        `uid:<hex>`, a comp with NO foreign content should keep
        the banner quiet. Pre-Bug-F this would have classified
        every stamped layer as MIXED and lit up the banner on
        every comp. Locked decision Q5.

        Mix of EMPTY (none ever scraped or comment cleared),
        DIMENSION_STAMPED (scraped but untagged), DIMENSION_TAG
        (scraped + tagged) — the realistic post-fix steady state.
        v6.0: uses canonical CENTER (not HERO)."""
        layers = [
            _FakeLayer(1, "Untagged1", "uid:abc123"),
            _FakeLayer(2, "Untagged2", "uid:def456"),
            _FakeLayer(3, "Tagged", "uid:abc789 #CENTER"),
            _FakeLayer(4, "Empty", None),
        ]
        r = scan_comp(_FakeManifest(layers))
        assert r.total_layers == 4
        assert r.stamped_count == 2
        assert r.foreign_count == 0
        assert r.malformed_count == 0
        assert r.mixed_count == 0
        assert r.has_warnings is False
        assert len(r.by_class[CommentClass.DIMENSION_STAMPED]) == 2
        assert len(r.by_class[CommentClass.DIMENSION_TAG]) == 1
        assert len(r.by_class[CommentClass.EMPTY]) == 1

    def test_stamped_count_does_not_trigger_warnings(self):
        """A pure-stamped comp (no tags applied yet) keeps the
        banner quiet."""
        layers = [
            _FakeLayer(i, f"L{i}", "uid:" + ("a" * 11) + "_" + ("b" * 6))
            for i in range(1, 11)
        ]
        r = scan_comp(_FakeManifest(layers))
        assert r.stamped_count == 10
        assert r.foreign_count == 0
        assert r.has_warnings is False

    def test_has_warnings_fires_on_any_problem_class(self):
        """has_warnings is True when ANY of FOREIGN, MALFORMED,
        or MIXED is present. LEGACY alone does NOT trigger
        warnings — legacy aliases are recognised tags, just
        pre-canonical."""

        # LEGACY only — has_warnings False (recognised content)
        r = scan_comp(_FakeManifest([
            _FakeLayer(1, "L", "uid:abc #TYPE"),
        ]))
        assert r.has_warnings is False
        assert r.legacy_count == 1

        # FOREIGN — has_warnings True
        r = scan_comp(_FakeManifest([
            _FakeLayer(1, "L", "render note"),
        ]))
        assert r.has_warnings is True

        # MALFORMED — has_warnings True
        r = scan_comp(_FakeManifest([
            _FakeLayer(1, "L", "uid:abc #UNKNOWN"),
        ]))
        assert r.has_warnings is True

        # MIXED — has_warnings True
        r = scan_comp(_FakeManifest([
            _FakeLayer(1, "L", "uid:abc #HERO and notes"),
        ]))
        assert r.has_warnings is True


# ── LayerSummary: preview truncation ─────────────────────────────


class TestLayerSummary:
    def test_preview_short_comment_unchanged(self):
        s = LayerSummary.from_layer(1, "L", "short note")
        assert s.comment_preview == "short note"

    def test_preview_long_comment_truncated_with_ellipsis(self):
        long_comment = "A" * 100
        s = LayerSummary.from_layer(1, "L", long_comment)
        assert len(s.comment_preview) == 60
        assert s.comment_preview.endswith("...")

    def test_preview_exact_60_chars_unchanged(self):
        exact = "A" * 60
        s = LayerSummary.from_layer(1, "L", exact)
        assert s.comment_preview == exact
        assert "..." not in s.comment_preview

    def test_preview_none_becomes_empty(self):
        s = LayerSummary.from_layer(1, "L", None)
        assert s.comment_preview == ""


# ── Performance smoke test ───────────────────────────────────────


class TestScannerPerformance:
    """Scope doc budget: 200 layers in <100ms; 500 layers in <250ms.
    Pure-Python regex fullmatch + set lookup — should be well
    under budget on any modern machine. Test fails loudly if
    something inadvertently makes the scan O(n²) or slower."""

    def test_500_layers_under_budget(self):
        layers = []
        for i in range(500):
            # Mix of all six classes to exercise every branch.
            cls_pick = i % 6
            if cls_pick == 0:
                comment = f"uid:abc{i:x} #HERO"
            elif cls_pick == 1:
                comment = f"uid:abc{i:x} #TYPE"          # LEGACY
            elif cls_pick == 2:
                comment = f"uid:abc{i:x} #UNKNOWN"       # MALFORMED
            elif cls_pick == 3:
                comment = None                            # EMPTY
            elif cls_pick == 4:
                comment = f"render note {i}"             # FOREIGN
            else:
                comment = f"uid:abc{i:x} #HERO note"     # MIXED
            layers.append(_FakeLayer(i, f"Layer{i}", comment))

        start = time.monotonic()
        report = scan_comp(_FakeManifest(layers))
        elapsed_ms = (time.monotonic() - start) * 1000

        assert report.total_layers == 500
        assert elapsed_ms < 250, (
            f"500-layer scan took {elapsed_ms:.1f}ms — budget is 250ms. "
            f"Investigate before shipping."
        )
