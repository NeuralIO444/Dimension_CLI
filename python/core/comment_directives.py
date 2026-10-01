# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/comment_directives.py
Parser for artist directives in the AE layer comment field.

Reads `variant:` and `nudge:` directives out of a layer comment and
returns them as data. Pure function: no I/O, no manifest access, no
knowledge of the current conform target, and it never writes to a
comment.

Full grammar, precedence and reserved words:
`docs/knowledge/comment-directive-grammar.md`. Read that before
extending this module — the comment field has five tenants already and
the rules for adding a sixth are written down there, not here.

Quick shape:

    variant:vertical                only render on VERTICAL targets
    variant:horizontal,square       either of those
    variant:!vertical               everywhere except VERTICAL
    nudge:-40,20@vertical           offset x-40 y+20, VERTICAL only
    nudge:0,-120                    offset on every bucket

The load-bearing property of this module is `strip_directives()`, not
the parse itself. The Comment Gardener classifies a comment by what
REMAINS after Dimension's own syntax is removed; without the strip, a
layer commented `variant:vertical` classifies as FOREIGN, which raises
the tagging banner AND makes `surveyor.survey_manifest` skip heuristic
classification for that layer — so asking for a variant would silently
cost the layer its tag. See the grammar doc, § 1.

Nothing in the conform pipeline reads these yet. PR-V2 wires `variant`;
PR-N1 wires `nudge`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

# Bucket names accepted in directives. Mirrors `core.orientation.Orientation`
# member names, lowercased. Imported rather than re-declared so a new bucket
# cannot exist in one module and not the other.
from core.orientation import Orientation

_BUCKET_NAMES: Dict[str, str] = {o.value.lower(): o.value for o in Orientation}

# Reserved words — see the grammar doc § 4. A test asserts none of these
# collide with the natural-keyword maps or the tag registry.
RESERVED_WORDS: frozenset = frozenset(
    {"variant", "nudge"} | set(_BUCKET_NAMES.keys())
)

# ── Directive patterns ────────────────────────────────────────────────────
#
# Both are deliberately strict about their VALUE half. A loose pattern that
# matched `variant:` followed by anything would swallow the studio prose after
# it (`variant: see brief` would eat "see brief"), and stripped text is text
# the gardener no longer protects. So a directive only matches — and only gets
# stripped — when its value is fully well-formed. A malformed one stays in the
# comment as foreign content and is reported as a warning, per the grammar
# doc § 3.
#
# `(?<![\w#])` stops `#variant:x` or `myvariant:x` matching: a hashtag is the
# tag namespace and a word-prefixed token is somebody else's identifier.

_NUMBER = r"[+-]?\d+(?:\.\d+)?"
_BUCKET_ALT = "|".join(sorted(_BUCKET_NAMES.keys()))

_VARIANT_RE = re.compile(
    r"(?<![\w#])variant:(?P<neg>!?)(?P<buckets>"
    rf"(?:{_BUCKET_ALT})(?:,(?:{_BUCKET_ALT}))*)(?![\w])",
    re.IGNORECASE,
)

_NUDGE_RE = re.compile(
    rf"(?<![\w#])nudge:(?P<x>{_NUMBER}),(?P<y>{_NUMBER})"
    rf"(?:@(?P<bucket>{_BUCKET_ALT}))?(?![\w])",
    re.IGNORECASE,
)

# Recognises a directive KEYWORD that failed to parse as a well-formed
# directive — `nudge:abc`, `variant:diagonal`. Used only to warn; these are
# never stripped.
_MALFORMED_RE = re.compile(r"(?<![\w#])(variant|nudge):(?P<value>\S*)", re.IGNORECASE)

# Sentinel bucket key meaning "applies to every bucket".
ALL_BUCKETS = "*"


def _bucket_key(bucket) -> str:
    """Normalise an `Orientation` member or a string to the bucket key.

    `Orientation` is a `(str, Enum)`, and on Python 3.11+ `str()` of such
    a member returns `'Orientation.VERTICAL'`, not `'VERTICAL'` — so a
    plain `str(bucket)` lookup misses every dict keyed by `.value`, and
    every directive silently stops matching. Caught by
    `test_comment_directives.py::TestNudgeParsing`; the same trap applies
    to any future dict keyed by these buckets.
    """
    return bucket.value if isinstance(bucket, Orientation) else str(bucket)


@dataclass(frozen=True)
class CommentDirectives:
    """Parsed directives from one layer comment.

    `variants` is None when the layer carries no `variant:` directive,
    meaning "render on every bucket" — today's behaviour for every layer.
    An EMPTY set is different and legal: `variant:!horizontal
    variant:!square variant:!vertical` excludes everything, and the
    caller should surface that as the artist error it probably is rather
    than have the parser guess.

    `nudges` maps a bucket name (or `ALL_BUCKETS`) to an (x, y) offset in
    target-comp pixels.

    `warnings` carries human-readable notes for the caller to surface —
    repeats, conflicts, malformed directives. The parser never raises on
    bad input: a typo in a layer comment must not fail a conform.
    """

    variants: Optional[Set[str]] = None
    nudges: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def renders_in(self, bucket) -> bool:
        """Should a layer carrying these directives render on `bucket`?

        `bucket` is an `Orientation` member or its string form. No
        directive means yes — the default must stay "render", so an
        unparsed or absent comment can never make a layer disappear.
        """
        if self.variants is None:
            return True
        return _bucket_key(bucket) in self.variants

    def nudge_for(self, bucket) -> Tuple[float, float]:
        """Offset to apply on `bucket`. (0.0, 0.0) when none applies.

        A bucket-specific nudge wins over an all-buckets one; they do not
        sum. Summing would make `nudge:0,-120 nudge:0,-40@vertical` mean
        −160 on vertical, which is not what either line says.
        """
        key = _bucket_key(bucket)
        if key in self.nudges:
            return self.nudges[key]
        return self.nudges.get(ALL_BUCKETS, (0.0, 0.0))

    @property
    def is_empty(self) -> bool:
        return self.variants is None and not self.nudges


def parse_directives(comment: Optional[str]) -> CommentDirectives:
    """Extract directives from a layer comment.

    Never raises. Unknown or malformed input yields warnings, not
    exceptions — this runs on artist-typed free text.
    """
    if not comment or not comment.strip():
        return CommentDirectives()

    warnings: List[str] = []

    variants = _parse_variants(comment, warnings)
    nudges = _parse_nudges(comment, warnings)
    _warn_malformed(comment, warnings)

    return CommentDirectives(variants=variants, nudges=nudges, warnings=warnings)


def _parse_variants(comment: str, warnings: List[str]) -> Optional[Set[str]]:
    matches = list(_VARIANT_RE.finditer(comment))
    if not matches:
        return None

    all_buckets = {o.value for o in Orientation}
    included: Set[str] = set()
    excluded: Set[str] = set()

    for m in matches:
        named = {
            _BUCKET_NAMES[b.strip().lower()]
            for b in m.group("buckets").split(",")
            if b.strip()
        }
        if m.group("neg"):
            excluded |= named
        else:
            included |= named

    # Positives union; negatives subtract from whatever the positives
    # selected (or from everything, when there are no positives).
    #
    # Unioning the COMPLEMENTS instead — the obvious reading of "combine
    # them" — inverts the artist's meaning the moment there is more than
    # one negation: `variant:!horizontal variant:!vertical` would become
    # (all−H) ∪ (all−V) = everything, i.e. two exclusions that exclude
    # nothing. Subtracting is the only reading where each directive still
    # means what it says on its own.
    selected = (included or all_buckets) - excluded

    if included and excluded:
        warnings.append(
            "`variant:` mixes include and exclude directives — includes are "
            f"combined first, then excludes removed (result: "
            f"{', '.join(sorted(selected)) or 'nothing'})."
        )
    if not selected:
        warnings.append(
            "`variant:` directives leave no orientation — this layer would "
            "render on no target."
        )
    return selected


def _parse_nudges(comment: str, warnings: List[str]) -> Dict[str, Tuple[float, float]]:
    nudges: Dict[str, Tuple[float, float]] = {}

    for m in _NUDGE_RE.finditer(comment):
        raw_bucket = m.group("bucket")
        key = _BUCKET_NAMES[raw_bucket.lower()] if raw_bucket else ALL_BUCKETS
        offset = (float(m.group("x")), float(m.group("y")))

        if key in nudges and nudges[key] != offset:
            scope = "all targets" if key == ALL_BUCKETS else key
            warnings.append(
                f"two different `nudge:` directives for {scope} "
                f"({nudges[key]} then {offset}) — the last one wins."
            )
        nudges[key] = offset

    return nudges


def _warn_malformed(comment: str, warnings: List[str]) -> None:
    """Flag `variant:`/`nudge:` tokens that did not parse.

    Found by taking every directive-keyword occurrence and subtracting the
    ones the strict patterns claimed. What is left is a typo the artist
    believes is doing something.
    """
    claimed = {
        m.start()
        for pattern in (_VARIANT_RE, _NUDGE_RE)
        for m in pattern.finditer(comment)
    }
    for m in _MALFORMED_RE.finditer(comment):
        if m.start() in claimed:
            continue
        warnings.append(
            f"`{m.group(0)}` is not a valid directive and was ignored "
            "(see docs/knowledge/comment-directive-grammar.md)."
        )


def strip_directives(comment: Optional[str]) -> str:
    """Return `comment` with every well-formed directive removed.

    This is what lets the Comment Gardener classify a comment by what the
    studio actually put there. Malformed directives are deliberately NOT
    stripped: they are not doing anything, so they are foreign content
    and must keep the gardener's protection.

    Whitespace left behind by the removal is collapsed, so
    `"uid:a1 #TOP  variant:vertical"` strips to `"uid:a1 #TOP"` and still
    matches the gardener's strict full-match tag pattern.
    """
    if not comment:
        return ""
    out = _NUDGE_RE.sub(" ", _VARIANT_RE.sub(" ", comment))
    return re.sub(r"\s+", " ", out).strip()


def has_directives(comment: Optional[str]) -> bool:
    """True when `comment` carries at least one well-formed directive."""
    if not comment:
        return False
    return bool(_VARIANT_RE.search(comment) or _NUDGE_RE.search(comment))
