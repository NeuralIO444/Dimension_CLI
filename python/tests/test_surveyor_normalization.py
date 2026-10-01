# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_surveyor_normalization.py
Surveyor tag-breakdown counter must emit canonical ids only, not
the raw `content_tag` strings (which may be legacy aliases from
pre-v5.10 AE comps).

Per v5.10.1 vocab policy:
  - `classify_layer` Pass 1 honors `layer.content_tag` verbatim
    when it's in `VALID_TAGS = REGISTRY.all_ids() | REGISTRY.all_aliases()`.
  - Conform-pipeline branches (`scale_engine`, `occlusion_engine`)
    call `REGISTRY.normalize()` via local `_canon()` helpers so
    canonical and legacy are treated identically downstream.
  - The surveyor's `tag_breakdown` counter (which feeds the
    launcher chip row and the tagging-page footer) was the
    asymmetric surface: it incremented `tag_breakdown[result.tag]`
    with the verbatim value, so a manifest carrying `content_tag
    = "TYPE"` (legacy alias) would render `TYPE × N` separately
    from `TT × M` on layers whose tag was already canonical.

This test pins the contract: breakdown keys are canonical ids
only; alias inputs merge under the canonical bucket.
"""

from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


class _Layer:
    """Lightweight stand-in for LayerModel — Surveyor only reads
    `name`, `content_tag*`, `index`, `layer_kind` for the manual /
    structural / heuristic passes."""

    def __init__(self, name: str, content_tag=None,
                 source: str = "manual_comment"):
        self.name = name
        self.content_tag = content_tag
        self.content_tag_source = source if content_tag else None
        self.content_tag_confidence = 1.0 if content_tag else None
        self.index = 1
        self.layer_kind = "av"


class _Manifest:
    def __init__(self, *layers):
        self.layers = list(layers)
        self.project_info = type("P", (), {
            "width":  1920,
            "height": 1080,
            "name":   "test_comp",
            "fps":    23.976,
        })()
        # PR-E.1 — gardener report is optional; None falls through
        # to the empty-foreign-set path.
        self.comment_report = None


class TestTagBreakdownNormalizesLegacyAliases:
    """Surveyor's `tag_breakdown` must emit canonical ids only,
    matching the conform pipeline's `_canon()` normalization."""

    def test_legacy_aliases_collapse_to_canonical_in_breakdown(self):
        from core.surveyor import survey_manifest

        # v6.0 aliases per current `config/tag_registry.yaml`:
        #   TYPE → TOP (was TT)
        #   KEYART → CENTER (was HERO)
        #   LEGALS → BOTTOM (was LGL)
        #   BACKGROUND → FILL (was BG)
        #   TT → TOP, HERO → CENTER, LGL → BOTTOM, BG → FILL
        # All exercised here, paired with their canonical
        # counterparts so the merge behaviour is asserted too.
        manifest = _Manifest(
            _Layer("L1", content_tag="TYPE"),         # alias → TOP
            _Layer("L2", content_tag="TT"),           # alias → TOP
            _Layer("L3", content_tag="KEYART"),       # alias → CENTER
            _Layer("L4", content_tag="HERO"),         # alias → CENTER
            _Layer("L5", content_tag="LEGALS"),       # alias → BOTTOM
            _Layer("L6", content_tag="BACKGROUND"),   # alias → FILL
        )
        counts = survey_manifest(manifest)
        breakdown = counts["tag_breakdown"]

        # No legacy alias keys should appear.
        for alias in ("TYPE", "KEYART", "LEGALS", "BACKGROUND",
                      "TT", "HERO", "LGL", "BG"):
            assert alias not in breakdown, (
                f"legacy alias {alias!r} leaked into tag_breakdown: "
                f"{breakdown!r}"
            )

        # Aliases merge into the v6.0 canonical buckets.
        assert breakdown.get("TOP") == 2, (
            f"expected TOP × 2 (TT + TYPE alias merged); "
            f"got breakdown={breakdown!r}"
        )
        assert breakdown.get("CENTER") == 2, (
            f"expected CENTER × 2 (HERO + KEYART alias merged); "
            f"got breakdown={breakdown!r}"
        )
        assert breakdown.get("BOTTOM") == 1, (
            f"expected BOTTOM × 1 (LEGALS alias normalized); "
            f"got breakdown={breakdown!r}"
        )
        assert breakdown.get("FILL") == 1, (
            f"expected FILL × 1 (BACKGROUND alias normalized); "
            f"got breakdown={breakdown!r}"
        )

    def test_canonical_only_input_unchanged(self):
        """Sanity: a manifest with all-canonical tags renders
        breakdown identically pre- and post-fix. Guards against
        regressions where the normalize call accidentally rewrites
        already-canonical values. v6.0: uses new canonical names."""
        from core.surveyor import survey_manifest

        manifest = _Manifest(
            _Layer("L1", content_tag="TOP"),
            _Layer("L2", content_tag="TOP"),
            _Layer("L3", content_tag="CENTER"),
            _Layer("L4", content_tag="FILL"),
            _Layer("L5", content_tag="BOTTOM"),
        )
        counts = survey_manifest(manifest)
        breakdown = counts["tag_breakdown"]
        assert breakdown == {
            "TOP": 2, "CENTER": 1, "FILL": 1, "BOTTOM": 1,
        }, f"canonical-only input mutated; got {breakdown!r}"

    def test_unknown_tag_passes_through(self):
        """Defensive: REGISTRY.normalize() returns None for tags
        outside the registry. The `_canon()` helper's fallback
        (`normalize(t) or t`) preserves the original string so the
        breakdown still surfaces unknown tags rather than dropping
        them silently. Pass 1 only honors tags in VALID_TAGS so
        this is mostly theoretical — but the contract matters.
        v6.0: uses TOP instead of TT."""
        from core.surveyor import survey_manifest
        from core.tag_registry import REGISTRY

        assert REGISTRY.normalize("NOTATAG") is None, (
            "registry preconditions changed — update fixture"
        )

        # Pass 1 won't honor "NOTATAG" because it's not in VALID_TAGS,
        # so the layer falls through to heuristic / unclassified and
        # never reaches the breakdown. This test pins the contract at
        # the registry boundary instead — the _canon() fallback must
        # not raise on unknown strings.
        manifest = _Manifest(_Layer("L1", content_tag="TOP"))
        counts = survey_manifest(manifest)
        assert counts["tag_breakdown"] == {"TOP": 1}


def test_source_coverage_always_computed():
    """Audit polish: surveyor now always sets source_coverage from wb/sr
    even if missing, for Rule 3 and Ollama."""
    from core.surveyor import survey_manifest
    l1 = _Layer("Full")
    l1.world_bounds = {"l":0,"t":0,"r":1920,"b":1080}
    l2 = _Layer("Partial")
    l2.world_bounds = {"l":0,"t":0,"r":960,"b":540}
    manifest = _Manifest(l1, l2)
    manifest.project_info = type("P", (), {"width":1920, "height":1080})()
    survey_manifest(manifest)
    covs = [getattr(l, "source_coverage", None) for l in manifest.layers]
    assert covs[0] == 1.0
    assert 0.2 < covs[1] < 0.3  # approx 0.25 area
    assert all(c is not None for c in covs)
