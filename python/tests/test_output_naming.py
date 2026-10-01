# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_output_naming.py — Slot 12.5 Stage D / item 1.

Unit coverage for `core.output_naming.resolve_output_name` +
`resolve_output_name_for_preset`, the single source-of-truth for
conformed-output comp naming per Q4 Path A-minus.

Coverage scope:
  - Template substitution: {source}, {width}, {height}, {date}, unknown tokens
  - Collision-aware bump: clean base, single bump, multi-bump, version=999 cap
  - Default template behavior (no template passed)
  - Preset-driven wrapper carries template + width + height from Target
  - Caller-must-reserve contract (NamedTuple returned; existing_names
    not mutated by the resolver)
  - sanitize_comp_name: AE-illegal character stripping, collapse,
    whitespace/underscore trim, empty-result fallback, and wiring into
    resolve_output_name (Track B / B2, 2026-08-26)
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
))

from core.output_naming import (  # noqa: E402
    DEFAULT_OUTPUT_NAME_TEMPLATE,
    ResolvedName,
    resolve_output_name,
    resolve_output_name_for_preset,
    sanitize_comp_name,
)
from models.target import Target  # noqa: E402


def _target(template: str = "{source}_{preset}", width: int = 1080, height: int = 1920) -> Target:
    """Factory for a representative Target with the given template +
    target dimensions. Uses the social/tiktok shape so the catalog
    semantics don't get in the way of naming verification."""
    return Target(
        id="test:tiktok",
        label="TikTok Vertical",
        category="social",
        subcategory="tiktok",
        width=width,
        height=height,
        aspect_ratio=width / height,
        aspect_label="9:16",
        source="builtin",
        output_name_template=template,
    )


# ---------------------------------------------------------------------------
# 1. Template substitution
# ---------------------------------------------------------------------------

class TestTemplateSubstitution:
    def test_default_template_includes_preset_slug(self):
        # v6 — default is "{source}_{preset}"; preset_id default "" 
        # resolves to "conform" via _slug_preset's fallback.
        result = resolve_output_name("Hero", existing_names=set())
        assert result.name == "Hero_conform"
        assert result.version == 1

    def test_default_template_with_preset_id_slugifies(self):
        result = resolve_output_name("Hero", existing_names=set(),
                                     preset_id="builtin:tiktok_video")
        assert result.name == "Hero_tiktok_video"
        assert result.version == 1


    def test_source_token_substitutes_anywhere_in_template(self):
        result = resolve_output_name(
            "Hero",
            existing_names=set(),
            template="{source}_conformed_{source}",
        )
        assert result.name == "Hero_conformed_Hero"

    def test_width_and_height_tokens_substitute(self):
        result = resolve_output_name(
            "Hero",
            existing_names=set(),
            template="{source}_{width}x{height}",
            target_width=1080,
            target_height=1920,
        )
        assert result.name == "Hero_1080x1920"

    def test_unknown_token_passes_through_unchanged(self):
        """Forward-compat: a template authored against a future token
        set must not break — unknown tokens stay as literal text so
        the divergence is visible in the output rather than silent."""
        result = resolve_output_name(
            "Hero",
            existing_names=set(),
            template="{source}_{aspect}",   # aspect not yet supported
        )
        assert result.name == "Hero_{aspect}"

    def test_template_with_no_tokens_works_as_prefix_form(self):
        """Designers can author templates that don't reference {source}
        at all. They'd lose source identity but the resolver doesn't
        impose a token requirement."""
        result = resolve_output_name(
            "Hero",
            existing_names=set(),
            template="CONFORMED_OUTPUT",
        )
        assert result.name == "CONFORMED_OUTPUT"

    def test_template_with_special_chars_passes_through(self):
        """Templates can carry literal `[`, `]`, brackets, spaces — the
        legacy `[DIMENSION] <label>` style is expressible as a template
        if a designer wants the old aesthetic."""
        result = resolve_output_name(
            "Hero",
            existing_names=set(),
            template="[DIMENSION] {source}",
        )
        assert result.name == "[DIMENSION] Hero"


# ---------------------------------------------------------------------------
# 2. Collision-aware bump
# ---------------------------------------------------------------------------

class TestCollisionBump:
    def test_no_collision_returns_version_1(self):
        result = resolve_output_name(
            "Hero", existing_names={"Other_D", "Title_D"}, template="{source}_D"
        )
        assert result == ResolvedName("Hero_D", 1)

    def test_single_collision_bumps_to_v2(self):
        result = resolve_output_name(
            "Hero", existing_names={"Hero_D"}, template="{source}_D"
        )
        assert result == ResolvedName("Hero_D_v2", 2)

    def test_v2_and_v3_collisions_bump_to_v4(self):
        result = resolve_output_name(
            "Hero", existing_names={"Hero_D", "Hero_D_v2", "Hero_D_v3"}, template="{source}_D"
        )
        assert result == ResolvedName("Hero_D_v4", 4)

    def test_collision_check_is_against_resolved_base(self):
        """The collision check runs AFTER template substitution.
        A template that produces `Hero_D` collides against the same
        literal regardless of how it was assembled."""
        result = resolve_output_name(
            "Hero",
            existing_names={"Hero_D"},
            template="{source}_D",
        )
        assert result.version == 2

    def test_collision_bump_only_matches_exact_strings(self):
        """Bumping is exact-string match — `Hero_DX` does NOT count as
        a collision for `Hero_D`."""
        result = resolve_output_name(
            "Hero", existing_names={"Hero_DX", "Hero_D_xyz"}, template="{source}_D"
        )
        assert result == ResolvedName("Hero_D", 1)

    def test_pathological_chain_returns_high_version_gracefully(self):
        """v1..v999 all present → resolver returns v1000 rather than
        spinning forever. Caller sees the high version and can flag it.

        Implementation detail: with `Hero_D` taken AND `_v2`..`_v999`
        taken, the resolver returns `Hero_D_v1000`."""
        existing = {"Hero_D"} | {f"Hero_D_v{n}" for n in range(2, 1000)}
        result = resolve_output_name("Hero", existing_names=existing, template="{source}_D")
        assert result == ResolvedName("Hero_D_v1000", 1000)


# ---------------------------------------------------------------------------
# 3. Resolver does NOT mutate existing_names
# ---------------------------------------------------------------------------

class TestResolverImmutability:
    def test_existing_names_not_mutated(self):
        """Caller-must-reserve contract: the resolver computes a free
        name but does NOT reserve it in `existing_names`. The caller
        (DuplicationPlanner / Babysitter) does the reservation so two
        sibling resolutions don't return the same name."""
        existing = {"Hero_D"}
        before = set(existing)
        resolve_output_name("Hero", existing_names=existing)
        assert existing == before

    def test_sequential_resolves_without_reservation_collide(self):
        """Documenting the caller-must-reserve contract: two
        consecutive calls with the same input without reserving in
        between return the same name."""
        existing = {"Hero_D"}
        r1 = resolve_output_name("Hero", existing_names=existing, template="{source}_D")
        r2 = resolve_output_name("Hero", existing_names=existing, template="{source}_D")
        assert r1.name == r2.name == "Hero_D_v2"
        # The planner's pattern is: reserve r1 between calls.
        existing.add(r1.name)
        r3 = resolve_output_name("Hero", existing_names=existing, template="{source}_D")
        assert r3.name == "Hero_D_v3"


# ---------------------------------------------------------------------------
# 4. resolve_output_name_for_preset wrapper
# ---------------------------------------------------------------------------

class TestResolveForPreset:
    def test_pulls_template_from_target(self):
        t = _target(template="{source}_TT")
        result = resolve_output_name_for_preset(
            "Hero", t, existing_names=set()
        )
        assert result.name == "Hero_TT"

    def test_pulls_width_and_height_for_token_substitution(self):
        t = _target(template="{source}_{width}x{height}", width=1080, height=1920)
        result = resolve_output_name_for_preset(
            "Hero", t, existing_names=set()
        )
        assert result.name == "Hero_1080x1920"

    def test_default_target_template_produces_preset_suffix(self):
        """A Target constructed without explicit output_name_template
        carries the v6 Pydantic default `"{source}_{preset}"`.
        `_target()` uses `id="test:tiktok"` — `_slug_preset` strips
        only the known production prefixes (builtin/custom/user), so
        `test:tiktok` becomes `test_tiktok`."""
        t = _target()
        assert t.output_name_template == "{source}_{preset}"
        result = resolve_output_name_for_preset(
            "Hero", t, existing_names=set()
        )
        assert result.name == "Hero_test_tiktok"

    def test_collision_through_preset_wrapper(self):
        t = _target(template="{source}_D")
        result = resolve_output_name_for_preset(
            "Hero", t, existing_names={"Hero_D", "Hero_D_v2"}
        )
        assert result == ResolvedName("Hero_D_v3", 3)


# ---------------------------------------------------------------------------
# 5. Pydantic Target schema integration
# ---------------------------------------------------------------------------

class TestTargetSchemaIntegration:
    def test_default_value_locked_to_v6_preset_slug(self):
        """Locking the Q4 Path A-minus default at the schema layer.
        A future change to this default would surface here and force
        a roadmap revision."""
        assert DEFAULT_OUTPUT_NAME_TEMPLATE == "{source}_{preset}"
        t = _target()
        assert t.output_name_template == DEFAULT_OUTPUT_NAME_TEMPLATE

    def test_target_validates_custom_template(self):
        t = Target(
            id="custom:billboard",
            label="Billboard",
            category="custom_signage",
            subcategory="dooh",
            width=3840,
            height=1080,
            aspect_ratio=3840 / 1080,
            aspect_label="32:9",
            source="custom",
            output_name_template="{source}_DOOH_{width}x{height}",
        )
        assert t.output_name_template == "{source}_DOOH_{width}x{height}"

    def test_empty_template_rejected(self):
        """min_length=1 on the schema rejects an empty template — empty
        string would substitute to an empty name, which would collide
        with itself trivially."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            Target(
                id="test", label="t", category="social", subcategory="x",
                width=1, height=1, aspect_ratio=1.0, aspect_label="1:1",
                source="builtin", output_name_template="",
            )

    def test_legacy_target_json_without_field_parses_with_default(self):
        """A Target persisted before Slot 12.5 (no
        `output_name_template`) parses against the new schema and
        receives the default. Additive Optional → backwards-compat."""
        legacy_blob = {
            "id": "test:legacy",
            "label": "Legacy",
            "category": "social",
            "subcategory": "tiktok",
            "width": 1080,
            "height": 1920,
            "aspect_ratio": 0.5625,
            "aspect_label": "9:16",
            "source": "builtin",
            # output_name_template is absent
        }
        t = Target.model_validate(legacy_blob)
        assert t.output_name_template == "{source}_{preset}"


# ---------------------------------------------------------------------------
# 5. {date} token — Track B / B2
# ---------------------------------------------------------------------------

class TestDateToken:
    def test_date_token_substitutes_iso_format(self):
        import datetime
        result = resolve_output_name(
            "Hero",
            existing_names=set(),
            template="{source}_{date}",
        )
        today = datetime.date.today().isoformat()
        assert result.name == f"Hero_{today}"

    def test_date_token_combines_with_other_tokens(self):
        import datetime
        result = resolve_output_name(
            "Hero",
            existing_names=set(),
            template="{source}_{preset}_{date}",
            preset_id="builtin:tiktok_video",
        )
        today = datetime.date.today().isoformat()
        assert result.name == f"Hero_tiktok_video_{today}"


# ---------------------------------------------------------------------------
# 6. sanitize_comp_name — Track B / B2
# ---------------------------------------------------------------------------

class TestSanitizeCompName:
    def test_clean_name_passes_through_unchanged(self):
        assert sanitize_comp_name("Hero_tiktok") == "Hero_tiktok"

    @pytest.mark.parametrize("illegal_char", [":", "*", "?", '"', "<", ">", "|", "/", "\\"])
    def test_each_ae_illegal_char_is_replaced(self, illegal_char):
        result = sanitize_comp_name(f"Hero{illegal_char}Name")
        assert illegal_char not in result
        assert result == "Hero_Name"

    def test_multiple_illegal_chars_collapse_to_single_underscore(self):
        assert sanitize_comp_name("Hero:*?Name") == "Hero_Name"

    def test_leading_and_trailing_whitespace_stripped(self):
        assert sanitize_comp_name("  Hero_tiktok  ") == "Hero_tiktok"

    def test_leading_and_trailing_underscore_stripped(self):
        assert sanitize_comp_name(":Hero_tiktok:") == "Hero_tiktok"

    def test_empty_result_falls_back_to_untitled(self):
        assert sanitize_comp_name("***") == "Untitled"

    def test_wired_into_resolve_output_name(self):
        """B2 — resolve_output_name must sanitize the resolved base name
        before checking for collisions, so illegal characters never
        reach Babysitter's Item.name setter."""
        result = resolve_output_name(
            "Hero:Main",
            existing_names=set(),
            template="{source}_conformed",
        )
        assert result.name == "Hero_Main_conformed"
        assert ":" not in result.name
