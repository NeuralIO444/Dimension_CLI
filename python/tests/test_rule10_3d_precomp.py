# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).

"""
Tests for Rule 10: 3D precomp at Z≠0 → CENTER (pre-pass, bypasses manual tags).

Context: precomp layers in a 3D camera scene (e.g. animated logo precomps
at Z=470) were being tagged TOP via AE label color 2 on every rescan.
Rule 10 fires before Pass 1 so the label color can no longer override it.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from models.scrape_manifest import LayerModel, LayerFlags, SourceItem
from core.surveyor import classify_layer


def _make_3d_precomp(z_pos: float, content_tag="TOP", source="manual_label"):
    flags = LayerFlags(three_d=True, blend_mode="5212")
    si = SourceItem.model_validate({
        "kind": "comp", "id": 12345,
        "name": "87N_Reels_DEV_87Neon_HD_10_mc",
    })
    return LayerModel.model_validate({
        "id": 1, "index": 14,
        "name": "87N_Reels_DEV_87Neon_HD_10_mc",
        "layer_kind": "av",
        "position": [960.0, 540.0, z_pos],
        "scale": [100.0, 100.0, 100.0],
        "anchor": [960.0, 580.0, 0.0],
        "rotation_z": 0.0, "hero_time": 5.0,
        "flags": flags.model_dump(),
        "source_item": si.model_dump(),
        "content_tag": content_tag,
        "content_tag_source": source,
    })


class TestRule10_3dPrecompCenter:

    def test_3d_precomp_at_significant_z_gets_center(self):
        """Rule 10: 3D precomp at Z=470 → CENTER regardless of manual label."""
        layer = _make_3d_precomp(z_pos=470.0)
        result = classify_layer(layer, 14, 16)
        assert result.tag == "CENTER"
        assert result.source == "heuristic"
        assert result.confidence == pytest.approx(0.85)
        assert "Rule 10" in result.reason

    def test_3d_precomp_overrides_manual_top_label(self):
        """AE label color 2 (TOP) on a 3D precomp is overridden by Rule 10."""
        layer = _make_3d_precomp(z_pos=470.0, content_tag="TOP",
                                  source="manual_label")
        result = classify_layer(layer, 14, 16)
        assert result.tag == "CENTER"
        assert result.source == "heuristic"

    def test_3d_precomp_at_z_zero_falls_through(self):
        """A 3D precomp at Z=0 is not caught by Rule 10 (no world-space depth)."""
        layer = _make_3d_precomp(z_pos=0.0, content_tag="TOP",
                                  source="manual_label")
        result = classify_layer(layer, 14, 16)
        # Should fall through to Pass 1 (manual label TOP) since Z=0
        assert result.tag == "TOP"
        assert result.source == "manual_label"

    def test_3d_precomp_at_small_z_threshold_falls_through(self):
        """Z=0.05 is below the 0.1 threshold — still falls through."""
        layer = _make_3d_precomp(z_pos=0.05, content_tag="TOP",
                                  source="manual_label")
        result = classify_layer(layer, 14, 16)
        assert result.tag == "TOP"

    def test_negative_z_also_triggers_rule10(self):
        """Z=-800 (behind camera) — abs(Z)=800 > 0.1 → CENTER."""
        layer = _make_3d_precomp(z_pos=-800.0)
        result = classify_layer(layer, 14, 16)
        assert result.tag == "CENTER"

    def test_2d_precomp_not_caught_by_rule10(self):
        """A 2D precomp (three_d=False) with Z in name is not caught."""
        flags = LayerFlags(three_d=False, blend_mode="5212")
        si = SourceItem.model_validate({"kind": "comp", "id": 9, "name": "logo"})
        layer = LayerModel.model_validate({
            "id": 2, "index": 5, "name": "logo_precomp",
            "layer_kind": "av",
            "position": [960.0, 540.0, 470.0],  # Z set but three_d=False
            "scale": [100.0, 100.0, 100.0],
            "anchor": [960.0, 540.0, 0.0],
            "rotation_z": 0.0, "hero_time": 5.0,
            "flags": flags.model_dump(),
            "source_item": si.model_dump(),
            "content_tag": "TOP", "content_tag_source": "manual_label",
        })
        result = classify_layer(layer, 5, 16)
        # three_d=False → Rule 10 doesn't fire; Pass 1 (manual TOP) wins
        assert result.tag == "TOP"

    def test_av_footage_layer_not_caught(self):
        """A regular 3D footage layer (not a precomp) is not caught by Rule 10."""
        flags = LayerFlags(three_d=True, blend_mode="5212")
        layer = LayerModel.model_validate({
            "id": 3, "index": 2, "name": "hero_footage",
            "layer_kind": "av",
            "position": [960.0, 540.0, 200.0],
            "scale": [100.0, 100.0, 100.0],
            "anchor": [960.0, 540.0, 0.0],
            "rotation_z": 0.0, "hero_time": 5.0,
            "flags": flags.model_dump(),
            # No source_item — footage layer, not a precomp wrapper
            "content_tag": "TOP", "content_tag_source": "manual_label",
        })
        result = classify_layer(layer, 2, 16)
        # source_item is None → Rule 10 condition fails; Pass 1 (TOP) wins
        assert result.tag == "TOP"
