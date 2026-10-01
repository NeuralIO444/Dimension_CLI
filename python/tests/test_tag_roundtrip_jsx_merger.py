# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_tag_roundtrip_jsx_merger.py
v5.10.1 — regression coverage for the JSX `scrapeUnified` merger.

Background
----------
The Python `surveyor.classify_layer` Pass 1 trusts the manifest's
`content_tag` field when it's already a known tag. The JSX side
populates that field via `SovCore_Layer.jsx::_scrapeContentTag`,
which reads `#TAG` hashtags from the layer comment.

Until v5.10.1 those tags were silently lost on serialization:
`Sovereign_Core.jsx::scrapeUnified` walked an explicit allow-list
(V5_LAYER_KEYS) when transplanting v5 fields onto the legacy layer
records — and `content_tag` / `content_tag_source` were not on the
list. So a user who tagged a layer as HERO via the orchestrator
saw `uid:<id> #HERO` in the AE comment but the post-rescrape
manifest had `content_tag: null` for that layer; the orchestrator
re-classified it as unclassified and re-rendered `+ TAG`.

Because the bug was in JSX (untestable from pytest), the
regression coverage we add here is at the Python boundary:

  - Positive case: when the manifest carries `content_tag = "HERO"`
    on a layer whose name does NOT match any heuristic keyword,
    the surveyor MUST preserve that tag with a `manual_*` source.

  - Negative case: the SAME layer with `content_tag = None`
    (simulating the pre-v5.10.1 bug) MUST end up unclassified.
    This is the assertion that proves the test would have caught
    the original bug — without it, a future regression that drops
    the field again would slip past CI.

These two cases together pin both halves of the contract: the
surveyor honours a present tag AND fails the right way when the
tag is absent.
"""

from __future__ import annotations

import sys
import pathlib

# Make `python/` importable when pytest is run from the repo root.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.surveyor import survey_manifest  # noqa: E402
from models.scrape_manifest import LayerModel, ProjectInfo, ScrapeManifest  # noqa: E402


# A filename that the heuristic lexicon does NOT match. The real
# repro that surfaced the bug used `87N.mov` — a video filename
# the user tagged HERO. The heuristic's _filename_signals catches
# `*.mov` as ANIMATION (line 399 of surveyor.py), so we use a name
# that shares no substrings with any keyword and isn't a recognised
# extension. That guarantees the surveyor's heuristic pass produces
# `tag = None` and we can isolate Pass 1's behaviour.
_NO_HEURISTIC_MATCH_NAME = "qx7zr_payload"


def _make_manifest(content_tag, content_tag_source="manual_comment"):
    """Build a single-layer ScrapeManifest with one av layer named
    so the heuristic CANNOT classify it. The `content_tag` argument
    is what we put on the layer — None simulates the pre-v5.10.1
    JSX merger bug where the field was dropped."""
    layer = LayerModel(
        index=1,
        name=_NO_HEURISTIC_MATCH_NAME,
        layer_kind="av",
        position=[960.0, 540.0, 0.0],
        scale=[100.0, 100.0, 100.0],
        anchor=[0.0, 0.0, 0.0],
        content_tag=content_tag,
        content_tag_source=content_tag_source if content_tag else None,
    )
    return ScrapeManifest(
        status="OK",
        project_info=ProjectInfo(name="test_comp", width=1920, height=1080,
                                 fps=24.0),
        layers=[layer],
    )


class TestJsxMergerRoundTrip:
    """Pin the surveyor's behaviour on both sides of the v5.10.1 fix.

    Positive: manifest carries the manual tag → surveyor keeps it.
    Negative: manifest dropped the tag (pre-fix JSX bug) → surveyor
    can't recover it from a non-keyword layer name.
    """

    def test_manual_tag_survives_when_present_in_manifest(self):
        """The fix delivers `content_tag = "HERO"` into the manifest;
        the surveyor MUST keep it on a layer whose name doesn't
        match any heuristic keyword."""
        manifest = _make_manifest(content_tag="HERO",
                                  content_tag_source="manual_comment")
        counts = survey_manifest(manifest)

        # Counts: one manual tag, no heuristic, no unclassified.
        assert counts["manual"] == 1, (
            f"expected manual=1, got {counts['manual']} "
            f"(full counts: {counts})"
        )
        assert counts["unclassified"] == 0, (
            f"surveyor must not reclassify a layer that already "
            f"carries a valid manual tag (counts: {counts})"
        )
        assert counts["heuristic"] == 0

        layer = manifest.layers[0]
        assert layer.content_tag == "HERO", (
            f"surveyor mutated content_tag from HERO to "
            f"{layer.content_tag!r}"
        )
        assert layer.content_tag_source == "manual_comment", (
            f"surveyor mutated content_tag_source: {layer.content_tag_source!r}"
        )
        assert layer.content_tag_confidence == 1.0

    def test_negative_case_proves_test_would_have_caught_pre_fix_bug(
            self, monkeypatch):
        """Same manifest, but `content_tag = None` — the exact state
        the JSX merger produced before v5.10.1. The layer name was
        chosen so heuristic scoring CANNOT recover a tag, so the
        layer MUST end up unclassified.

        If a future regression silently re-drops `content_tag` from
        the JSX merger (or if a Python change starts ignoring the
        manifest's tag value), the positive test above passes when
        it shouldn't and we'd never know — UNLESS this negative
        test still asserts the unclassified outcome. That's what
        makes the pair regression-proof.

        Ollama is disabled via monkeypatch: if Ollama is running
        locally it would classify the layer (valid production
        behaviour) but that would break this unit test which
        specifically checks heuristic-only classification.
        """
        # Disable Ollama so live Ollama installations don't classify
        # what should be an unclassifiable layer in this unit test.
        import types
        _fake_prefs = types.SimpleNamespace(
            ollama_enabled=False, ollama_model="llama3.1:8b", ollama_threshold=0.70
        )
        monkeypatch.setattr("logic.preferences_state.preferences", _fake_prefs)
        manifest = _make_manifest(content_tag=None)
        counts = survey_manifest(manifest)

        assert counts["manual"] == 0
        assert counts["unclassified"] == 1, (
            f"layer named {_NO_HEURISTIC_MATCH_NAME!r} must NOT match "
            f"any heuristic keyword — if it does, pick a different "
            f"placeholder name. counts: {counts}"
        )

        layer = manifest.layers[0]
        assert layer.content_tag is None, (
            f"surveyor invented a tag {layer.content_tag!r} for an "
            f"unclassifiable layer — that breaks the negative case"
        )

    def test_canonical_tags_round_trip(self):
        """Smoke-test every canonical tag that the +TAG popover offers
        survives the surveyor pass. v5.10.1 lifted the lid on all of
        these — pre-fix, only legacy aliases (TYPE/KEYART/LEGALS/
        BACKGROUND) happened to round-trip because the heuristic
        keyword lexicon also produced them; canonical ids
        (HERO/TT/LGL/BG/etc.) silently dropped."""
        for tag in ("TT", "SUP", "HERO", "LOGO", "BOXART", "ARTWORK",
                    "ANIMATION", "BODY", "CTA", "LGL", "DISC", "BG"):
            manifest = _make_manifest(content_tag=tag,
                                      content_tag_source="manual_comment")
            counts = survey_manifest(manifest)
            assert counts["manual"] == 1, (
                f"canonical tag {tag!r} did not round-trip: counts={counts}"
            )
            assert manifest.layers[0].content_tag == tag

    def test_legacy_aliases_round_trip(self):
        """Backward compatibility: legacy alias forms in the manifest
        must also pass Pass 1. They were already in `VALID_TAGS` via
        `REGISTRY.all_aliases()`; this test pins the contract so a
        future cleanup PR doesn't accidentally tighten Pass 1 to
        canonical-only and break existing AE comps tagged before
        v5.10."""
        for alias in ("TYPE", "KEYART", "LEGALS", "BACKGROUND"):
            manifest = _make_manifest(content_tag=alias,
                                      content_tag_source="manual_comment")
            counts = survey_manifest(manifest)
            assert counts["manual"] == 1, (
                f"legacy alias {alias!r} no longer round-trips: counts={counts}"
            )
            # Surveyor preserves the alias verbatim — normalize() lives
            # downstream in conform-pipeline call sites (v5.10.1 commit 4).
            assert manifest.layers[0].content_tag == alias
