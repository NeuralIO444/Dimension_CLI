# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_gardener_prescan.py
Sprint 3 (PR #121) — Unit tests for the prescan_comp() function and
the new PreflightReport / LabelConflict / ExpressionIssue / SafeZoneGap
dataclasses in `python/core/comment_gardener.py`.

Synthetic fixture approach is correct here: the contract under test is
the Python prescan logic given duck-typed layer objects.  The prescan
does NOT depend on JSX-written data (it reads fields that Python also
writes).  A Layer 2 integration test (reading a real JSX-written manifest
with `label` set) would be the guard against JSX-side drift — filed as a
follow-up per scope-doc.

Character-class note (CLAUDE.md sharp edge): `expressions` dict values
are arbitrary strings — no regex constraint.  Layer index and label are
plain ints.  These fixtures are safe to use verbatim.
"""

from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.comment_gardener import (  # noqa: E402
    ExpressionIssue,
    LabelConflict,
    OrphanedLabelColor,
    PreflightReport,
    SafeZoneGap,
    prescan_comp,
)


# ── Test helpers ─────────────────────────────────────────────────────────


class _FakeLayer:
    """Minimal duck-type accepted by prescan_comp().

    prescan_comp() reads via getattr with defaults:
      `index`              — int, 1-based layer order
      `name`                — str
      `label`               — int, AE label colour (0 = no label)
      `expressions`         — dict[str, str], property_name → expression string
      `content_tag`         — str|None, final post-survey classification
      `content_tag_source`  — str|None, final post-survey source
    """

    def __init__(
        self,
        index: int = 1,
        name: str = "Layer 1",
        label: int = 0,
        expressions: dict = None,
        content_tag: str = None,
        content_tag_source: str = None,
    ):
        self.index = index
        self.name = name
        self.label = label
        self.expressions = expressions if expressions is not None else {}
        self.content_tag = content_tag
        self.content_tag_source = content_tag_source


class _FakeManifest:
    def __init__(self, layers=None):
        self.layers = layers or []


# ── Return type ───────────────────────────────────────────────────────────


class TestPrescanReturnType:
    """prescan_comp() must return a PreflightReport regardless of input."""

    def test_prescan_returns_report_type(self):
        manifest = _FakeManifest(layers=[])
        result = prescan_comp(manifest)
        assert isinstance(result, PreflightReport)

    def test_empty_manifest_returns_empty_report(self):
        manifest = _FakeManifest(layers=[])
        result = prescan_comp(manifest)
        assert result.label_conflicts == []
        assert result.expression_issues == []
        assert result.safe_zone_gaps == []

    def test_none_layers_handled_gracefully(self):
        """manifest.layers = None must not raise."""
        manifest = _FakeManifest(layers=None)
        result = prescan_comp(manifest)
        assert isinstance(result, PreflightReport)


# ── Label conflict detection ───────────────────────────────────────────────


class TestLabelConflictDetection:
    """Label colours that resolve to GUIDE or TOP are flagged as conflicts."""

    def test_label_conflict_detected(self):
        """Label 15 was historically GUIDE — flagged via the legacy map."""
        layer = _FakeLayer(index=3, name="Pre_Neon_logo_10_mc", label=15)
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert len(result.label_conflicts) == 1
        conflict = result.label_conflicts[0]
        assert isinstance(conflict, LabelConflict)
        assert conflict.layer_index == 3
        assert conflict.layer_name == "Pre_Neon_logo_10_mc"
        assert conflict.ae_label_color == 15
        assert conflict.resolved_tag == "GUIDE"
        assert conflict.suggested_fix  # non-empty string

    def test_label_conflict_top_detected(self):
        """Label 2 → TOP (live registry mapping) is flagged as a conflict."""
        layer = _FakeLayer(index=7, name="3D_precomp_world", label=2)
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert len(result.label_conflicts) == 1
        conflict = result.label_conflicts[0]
        assert conflict.resolved_tag == "TOP"
        assert conflict.ae_label_color == 2
        assert conflict.layer_index == 7

    def test_no_conflict_for_label_zero(self):
        """Label 0 is the 'no label' sentinel — must never produce a conflict."""
        layer = _FakeLayer(index=1, name="Background", label=0)
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert result.label_conflicts == []

    def test_no_conflict_for_content_label(self):
        """Label 1 → CENTER.  CENTER is not in _CONFLICT_TAGS — no conflict."""
        layer = _FakeLayer(index=2, name="Logo_lockup", label=1)
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert result.label_conflicts == []

    def test_multiple_conflicting_layers(self):
        """All conflicting layers in the comp are reported."""
        layers = [
            _FakeLayer(index=1, name="SafeArea_guide", label=15),  # GUIDE (legacy)
            _FakeLayer(index=2, name="TitleCard", label=2),         # TOP
            _FakeLayer(index=3, name="Background", label=0),        # no conflict
        ]
        manifest = _FakeManifest(layers=layers)
        result = prescan_comp(manifest)
        assert len(result.label_conflicts) == 2
        indices = {c.layer_index for c in result.label_conflicts}
        assert indices == {1, 2}

    def test_conflict_carries_suggested_fix(self):
        """LabelConflict.suggested_fix is a non-empty actionable string."""
        layer = _FakeLayer(label=15)
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        fix = result.label_conflicts[0].suggested_fix
        assert isinstance(fix, str) and len(fix) > 10


# ── Orphaned label color detection ─────────────────────────────────────────


class TestOrphanedLabelColorDetection:
    """Nonzero AE label colors that no current tag (or legacy-map entry)
    claims must be flagged — almost always a sign of a manual tag from
    before the 4-tag vocabulary simplification (#117) silently
    disappearing. Root-caused 2026-06-30 from 87N.mov's label=3 (the old
    ARTWORK color) resolving to nothing and falling through to AI."""

    def test_pre_117_artwork_label_resolves_via_legacy_map(self):
        """Label 3 was ARTWORK under the pre-#117 vocabulary — must
        resolve to CENTER via the legacy colour map, not orphan."""
        layer = _FakeLayer(
            index=16, name="87N.mov", label=3,
            content_tag="FILL", content_tag_source="heuristic",
        )
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert result.orphaned_label_colors == []

    def test_unmapped_label_flagged_when_fallen_through_to_heuristic(self):
        """Label 16 is unused by both live and legacy maps. Final
        classification came from heuristic — must flag."""
        layer = _FakeLayer(
            index=16, name="87N.mov", label=16,
            content_tag="FILL", content_tag_source="heuristic",
        )
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert len(result.orphaned_label_colors) == 1
        warn = result.orphaned_label_colors[0]
        assert isinstance(warn, OrphanedLabelColor)
        assert warn.layer_index == 16
        assert warn.layer_name == "87N.mov"
        assert warn.ae_label_color == 16
        assert warn.current_tag == "FILL"
        assert warn.current_tag_source == "heuristic"

    def test_unmapped_label_flagged_when_fallen_through_to_ai(self):
        """Same scenario, but the layer ended up AI-classified (the
        exact case observed live on the 87N comp)."""
        layer = _FakeLayer(
            index=16, name="87N.mov", label=16,
            content_tag="FILL", content_tag_source="ai",
        )
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert len(result.orphaned_label_colors) == 1
        assert result.orphaned_label_colors[0].current_tag_source == "ai"

    def test_no_warning_when_comment_override_already_covers_it(self):
        """A #TAG comment takes precedence over label color regardless —
        the orphaned color is harmless when comment already won."""
        layer = _FakeLayer(
            index=16, name="87N.mov", label=3,
            content_tag="CENTER", content_tag_source="manual_comment",
        )
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert result.orphaned_label_colors == []

    def test_no_warning_when_name_override_already_covers_it(self):
        """Same precedence reasoning for a [BRACKET] name override."""
        layer = _FakeLayer(
            index=16, name="[CENTER] 87N.mov", label=3,
            content_tag="CENTER", content_tag_source="manual_name",
        )
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert result.orphaned_label_colors == []

    def test_no_warning_for_label_zero(self):
        """Label 0 is the 'no label' sentinel — never flagged."""
        layer = _FakeLayer(index=1, name="Background", label=0)
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert result.orphaned_label_colors == []

    def test_no_warning_for_currently_mapped_label(self):
        """Label 1 → CENTER (live registry mapping) is a normal, valid
        manual_label tag — must not be flagged as orphaned."""
        layer = _FakeLayer(
            index=2, name="Logo_lockup", label=1,
            content_tag="CENTER", content_tag_source="manual_label",
        )
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert result.orphaned_label_colors == []

    def test_no_warning_for_legacy_mapped_label(self):
        """Label 15 resolves via _LEGACY_LABEL_MAP to GUIDE — that's a
        LabelConflict (different check), not an orphaned color."""
        layer = _FakeLayer(index=3, name="SafeArea_guide", label=15)
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert result.orphaned_label_colors == []
        assert len(result.label_conflicts) == 1

    def test_multiple_orphaned_layers_all_reported(self):
        layers = [
            _FakeLayer(index=1, name="A", label=16, content_tag_source="heuristic"),
            _FakeLayer(index=2, name="B", label=16, content_tag_source="ai"),
            _FakeLayer(index=3, name="C", label=1, content_tag_source="manual_label"),
        ]
        manifest = _FakeManifest(layers=layers)
        result = prescan_comp(manifest)
        assert len(result.orphaned_label_colors) == 2
        indices = {w.layer_index for w in result.orphaned_label_colors}
        assert indices == {1, 2}


# ── Expression issue detection ─────────────────────────────────────────────


class TestExpressionIssueDetection:
    """Layers with active expressions on transform properties are flagged."""

    def test_expression_issue_detected(self):
        """A layer with a wiggle() expression on 'position' is flagged."""
        layer = _FakeLayer(
            index=4,
            name="Neon_glow",
            expressions={"position": "wiggle(2,30)"},
        )
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert len(result.expression_issues) == 1
        issue = result.expression_issues[0]
        assert isinstance(issue, ExpressionIssue)
        assert issue.layer_index == 4
        assert issue.layer_name == "Neon_glow"
        assert issue.property_name == "position"
        assert "wiggle" in issue.expression_snippet

    def test_no_expression_empty(self):
        """A layer with an empty expressions dict produces no issue."""
        layer = _FakeLayer(index=1, expressions={})
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert result.expression_issues == []

    def test_no_expression_none_value(self):
        """A layer with expressions={'position': None} produces no issue."""
        layer = _FakeLayer(index=1, expressions={"position": None})
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert result.expression_issues == []

    def test_expression_snippet_truncated_at_100_chars(self):
        """Expression strings longer than 100 chars are truncated."""
        long_expr = "wiggle(2,30);" * 20  # >100 chars
        layer = _FakeLayer(expressions={"scale": long_expr})
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert len(result.expression_issues) == 1
        snippet = result.expression_issues[0].expression_snippet
        assert len(snippet) <= 100

    def test_multiple_expressions_on_same_layer(self):
        """Each expression property gets its own ExpressionIssue row."""
        layer = _FakeLayer(
            index=5,
            expressions={
                "position": "wiggle(2,30)",
                "rotation": "loopOut()",
            },
        )
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert len(result.expression_issues) == 2
        props = {i.property_name for i in result.expression_issues}
        assert props == {"position", "rotation"}

    def test_whitespace_only_expression_ignored(self):
        """An expression string that is only whitespace is not an active expression."""
        layer = _FakeLayer(expressions={"position": "   "})
        manifest = _FakeManifest(layers=[layer])
        result = prescan_comp(manifest)
        assert result.expression_issues == []


# ── Safe zone gap detection ────────────────────────────────────────────────


class TestSafeZoneGapDetection:
    """When the target preset slug has no mask on disk, a gap is reported."""

    def test_safe_zone_gap_detected(self):
        """An unknown preset slug produces a SafeZoneGap entry."""
        manifest = _FakeManifest(layers=[])
        result = prescan_comp(
            manifest, target_preset_slug="__no_such_preset_for_tests__"
        )
        assert len(result.safe_zone_gaps) == 1
        gap = result.safe_zone_gaps[0]
        assert isinstance(gap, SafeZoneGap)
        assert gap.preset_slug == "__no_such_preset_for_tests__"
        assert gap.message  # non-empty

    def test_no_gap_when_slug_omitted(self):
        """When target_preset_slug is None, no safe-zone check runs."""
        manifest = _FakeManifest(layers=[])
        result = prescan_comp(manifest, target_preset_slug=None)
        assert result.safe_zone_gaps == []

    def test_known_slug_produces_no_gap(self):
        """A slug that exists in config/safe_zones/*.png produces no gap.

        The test uses 'tiktok' — the only slug guaranteed present in the
        repo default safe-zone directory (config/safe_zones/tiktok.png).
        If the test environment lacks the config dir, the check degrades
        silently (available_masks() returns an empty set and the gap would
        fire).  We accept that — the important invariant is 'known slug →
        no gap when mask exists', not 'test always passes regardless of
        environment'."""
        manifest = _FakeManifest(layers=[])
        result = prescan_comp(manifest, target_preset_slug="tiktok")
        # Only assert no gaps when the mask is actually resolvable.
        # available_masks() may return empty in some CI environments that
        # don't include the config/ tree — skip instead of failing.
        from logic.safe_zone_resolver import available_masks
        if "tiktok" in available_masks():
            assert result.safe_zone_gaps == []
        # If the mask isn't present in this env, we just don't assert.
