# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_surveyor_shadowed_rules.py
Slot 11.5 Commit A — RED tests for `shadowed_rule_count`, the
new counter the surveyor must surface for the Phase 1 tag-source
banner.

Spec (locked):
  - A layer is "shadowed" when its `content_tag_source` starts
    with "manual_*" (manual_comment / manual_name / manual_label)
    AND the active profile's `resolve(layer.name)` returns a
    non-None rule. The manual override is shadowing a rule that
    would have caught the layer anyway — the canonical trap state.
  - `survey_manifest(..., profile=...)` must include a
    `shadowed_rule_count: int` key in its returned counts dict.
  - With no manual tags OR no matching profile rules, the count
    is zero.

Until the green phase extends `survey_manifest` to compute this
counter, every test below fails on KeyError. The test fixture
uses the existing minimal `_Layer` stand-in from
`test_surveyor_normalization.py` rather than the full
LayerModel — survey_manifest only reads a few attributes so the
synthetic shape is sufficient.
"""

from __future__ import annotations

import os
import sys
from typing import Optional


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


# ── Minimal stand-ins (mirrors test_surveyor_normalization.py) ─────


class _Layer:
    """Surveyor only reads `name`, `index`, `layer_kind`, the
    `content_tag*` triplet, and lightly probes a few optional
    attrs. Keep the shape minimal so the test doesn't have to
    spec the full LayerModel."""

    def __init__(self, name: str,
                 content_tag: Optional[str] = None,
                 source: Optional[str] = None,
                 index: int = 1):
        self.name = name
        self.index = index
        self.layer_kind = "av"
        self.content_tag = content_tag
        self.content_tag_source = source
        self.content_tag_confidence = 1.0 if content_tag else None
        # Optional attrs survey_manifest may touch.
        self.flags = None
        self.world_bounds = None
        self.source_rect = None
        self.uid = f"uid-{index:03d}"


class _Manifest:
    def __init__(self, *layers):
        self.layers = list(layers)
        self.project_info = type("P", (), {
            "width": 1920, "height": 1080,
            "name": "shadowed_test", "fps": 30.0,
        })()
        self.comment_report = None


class _Profile:
    """Stand-in profile so we can deterministically control which
    layer names the surveyor sees as 'would have matched a rule'.
    Mirrors the StudioProfile.resolve API used by classify_layer
    Pass 2."""

    class _Rule:
        def __init__(self, tag: str, match: str):
            self.tag = tag
            self.gravity = "center"
            self.scale = 1.0
            self.weight = "normal"
            self.match = match

    def __init__(self, prefix_map: dict[str, str]):
        # prefix_map: {"PREFIX_": "TAG"}
        self.id = "synthetic"
        self.prefixes = [
            _Profile._Rule(tag=v, match=k) for k, v in prefix_map.items()
        ]

    def resolve(self, layer_name: str):
        if not layer_name:
            return None
        for rule in self.prefixes:
            if layer_name.startswith(rule.match):
                return rule
        return None


# ── Tests ──────────────────────────────────────────────────────────


class TestShadowedRuleCount:
    def test_surveyor_reports_shadowed_rule_count(self):
        """3 layers manually tagged; active profile has rules that
        match their names → `shadowed_rule_count == 3`."""
        from core.surveyor import survey_manifest

        profile = _Profile({"TITLE_": "TT", "HERO_": "HERO"})
        manifest = _Manifest(
            _Layer("TITLE_main", content_tag="TT",
                   source="manual_label", index=1),
            _Layer("TITLE_sub", content_tag="TT",
                   source="manual_comment", index=2),
            _Layer("HERO_promo", content_tag="HERO",
                   source="manual_name", index=3),
            # Manual tag but profile doesn't match → NOT shadowed.
            _Layer("RandomBlob", content_tag="ARTWORK",
                   source="manual_label", index=4),
            # No manual tag → not shadowed by definition.
            _Layer("BG_plate", content_tag=None, source=None,
                   index=5),
        )

        counts = survey_manifest(manifest, profile=profile)
        assert "shadowed_rule_count" in counts, (
            "survey_manifest must surface `shadowed_rule_count` in "
            "its returned counts dict — required for the Phase 1 "
            "tag-source banner trap-state gate"
        )
        assert counts["shadowed_rule_count"] == 3, (
            f"3 manual-tagged layers match profile prefixes; "
            f"expected shadowed_rule_count=3, got "
            f"{counts['shadowed_rule_count']}"
        )

    def test_shadowed_rule_count_zero_when_no_manual(self):
        """All-profile tag state: no manual tags exist, so nothing
        can be shadowed regardless of how many rules match."""
        from core.surveyor import survey_manifest

        profile = _Profile({"TITLE_": "TT"})
        manifest = _Manifest(
            _Layer("TITLE_main", content_tag=None, source=None,
                   index=1),
            _Layer("TITLE_sub", content_tag=None, source=None,
                   index=2),
        )

        counts = survey_manifest(manifest, profile=profile)
        assert counts.get("shadowed_rule_count") == 0, (
            "no manual tags present; shadowed_rule_count must be 0"
        )

    def test_shadowed_rule_count_zero_when_no_matching_rule(self):
        """Manual tags exist but the active profile has no rule
        that would have matched those layer names — it's a clean
        manual override, not a shadowed-rule trap."""
        from core.surveyor import survey_manifest

        profile = _Profile({"NEVERMATCH_": "TT"})
        manifest = _Manifest(
            _Layer("TitleHero", content_tag="HERO",
                   source="manual_label", index=1),
            _Layer("BG_solid", content_tag="BG",
                   source="manual_comment", index=2),
        )

        counts = survey_manifest(manifest, profile=profile)
        assert counts.get("shadowed_rule_count") == 0, (
            "no profile rule matches these layer names; "
            "shadowed_rule_count must be 0"
        )

    def test_shadowed_rule_count_zero_when_no_profile(self):
        """No active profile means no rules exist to shadow.
        Returned counts dict must still include the key (the banner
        gate checks it unconditionally)."""
        from core.surveyor import survey_manifest

        manifest = _Manifest(
            _Layer("HERO_promo", content_tag="HERO",
                   source="manual_label", index=1),
        )
        counts = survey_manifest(manifest, profile=None)
        assert counts.get("shadowed_rule_count") == 0, (
            "no active profile → shadowed_rule_count must be 0 "
            "(key must exist, value must be 0)"
        )
