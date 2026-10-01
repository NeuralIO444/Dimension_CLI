# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Fix 2 regression tests — GUIDE logo precomp override.

Pre-fix: `_structural_classify` returned GUIDE for ANY layer with
`flags.guide=True`, including logo/brand precomps that had the AE Guide
flag set accidentally. This misclassified `Pre_Neon_logo_10_mc` (a
CENTER precomp) as GUIDE, excluding it from gravity and center-remap.

Post-fix: if the layer is a precomp (source_item.kind == "comp") AND its
name contains a logo keyword ("logo", "brand", "lock", "ident"), the
GUIDE return is bypassed and heuristic scoring runs → CENTER via keyword.

Five test cases from plan.md:
  1. guide-flag precomp with "logo" name       → CENTER (not GUIDE)
  2. guide-flag solid with "logo" name          → GUIDE  (not precomp)
  3. guide-flag precomp with non-logo name      → GUIDE
  4. guide-flag precomp, source_item=None       → GUIDE  (safe fallback)
  5. non-guide precomp with "logo"              → CENTER (no regression)
"""

import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


def _make_layer(
    name: str,
    guide: bool = False,
    source_item_kind: str | None = None,  # "comp", "solid", "footage", or None
    idx: int = 1,
):
    """Build a minimal LayerModel with optional guide flag and source_item."""
    from models.scrape_manifest import LayerModel

    payload: dict = {
        "uid": f"u-{idx}",
        "name": name,
        "index": idx,
        "layer_kind": "av",
        "match_name": "ADBE AV Layer",
        "label": 0,
        "transforms": {
            "raw": {
                "position": [960, 540],
                "scale": [100, 100],
                "anchor_point": [50, 50],
            },
        },
    }

    if guide:
        payload["flags"] = {"guide": True}

    if source_item_kind is not None:
        payload["source_item"] = {
            "kind": source_item_kind,
            "name": name,
        }

    return LayerModel.model_validate(payload)


def _classify(layer):
    """Run classify_layer (base surveyor, no profile)."""
    from core.surveyor import classify_layer
    return classify_layer(
        layer,
        layer_index=layer.index,
        total_layers=10,
        profile=None,
    )


class TestGuideLogoPrecompFallthrough:
    """
    Case 1: guide-flag precomp with logo name → CENTER.

    `Pre_Neon_logo_10_mc` has guide=True and source_item.kind=="comp".
    Post-fix: falls through to heuristic scoring; "logo" keyword → CENTER.
    """

    def test_guide_logo_precomp_routes_to_center(self):
        layer = _make_layer(
            name="Pre_Neon_logo_10_mc",
            guide=True,
            source_item_kind="comp",
        )
        result = _classify(layer)
        assert result.tag == "CENTER", (
            f"Logo precomp with guide flag should be CENTER; got {result.tag!r}"
        )
        assert result.source != "structural", (
            "Logo precomp should NOT return structural source"
        )

    def test_guide_brand_precomp_routes_to_center(self):
        """'brand' is also in _LOGO_KWS."""
        layer = _make_layer(
            name="Pre_brand_lockup_v2",
            guide=True,
            source_item_kind="comp",
        )
        result = _classify(layer)
        assert result.tag == "CENTER", (
            f"Brand precomp with guide flag should be CENTER; got {result.tag!r}"
        )

    def test_guide_lock_precomp_routes_to_heuristic(self):
        """'lock' is in _LOGO_KWS — falls through to heuristic.
        'lock' is not a CENTER keyword in heuristics.json so the result
        may not be CENTER, but the important assertion is that it is NOT
        classified as GUIDE/structural — it reached heuristic scoring.
        This tests the fallthrough mechanism, not the heuristic outcome."""
        layer = _make_layer(
            name="Pre_lockup_v2",
            guide=True,
            source_item_kind="comp",
        )
        result = _classify(layer)
        # The key invariant: the guide structural return was bypassed.
        assert result.source != "structural", (
            "Lock precomp should NOT return structural — guide fallthrough "
            f"should have fired; got source={result.source!r}"
        )
        assert result.tag != "GUIDE", (
            f"Lock precomp should NOT be GUIDE; got {result.tag!r}"
        )


class TestGuideSolidWithLogoNameStaysGuide:
    """
    Case 2: guide-flag solid with "logo" name → GUIDE.

    A solid named "logo_bg" with guide=True is a genuine guide overlay
    (e.g. a watermark reference). It is NOT a precomp (source_item.kind
    is "solid"), so the fallthrough condition does NOT fire.
    """

    def test_guide_solid_named_logo_stays_guide(self):
        layer = _make_layer(
            name="logo_bg_guide",
            guide=True,
            source_item_kind="solid",
        )
        result = _classify(layer)
        assert result.tag == "GUIDE", (
            f"Guide solid named 'logo' should stay GUIDE; got {result.tag!r}"
        )
        assert result.source == "structural"
        assert result.protect is True

    def test_guide_footage_named_logo_stays_guide(self):
        """footage layers are also not precomps."""
        layer = _make_layer(
            name="logo_footage_ref",
            guide=True,
            source_item_kind="footage",
        )
        result = _classify(layer)
        assert result.tag == "GUIDE"
        assert result.source == "structural"


class TestGuidePrecompNonLogoNameStaysGuide:
    """
    Case 3: guide-flag precomp with non-logo name → GUIDE.

    A precomp used as a safe-area overlay (name: "03_SafeArea") has
    guide=True and source_item.kind=="comp". It does NOT have a logo
    keyword so the fallthrough does NOT fire.
    """

    def test_guide_precomp_non_logo_name_stays_guide(self):
        layer = _make_layer(
            name="03_SafeArea",
            guide=True,
            source_item_kind="comp",
        )
        result = _classify(layer)
        assert result.tag == "GUIDE", (
            f"Non-logo guide precomp should stay GUIDE; got {result.tag!r}"
        )
        assert result.source == "structural"
        assert result.protect is True

    def test_guide_precomp_titlecard_name_stays_guide(self):
        """Ensure names with no logo keyword don't accidentally fall through."""
        layer = _make_layer(
            name="Pre_Title_Card",
            guide=True,
            source_item_kind="comp",
        )
        result = _classify(layer)
        assert result.tag == "GUIDE"


class TestGuidePrecompNoSourceItemStaysGuide:
    """
    Case 4: guide-flag precomp, source_item=None → GUIDE (safe fallback).

    Legacy manifests may not have source_item populated. Without knowing
    whether the layer is a precomp, we default to the safe structural
    classification (GUIDE) rather than risking a spurious CENTER tag.
    """

    def test_guide_layer_no_source_item_stays_guide(self):
        layer = _make_layer(
            name="Pre_logo_v1",  # has logo keyword
            guide=True,
            source_item_kind=None,  # no source_item
        )
        result = _classify(layer)
        assert result.tag == "GUIDE", (
            "Guide layer with no source_item should stay GUIDE (safe fallback); "
            f"got {result.tag!r}"
        )
        assert result.source == "structural"
        assert result.protect is True


class TestNonGuideLogoPrecompNoRegression:
    """
    Case 5: non-guide precomp with "logo" name → CENTER (no regression).

    The typical case: a logo precomp WITHOUT the guide flag. This path
    has always worked (falls through all structural checks to heuristic).
    Ensures Fix 2 does not break the happy path.
    """

    def test_non_guide_logo_precomp_routes_to_center(self):
        layer = _make_layer(
            name="Pre_Neon_logo_10_mc",
            guide=False,
            source_item_kind="comp",
        )
        result = _classify(layer)
        assert result.tag == "CENTER", (
            f"Non-guide logo precomp should be CENTER; got {result.tag!r}"
        )

    def test_non_guide_logo_layer_no_source_item_routes_to_center(self):
        """Legacy manifest (no source_item) with logo name → CENTER via heuristic."""
        layer = _make_layer(
            name="logo_lockup_v3",
            guide=False,
            source_item_kind=None,
        )
        result = _classify(layer)
        assert result.tag == "CENTER", (
            f"Non-guide logo layer (no source_item) should be CENTER; "
            f"got {result.tag!r}"
        )
