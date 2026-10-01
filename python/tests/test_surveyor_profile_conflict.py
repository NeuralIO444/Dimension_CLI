"""
test_surveyor_profile_conflict.py
Slot 15.6 (#6) — when a manual tag shadows a profile rule with a
DIFFERENT tag, the surveyor records the would-be profile tag on
the layer so the panel can surface a ⚠ "manual override conflicts
with profile rule" warning.

Spec:
  - `LayerModel.profile_suggested_tag: Optional[str]` is populated
    by `survey_manifest` ONLY when the layer's content_tag_source
    starts with "manual_*" AND the active profile resolves the
    layer name to a rule whose tag differs from the manual tag.
  - When the manual tag equals what the profile would have chosen,
    the field stays None (no conflict, even though the rule was
    shadowed).
  - When no profile is active or no manual tag was set, the field
    stays None.
  - A re-survey must clear stale values: a layer that now agrees
    with the profile (or has no manual tag) must reset to None.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


class _Layer:
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
        self.profile_suggested_tag = None  # field under test
        self.flags = None
        self.world_bounds = None
        self.source_rect = None
        self.uid = f"uid-{index:03d}"


class _Manifest:
    def __init__(self, *layers):
        self.layers = list(layers)
        self.project_info = type("P", (), {
            "width": 1920, "height": 1080,
            "name": "conflict_test", "fps": 30.0,
        })()
        self.comment_report = None


class _Profile:
    class _Rule:
        def __init__(self, tag: str, match: str):
            self.tag = tag
            self.gravity = "center"
            self.scale = 1.0
            self.weight = "normal"
            self.match = match

    def __init__(self, prefix_map):
        self.id = "synthetic"
        self.prefixes = [_Profile._Rule(tag=v, match=k)
                         for k, v in prefix_map.items()]

    def resolve(self, layer_name: str):
        if not layer_name:
            return None
        for rule in self.prefixes:
            if layer_name.startswith(rule.match):
                return rule
        return None


def test_profile_suggested_tag_populated_on_conflict():
    """Manual tag HERO; profile says TITLE_* → TT → conflict ⚠."""
    from core.surveyor import survey_manifest

    profile = _Profile({"TITLE_": "TT"})
    layer = _Layer("TITLE_main", content_tag="HERO",
                   source="manual_label", index=1)
    manifest = _Manifest(layer)

    survey_manifest(manifest, profile=profile)

    assert layer.profile_suggested_tag == "TT", (
        "manual tag HERO shadows TITLE_*→TT rule; surveyor must "
        "record `profile_suggested_tag='TT'` so the panel can ⚠"
    )


def test_no_conflict_when_manual_agrees_with_profile():
    """Manual tag matches what profile would have chosen → no ⚠."""
    from core.surveyor import survey_manifest

    profile = _Profile({"TITLE_": "TT"})
    layer = _Layer("TITLE_main", content_tag="TT",
                   source="manual_comment", index=1)
    manifest = _Manifest(layer)

    survey_manifest(manifest, profile=profile)

    assert layer.profile_suggested_tag is None, (
        "manual tag agrees with profile rule; this is shadowed but "
        "NOT a conflict — field must stay None"
    )


def test_no_conflict_when_profile_has_no_matching_rule():
    """Manual tag, no matching profile rule → no ⚠."""
    from core.surveyor import survey_manifest

    profile = _Profile({"NEVERMATCH_": "TT"})
    layer = _Layer("RandomBlob", content_tag="HERO",
                   source="manual_label", index=1)
    manifest = _Manifest(layer)

    survey_manifest(manifest, profile=profile)

    assert layer.profile_suggested_tag is None


def test_no_conflict_when_no_profile_active():
    """No active profile means nothing to conflict with."""
    from core.surveyor import survey_manifest

    layer = _Layer("HERO_promo", content_tag="HERO",
                   source="manual_label", index=1)
    manifest = _Manifest(layer)

    survey_manifest(manifest, profile=None)

    assert layer.profile_suggested_tag is None


def test_resurvey_clears_stale_conflict():
    """A layer that previously conflicted but now agrees must reset."""
    from core.surveyor import survey_manifest

    profile = _Profile({"TITLE_": "TT"})
    layer = _Layer("TITLE_main", content_tag="HERO",
                   source="manual_label", index=1)
    manifest = _Manifest(layer)

    # First pass: conflict.
    survey_manifest(manifest, profile=profile)
    assert layer.profile_suggested_tag == "TT"

    # User accepts the profile suggestion (or re-tags) — re-survey
    # should clear the stale conflict flag.
    layer.content_tag = "TT"
    survey_manifest(manifest, profile=profile)
    assert layer.profile_suggested_tag is None, (
        "after re-tag to profile's choice, re-survey must clear "
        "the stale conflict flag"
    )
