# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_comment_directives.py

Covers `core/comment_directives.py` and its integration with the Comment
Gardener.

Grammar: `docs/knowledge/comment-directive-grammar.md`.

The two test classes that matter most are not the parsing ones:

- `TestGardenerClassification` pins the behaviour the whole design exists
  for. Before PR-V1, a layer commented `variant:vertical` classified as
  FOREIGN, which raised the tagging banner AND made
  `surveyor.survey_manifest` skip heuristic classification for that layer
  (`foreign_indices`). The artist asks for a variant; the layer silently
  loses its tag. If the strip is ever removed, those tests fail.

- `TestReservedWordCollisions` checks the directive keywords against the
  natural-keyword maps in BOTH `surveyor.py` and `SovCore_Layer.jsx`, and
  against the tag registry. Those two keyword maps are hand-synced with no
  generator and no test (2026-09-01 audit, finding B7), so this parses the
  JSX rather than trusting the Python copy to represent it.
"""

from __future__ import annotations

import os
import re

import pytest

from core.comment_directives import (
    ALL_BUCKETS,
    RESERVED_WORDS,
    CommentDirectives,
    has_directives,
    parse_directives,
    strip_directives,
)
from core.comment_gardener import CommentClass, classify_comment
from core.orientation import Orientation

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_JSX_LAYER = os.path.join(
    _REPO_ROOT, "Scripts", "Dimension_Assets", "SovCore_Layer.jsx"
)


class TestVariantParsing:
    def test_single_bucket(self):
        assert parse_directives("variant:vertical").variants == {"VERTICAL"}

    def test_bucket_list(self):
        d = parse_directives("variant:horizontal,square")
        assert d.variants == {"HORIZONTAL", "SQUARE"}

    def test_negation(self):
        d = parse_directives("variant:!vertical")
        assert d.variants == {"HORIZONTAL", "SQUARE"}

    def test_case_insensitive(self):
        assert parse_directives("VARIANT:VERTICAL").variants == {"VERTICAL"}
        assert parse_directives("Variant:Vertical").variants == {"VERTICAL"}

    def test_absent_means_every_bucket(self):
        d = parse_directives("uid:a1b2 #TOP")
        assert d.variants is None
        assert all(d.renders_in(o) for o in Orientation)

    def test_multiple_positives_union(self):
        d = parse_directives("variant:vertical variant:square")
        assert d.variants == {"VERTICAL", "SQUARE"}

    def test_multiple_negations_subtract(self):
        """The case that broke the first implementation.

        Unioning complements — the obvious reading of "combine them" —
        turns two exclusions into (all−H) ∪ (all−V) = everything, i.e.
        two exclusions that exclude nothing, silently inverting what the
        artist wrote.
        """
        d = parse_directives("variant:!horizontal variant:!vertical")
        assert d.variants == {"SQUARE"}

    def test_excluding_everything_warns_and_renders_nowhere(self):
        d = parse_directives(
            "variant:!horizontal variant:!square variant:!vertical"
        )
        assert d.variants == set()
        assert not any(d.renders_in(o) for o in Orientation)
        assert any("no target" in w for w in d.warnings)

    def test_mixed_include_and_exclude_warns(self):
        d = parse_directives("variant:horizontal,square variant:!square")
        assert d.variants == {"HORIZONTAL"}
        assert any("include and exclude" in w for w in d.warnings)


class TestNudgeParsing:
    def test_bucket_scoped(self):
        d = parse_directives("nudge:-40,20@vertical")
        assert d.nudges == {"VERTICAL": (-40.0, 20.0)}

    def test_all_buckets(self):
        d = parse_directives("nudge:0,-120")
        assert d.nudges == {ALL_BUCKETS: (0.0, -120.0)}

    def test_floats_and_signs(self):
        d = parse_directives("nudge:+12.5,-0.25@square")
        assert d.nudges == {"SQUARE": (12.5, -0.25)}

    def test_bucket_specific_wins_over_all(self):
        d = parse_directives("nudge:0,-120 nudge:0,-40@vertical")
        assert d.nudge_for(Orientation.VERTICAL) == (0.0, -40.0)
        assert d.nudge_for(Orientation.SQUARE) == (0.0, -120.0)

    def test_offsets_do_not_sum(self):
        """`nudge:0,-120 nudge:0,-40@vertical` means −40 on vertical, not
        −160. Neither line says −160."""
        d = parse_directives("nudge:0,-120 nudge:0,-40@vertical")
        assert d.nudge_for(Orientation.VERTICAL) == (0.0, -40.0)

    def test_duplicate_same_bucket_last_wins_with_warning(self):
        d = parse_directives("nudge:10,10@vertical nudge:20,20@vertical")
        assert d.nudges == {"VERTICAL": (20.0, 20.0)}
        assert any("last one wins" in w for w in d.warnings)

    def test_identical_duplicate_does_not_warn(self):
        d = parse_directives("nudge:10,10@vertical nudge:10,10@vertical")
        assert d.nudges == {"VERTICAL": (10.0, 10.0)}
        assert not d.warnings

    def test_no_nudge_is_zero_offset(self):
        d = parse_directives("variant:vertical")
        assert d.nudge_for(Orientation.VERTICAL) == (0.0, 0.0)

    @pytest.mark.parametrize("as_enum", [True, False])
    def test_lookup_accepts_enum_member_or_plain_string(self, as_enum):
        """`Orientation` is a `(str, Enum)`, and on Python 3.11+ `str()` of
        a member gives `'Orientation.VERTICAL'`, not `'VERTICAL'`. The
        first implementation used `str(bucket)` and so matched nothing
        when handed a real enum member — every directive would have
        silently stopped firing the moment PR-V2 passed one in. Both call
        shapes must work."""
        d = parse_directives("variant:vertical nudge:-40,20@vertical")
        key = Orientation.VERTICAL if as_enum else "VERTICAL"
        assert d.renders_in(key) is True
        assert d.nudge_for(key) == (-40.0, 20.0)
        other = Orientation.HORIZONTAL if as_enum else "HORIZONTAL"
        assert d.renders_in(other) is False
        assert d.nudge_for(other) == (0.0, 0.0)


class TestMalformedInput:
    """A typo must never fail a conform, and must never be mistaken for
    prose the gardener would protect while the artist thinks it is
    working."""

    @pytest.mark.parametrize(
        "comment", ["nudge:abc", "variant:diagonal", "nudge:10", "variant:"]
    )
    def test_malformed_is_ignored_warned_and_not_stripped(self, comment):
        d = parse_directives(comment)
        assert d.is_empty
        assert d.warnings, f"{comment!r} should have produced a warning"
        assert strip_directives(comment) == comment

    def test_never_raises_on_arbitrary_text(self):
        for junk in [
            "",
            "   ",
            "变体:垂直",
            "variant::::",
            "nudge:,,,",
            "#" * 500,
            "variant:vertical" * 50,
        ]:
            parse_directives(junk)  # must not raise

    def test_none_is_safe(self):
        d = parse_directives(None)
        assert d.is_empty
        assert strip_directives(None) == ""
        assert has_directives(None) is False


class TestNoFalsePositives:
    """Directives must not be found where the artist did not write one."""

    @pytest.mark.parametrize(
        "comment",
        [
            "#variant:vertical",  # hashtag namespace belongs to tags
            "myvariant:vertical",  # somebody else's identifier
            "hold for vertical delivery",  # prose containing a bucket word
            "see variant notes in the brief",
            "nudged the title left",
        ],
    )
    def test_not_treated_as_a_directive(self, comment):
        assert has_directives(comment) is False
        assert parse_directives(comment).is_empty
        assert strip_directives(comment) == comment


class TestStripDirectives:
    def test_leaves_tag_pattern_intact(self):
        """The strip must leave a comment the gardener's strict
        full-match tag regex still accepts — including the whitespace."""
        assert strip_directives("uid:a1b2 #TOP variant:vertical") == "uid:a1b2 #TOP"
        assert strip_directives("uid:a1b2  #TOP   nudge:0,-40") == "uid:a1b2 #TOP"

    def test_preserves_studio_content(self):
        assert strip_directives("DAM-88213 variant:vertical") == "DAM-88213"

    def test_no_directives_is_identity_modulo_whitespace(self):
        for c in ["uid:a1b2 #TOP", "DAM-88213", "render note for colorist"]:
            assert strip_directives(c) == c


class TestGardenerClassification:
    """The behaviour the whole strip-first design exists to protect."""

    @pytest.mark.parametrize(
        "comment,expected",
        [
            ("variant:vertical", CommentClass.DIMENSION_DIRECTIVE),
            ("nudge:-40,20@vertical", CommentClass.DIMENSION_DIRECTIVE),
            ("uid:a1b2 #TOP variant:vertical", CommentClass.DIMENSION_TAG),
            ("uid:a1b2 #TYPE variant:vertical", CommentClass.DIMENSION_TAG_LEGACY),
            ("uid:a1b2 #NOPE variant:vertical", CommentClass.DIMENSION_TAG_MALFORMED),
            ("uid:a1b2 variant:vertical", CommentClass.DIMENSION_STAMPED),
            ("DAM-88213 variant:vertical", CommentClass.FOREIGN),
            ("uid:a1b2 render note variant:vertical", CommentClass.MIXED),
            ("variant:diagonal", CommentClass.FOREIGN),
        ],
    )
    def test_classification(self, comment, expected):
        assert classify_comment(comment) is expected

    def test_directive_only_comment_does_not_read_as_studio_content(self):
        """The regression this prevents: FOREIGN here would raise the
        tagging banner and make surveyor.survey_manifest skip heuristic
        classification for the layer, so asking for a variant would cost
        the layer its tag with no error anywhere."""
        assert classify_comment("variant:vertical") is not CommentClass.FOREIGN

    def test_directive_beside_studio_content_still_protects_it(self):
        """A directive buys no exemption for the prose around it."""
        assert classify_comment("DAM-88213 variant:vertical") is CommentClass.FOREIGN

    @pytest.mark.parametrize(
        "comment,expected",
        [
            ("", CommentClass.EMPTY),
            ("   ", CommentClass.EMPTY),
            ("uid:a1b2 #TOP", CommentClass.DIMENSION_TAG),
            ("uid:a1b2 #TYPE", CommentClass.DIMENSION_TAG_LEGACY),
            ("uid:a1b2 #NOPE", CommentClass.DIMENSION_TAG_MALFORMED),
            ("uid:a1b2", CommentClass.DIMENSION_STAMPED),
            ("DAM-88213", CommentClass.FOREIGN),
            ("uid:a1b2 render note", CommentClass.MIXED),
        ],
    )
    def test_directiveless_comments_classify_exactly_as_before(
        self, comment, expected
    ):
        """Backward compatibility, asserted rather than assumed: a comment
        with no directives strips to itself, so PR-V1 cannot have moved
        any pre-existing classification."""
        assert classify_comment(comment) is expected


class TestReservedWordCollisions:
    """Directive keywords must not collide with anything else that reads
    the comment field."""

    def _jsx_keyword_map(self) -> set:
        with open(_JSX_LAYER, "r", encoding="utf-8") as f:
            src = f.read()
        m = re.search(r"var keywordMap = \{(.*?)\};", src, re.DOTALL)
        assert m, (
            "keywordMap not found in SovCore_Layer.jsx — if it moved, fix "
            "this test rather than deleting it; it is the only check that "
            "the JSX keyword map does not claim a directive word."
        )
        return {k.lower() for k in re.findall(r'"([A-Z0-9_]+)"\s*:', m.group(1))}

    def test_no_collision_with_python_natural_keywords(self):
        from core.surveyor import _NATURAL_COMMENT_KEYWORDS

        clash = RESERVED_WORDS & {k.lower() for k in _NATURAL_COMMENT_KEYWORDS}
        assert not clash, (
            f"Directive words also mean a tag in surveyor.py: {sorted(clash)}. "
            "A layer commented with one would be tagged as a side effect."
        )

    def test_no_collision_with_jsx_natural_keywords(self):
        clash = RESERVED_WORDS & self._jsx_keyword_map()
        assert not clash, (
            f"Directive words also mean a tag in SovCore_Layer.jsx: "
            f"{sorted(clash)}."
        )

    def test_python_and_jsx_keyword_maps_agree(self):
        """Not this PR's bug, but this PR depends on both maps: a word
        safe in one and claimed in the other is still a collision. Finding
        B7 — these are hand-synced with no generator."""
        from core.surveyor import _NATURAL_COMMENT_KEYWORDS

        py = {k.lower() for k in _NATURAL_COMMENT_KEYWORDS}
        assert py == self._jsx_keyword_map(), (
            "surveyor._NATURAL_COMMENT_KEYWORDS and SovCore_Layer.jsx's "
            "keywordMap have drifted. They are hand-synced; fix both."
        )

    def test_no_collision_with_tag_registry(self):
        from core.tag_registry import REGISTRY

        known = {t.lower() for t in REGISTRY.all_ids() | REGISTRY.all_aliases()}
        clash = RESERVED_WORDS & known
        assert not clash, f"Directive words collide with tag ids/aliases: {sorted(clash)}"

    def test_reserved_words_are_documented(self):
        doc = os.path.join(
            _REPO_ROOT, "docs", "knowledge", "comment-directive-grammar.md"
        )
        with open(doc, "r", encoding="utf-8") as f:
            text = f.read()
        for word in RESERVED_WORDS:
            assert word in text, (
                f"{word!r} is reserved in code but absent from the grammar doc."
            )

    def test_bucket_names_track_orientation(self):
        for o in Orientation:
            assert o.value.lower() in RESERVED_WORDS


class TestPurity:
    def test_result_is_frozen(self):
        d = parse_directives("variant:vertical")
        with pytest.raises(Exception):
            d.variants = set()  # type: ignore[misc]

    def test_deterministic(self):
        c = "uid:a1 #TOP variant:horizontal,square nudge:-40,20@vertical"
        first = parse_directives(c)
        for _ in range(20):
            d = parse_directives(c)
            assert d.variants == first.variants
            assert d.nudges == first.nudges

    def test_does_not_mutate_input(self):
        c = "uid:a1b2 #TOP variant:vertical"
        parse_directives(c)
        strip_directives(c)
        assert c == "uid:a1b2 #TOP variant:vertical"

    def test_empty_directives_object_is_permissive(self):
        """A layer with no directives renders everywhere and moves
        nowhere — the default must never make a layer disappear."""
        d = CommentDirectives()
        assert d.is_empty
        assert all(d.renders_in(o) for o in Orientation)
        assert all(d.nudge_for(o) == (0.0, 0.0) for o in Orientation)
