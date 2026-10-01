# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_surveyor_adj_override.py
Task 4 — Adjustment Layer Manual Tag Override Warning

Tests that classify_layer() appends a structured [AdjOverride: ...] token
to the reason field when the adjustment-layer pre-pass discards a manual
tag. Also tests that survey_manifest() collects these warnings into the
`adjustment_overrides` list in the returned counts dict.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


# ── Minimal stand-ins ───────────────────────────────────────────────


class _Flags:
    def __init__(self, adjustment=False, null_layer=False, guide=False,
                 is_text_layer=False, three_d=False):
        self.adjustment = adjustment
        self.null_layer = null_layer
        self.guide = guide
        self.is_text_layer = is_text_layer
        self.three_d = three_d
        # Maps to "normal" slug — no compositing blend (so FILL path fires,
        # not OVERLAY path). See _AE_BLEND_MODE_SLUGS in surveyor.py.
        self.blend_mode = "5212"


class _Layer:
    def __init__(self, name="Layer", content_tag=None, source=None,
                 index=1, flags=None):
        self.name = name
        self.index = index
        self.layer_kind = "av"
        self.content_tag = content_tag
        self.content_tag_source = source
        self.content_tag_confidence = 1.0 if content_tag else None
        self.flags = flags
        self.world_bounds = None
        self.source_rect = None
        self.uid = "uid-{0:03d}".format(index)
        self.source_item = None
        self.position = None
        self.isBrittle = False
        self.source_coverage = None
        self.profile_suggested_tag = None


class _Manifest:
    def __init__(self, *layers):
        self.layers = list(layers)
        self.project_info = type("P", (), {
            "width": 1920, "height": 1080,
            "name": "adj_test", "fps": 30.0,
        })()
        self.comment_report = None


# ── Tests ────────────────────────────────────────────────────────────


class TestAdjustmentLayerOverrideWarning:

    def test_adjustment_layer_no_manual_tag_no_warning(self):
        """Adj layer with no content_tag → FILL, no [AdjOverride:] in reason."""
        from core.surveyor import classify_layer

        layer = _Layer(
            "AdjPlain",
            content_tag=None,
            source=None,
            flags=_Flags(adjustment=True),
        )
        result = classify_layer(layer, 1, 1)

        assert result.tag == "FILL"
        assert result.source == "heuristic"
        assert "[AdjOverride:" not in result.reason

    def test_adjustment_layer_manual_label_tag_warning(self):
        """Adj layer with manual_label tag → FILL wins (pre-pass),
        reason contains structured [AdjOverride:] token."""
        from core.surveyor import classify_layer

        layer = _Layer(
            "AdjLabeled",
            content_tag="CENTER",
            source="manual_label",
            flags=_Flags(adjustment=True),
        )
        result = classify_layer(layer, 1, 1)

        assert result.tag == "FILL"
        assert result.source == "heuristic"
        assert "[AdjOverride:" in result.reason
        assert "manual_tag=CENTER" in result.reason
        assert "src=manual_label" in result.reason
        assert "→ FILL" in result.reason

    def test_adjustment_layer_manual_comment_tag_warning(self):
        """Adj layer with manual_comment tag → FILL wins (pre-pass),
        reason contains [AdjOverride:] with correct src."""
        from core.surveyor import classify_layer

        layer = _Layer(
            "AdjCommented",
            content_tag="TOP",
            source="manual_comment",
            flags=_Flags(adjustment=True),
        )
        result = classify_layer(layer, 1, 1)

        assert result.tag == "FILL"
        assert "[AdjOverride:" in result.reason
        assert "src=manual_comment" in result.reason
        assert "manual_tag=TOP" in result.reason
        assert "→ FILL" in result.reason

    def test_non_adjustment_layer_manual_tag_no_warning(self):
        """Non-adj layer with manual_label tag → Pass 1 preserves the
        manual tag; no [AdjOverride:] token in the reason."""
        from core.surveyor import classify_layer

        layer = _Layer(
            "RegularLayer",
            content_tag="CENTER",
            source="manual_label",
            flags=_Flags(adjustment=False),
        )
        result = classify_layer(layer, 1, 1)

        assert result.tag == "CENTER"
        assert result.source == "manual_label"
        assert "[AdjOverride:" not in result.reason

    def test_survey_manifest_collects_override_list(self):
        """survey_manifest on manifest with adjs: when manual present it wins
        (no override token). adjustment_overrides may be empty or only for
        cases where pre-pass actually overrode (current policy: manual first)."""
        from core.surveyor import survey_manifest

        layer_a = _Layer(
            "AdjWithManual",
            content_tag="CENTER",
            source="manual_label",
            index=1,
            flags=_Flags(adjustment=True),
        )
        layer_b = _Layer(
            "AdjNoManual",
            content_tag=None,
            source=None,
            index=2,
            flags=_Flags(adjustment=True),
        )
        manifest = _Manifest(layer_a, layer_b)
        counts = survey_manifest(manifest)

        assert "adjustment_overrides" in counts
        assert len(counts["adjustment_overrides"]) == 1

    def test_override_entry_has_required_fields(self):
        """Each adjustment_overrides entry has the 4 required keys with
        correct values: layer_name, manual_tag, manual_src, resolved_tag."""
        from core.surveyor import survey_manifest

        layer = _Layer(
            "AdjLayerNamed",
            content_tag="CENTER",
            source="manual_label",
            index=1,
            flags=_Flags(adjustment=True),
        )
        manifest = _Manifest(layer)
        counts = survey_manifest(manifest)

        assert len(counts["adjustment_overrides"]) == 1
        entry = counts["adjustment_overrides"][0]

        assert entry["layer_name"] == "AdjLayerNamed"
        assert entry["manual_tag"] == "CENTER"
        assert entry["manual_src"] == "manual_label"
        assert entry["resolved_tag"] == "FILL"
