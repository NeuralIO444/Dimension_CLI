"""
Bug 1 (2026-04-29) regression tests — structural-layer classification.

Pre-fix: JSX `_scrapeContentTag` would read AE label color even for
camera/light/null layers, so a camera with label color 4 produced
`content_tag: "SUP"`. Python's surveyor Pass 1 then trusted the
manual_label tag and skipped Pass 3 (`_structural_classify`), hijacking
the camera into SUP gravity rules.

Post-fix: JSX short-circuits camera/light/null at the top of
`_scrapeContentTag`, returning `{tag: null, source: null}`. Python's
Pass 1 falls through; Pass 3 fires and assigns the canonical structural
result (tag=None, source="structural", protect=True for camera/light;
tag="NULL", source="structural", protect=True for null objects).

These tests pin the Python-side guarantee: when JSX emits the post-fix
shape, the surveyor produces a structural classification regardless of
what label color or layer comment looked like in AE. Layer 2
(real-AE-fixture) coverage is added when a fresh camera scrape fixture
lands — same pattern as the v5.10.1 / v5.10.2 V5_LAYER_KEYS tests.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


def _structural_layer(layer_kind: str, *, name: str = "Camera 1",
                       content_tag=None, content_tag_source=None,
                       comment=None, label=0, idx: int = 1,
                       null_layer: bool = False):
    """Build a LayerModel matching what JSX emits post-Bug-1-fix.

    For camera/light/null, JSX returns {tag: null, source: null}
    BEFORE looking at comment / name / label color. So even if the
    fixture has a wild label color or `#HERO` comment, the manifest
    carries content_tag=None.
    """
    from models.scrape_manifest import LayerModel
    payload = {
        "uid":        f"u-{idx}",
        "name":       name,
        "index":      idx,
        "layer_kind": layer_kind,
        "match_name": _match_name_for(layer_kind, null_layer),
        "label":      label,
        "comment":    comment,
        "content_tag":         content_tag,
        "content_tag_source":  content_tag_source,
        "transforms": {
            "raw": {
                "position":     [0, 0],
                "scale":        [100, 100],
                "anchor_point": [50, 50],
            },
        },
    }
    if null_layer:
        payload["flags"] = {"null_layer": True}
    return LayerModel.model_validate(payload)


def _match_name_for(layer_kind: str, null_layer: bool) -> str:
    if layer_kind == "camera":
        return "ADBE Camera Layer"
    if layer_kind == "light":
        return "ADBE Light Layer"
    if null_layer:
        return "ADBE AV Layer"
    return "ADBE Vector Layer"


def _classify(layer):
    """Run the base surveyor (no profile) — return the full TagResult."""
    from core.surveyor import classify_layer
    return classify_layer(layer, layer_index=layer.index,
                          total_layers=10, profile=None)


# ── Bug 1 regression — camera/light don't get hijacked by label color ──


class TestStructuralClassifyCamera:
    """Pre-fix: a Camera with label color 4 produced content_tag='SUP'
    from JSX, hijacking surveyor Pass 1 and skipping Pass 3. Post-fix:
    JSX returns null content_tag for cameras regardless of label color;
    Pass 3 then correctly classifies as structural (tag=None,
    source='structural', protect=True)."""

    def test_camera_with_label_4_routes_to_structural(self):
        """The TEST_02_Camera_Dolly minimal repro pattern. Pre-fix
        produced tag_breakdown: {SUP: 1}; post-fix should hit Pass 3."""
        layer = _structural_layer("camera", label=4)
        result = _classify(layer)
        assert result.tag is None, (
            f"camera should NOT be tagged; got {result.tag!r}"
        )
        assert result.source == "structural"
        assert result.protect is True
        assert "STRUCTURAL" in result.reason

    def test_camera_with_no_label_routes_to_structural(self):
        layer = _structural_layer("camera", label=0)
        result = _classify(layer)
        assert result.tag is None
        assert result.source == "structural"

    def test_camera_with_hero_comment_still_structural(self):
        """Q3 design decision: artist comment overrides on structural
        layers are suppressed by design. JSX's short-circuit fires
        BEFORE the comment branch, so #HERO on a camera never reaches
        Python's Pass 1 — the post-fix manifest has content_tag=None,
        Pass 3 wins, structural classification holds."""
        layer = _structural_layer("camera", label=4,
                                   comment="uid:abc123 #HERO")
        result = _classify(layer)
        assert result.tag is None, (
            "comment overrides on structural layers must be suppressed; "
            f"got {result.tag!r}"
        )
        assert result.source == "structural"


class TestStructuralClassifyLight:
    """Lights are the same shape as cameras — instanceof LightLayer
    triggers the same JSX short-circuit."""

    def test_light_with_label_color_routes_to_structural(self):
        layer = _structural_layer("light", name="Spot 1", label=2)
        result = _classify(layer)
        assert result.tag is None
        assert result.source == "structural"
        assert result.protect is True


class TestStructuralClassifyNullObject:
    """Null objects are AVLayer instances with `nullLayer === true`,
    not a separate AE class. Per the AE DOM addendum, JSX's
    three-class check covers them via
    `instanceof AVLayer && nullLayer === true`. Python's Pass 3
    classifies them as 'NULL' tag (not None like cameras/lights) —
    locked at surveyor.py:149."""

    def test_null_object_routes_to_structural_null_tag(self):
        layer = _structural_layer("av", name="Null 1", null_layer=True,
                                   label=4)
        result = _classify(layer)
        assert result.tag == "NULL"
        assert result.source == "structural"
        assert result.protect is True


# ── Negative regression — non-structural AVLayer with label color
#    keeps working ────────────────────────────────────────────────────


class TestNonStructuralLabelColorStillWorks:
    """Confirm the short-circuit fires only on the three structural
    classes, not on every AVLayer. A regular AV layer with label
    color 4 should still classify as SUP via the existing JSX
    label-color branch — the short-circuit must not break that path.

    This is the symmetric guard: Bug 1 fixes false-positive structural
    misses without false-positive structural HITS on content layers.
    """

    def test_av_layer_with_label_4_still_gets_sup_when_jsx_emits_it(self):
        """Simulates what post-fix JSX emits for a regular AV layer
        with label color 4 — JSX's label-color branch still runs (only
        camera/light/null short-circuit), so content_tag arrives as
        'SUP'. Python's Pass 1 trusts it and returns SUP."""
        layer = _structural_layer("av", name="some_text_layer",
                                   label=4, content_tag="SUP",
                                   content_tag_source="manual_label")
        result = _classify(layer)
        assert result.tag == "SUP"
        assert result.source == "manual_label"
