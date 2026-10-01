# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_comment_gardener_jsx.py
PR-E.1 Layer 2 — JSX-written-fixture contract tests for the
`comment` field that surfaces from `SovCore_Layer.jsx` through
`Sovereign_Core.jsx::scrapeUnified` (V5_LAYER_KEYS allow-list)
into `LayerModel.comment`.

What this layer covers (and why synthetic dicts cannot replace it)
-------------------------------------------------------------------
Layer 1 (test_comment_gardener.py) verifies the classifier is
internally consistent. Synthetic dicts are appropriate there —
the contract under test is the Python schema's logic.

Layer 2 (this file) verifies that the JSX side actually writes
the `comment` field through the V5_LAYER_KEYS pipeline so the
Python `LayerModel` sees it. Synthetic dicts cannot prove this.
PR #45 + PR #48 both shipped because Python tests were green
against synthetic dicts that didn't reflect what JSX produced.
The smoke-test scrape on 2026-04-28 confirmed the field surfaces
end-to-end; this test pins that contract so a future regression
that drops the field from V5_LAYER_KEYS, removes it from
LayerModel, or stops emitting it from scrapeLayer fails CI.

Coverage gap
------------
The captured fixture (`scrape-87n-no-comments.json`) is the
"all comments null" case from the 87N test comp. It catches the
key-presence regression but does NOT exercise non-empty comment
content. A richer fixture (with deliberately-set foreign content
on at least one layer) is a documented follow-up — see the
fixtures README.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from models.scrape_manifest import ScrapeManifest  # noqa: E402


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "comment_field"


def _load_fixture(name: str) -> dict | None:
    """Return the parsed JSON, or None if the fixture doesn't
    exist yet (so future fixture captures can use pytest.skip
    instead of failing)."""
    path = FIXTURES_DIR / f"{name}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


_SKIP_MSG = (
    "Awaiting fixture capture — see "
    "python/tests/fixtures/comment_field/README.md"
)


# ── Key-presence contract on the all-null fixture ────────────────


class TestCommentFieldKeyPresence:
    """`scrape-87n-no-comments.json` is the post-PR-E.1-commit-2
    smoke-test capture. Every layer has `comment` as a top-level
    key (null for every layer in this fixture). These tests pin
    the V5_LAYER_KEYS → LayerModel wire."""

    def test_fixture_parses_through_layer_model(self):
        """Parse every layer in the fixture through LayerModel.
        If V5_LAYER_KEYS or the schema drift, this fails first.
        Same anti-pattern guard pattern as PR-B's bridge fixtures."""
        data = _load_fixture("scrape-87n-no-comments")
        if data is None:
            pytest.skip(_SKIP_MSG)

        manifest = ScrapeManifest.model_validate(data)
        assert len(manifest.layers) == 19, (
            "fixture should have 19 layers (87N test comp)"
        )

    def test_every_layer_dict_has_comment_key(self):
        """Hard-pin: the raw JSON must carry `comment` on every
        layer record. If a future regression drops it from
        V5_LAYER_KEYS, the key disappears from the wire and this
        test fails BEFORE the Python schema gets a chance to
        coerce a missing key to its Optional default."""
        data = _load_fixture("scrape-87n-no-comments")
        if data is None:
            pytest.skip(_SKIP_MSG)

        for i, layer in enumerate(data["layers"]):
            assert "comment" in layer, (
                f"layer index {i} ({layer.get('name')!r}) missing "
                f"`comment` key. V5_LAYER_KEYS dropped the field "
                f"or the JSX scraper stopped emitting it."
            )

    def test_layer_model_field_type_is_optional_str(self):
        """Pydantic schema accepts None and round-trips it as
        None. Defends against a future schema edit that tightens
        the field to a required str (which would refuse the
        all-null case + every layer that has no studio comment)."""
        data = _load_fixture("scrape-87n-no-comments")
        if data is None:
            pytest.skip(_SKIP_MSG)

        manifest = ScrapeManifest.model_validate(data)
        for L in manifest.layers:
            # Either None or str — Optional[str].
            assert L.comment is None or isinstance(L.comment, str), (
                f"layer {L.index} has unexpected comment type "
                f"{type(L.comment).__name__}"
            )

    def test_classifier_runs_on_real_fixture(self):
        """End-to-end smoke: scan the fixture through the actual
        gardener and assert it produces a sensible report. With
        the 87N fixture (all-null), expected outcome is 19 EMPTY
        layers and zero warnings."""
        from core.comment_gardener import (
            CommentClass, scan_comp,
        )
        data = _load_fixture("scrape-87n-no-comments")
        if data is None:
            pytest.skip(_SKIP_MSG)

        manifest = ScrapeManifest.model_validate(data)
        report = scan_comp(manifest)

        assert report.total_layers == 19
        assert report.has_warnings is False
        assert report.foreign_count == 0
        assert report.malformed_count == 0
        assert report.legacy_count == 0
        assert report.mixed_count == 0
        # Every layer should be EMPTY (the 87N comp has no
        # comment content set on any layer).
        assert len(report.by_class[CommentClass.EMPTY]) == 19


# ── Follow-up fixture (placeholder) ──────────────────────────────


class TestCommentFieldRoundTripWithContent:
    """This class skips today because the richer fixture (with
    real non-empty comment content) is a documented follow-up.
    See README.md for the capture procedure. The skip auto-lifts
    once the fixture lands."""

    def test_round_trip_preserves_foreign_content_verbatim(self):
        """When the follow-up fixture lands, this test will
        assert that a layer with a known studio comment (e.g.
        "render at 4K") classifies as FOREIGN with the original
        text intact in the LayerSummary preview."""
        data = _load_fixture("scrape-with-foreign-comment")
        if data is None:
            pytest.skip(_SKIP_MSG)

        from core.comment_gardener import (
            scan_comp,
        )
        manifest = ScrapeManifest.model_validate(data)
        report = scan_comp(manifest)
        assert report.foreign_count >= 1
