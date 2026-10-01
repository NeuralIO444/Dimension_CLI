# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_profiles_load.py
Slot 11.5 Commit A — schema validation for the v6 profile set
post-reduction (default / social / theatrical / ooh).

v5.6.0 had this module pinning four vertical profiles
(sports_broadcast / news_broadcast / trailer_theatrical /
agency_commercial). Slot 11.5 Phase 2 collapses the entire
9-profile baseline into 4: default / social / theatrical / ooh.
Migration table lives in `test_profile_migration.py`.

Asserts each new profile YAML:
  - parses cleanly
  - id matches filename
  - extends references a real existing profile
  - every prefix rule's tag is in the surveyor's VALID_TAGS
  - every prefix rule's gravity is a real GravityCode
  - every prefix rule's weight is a real WeightCode

Until the green phase ships the four new YAMLs under
`config/profiles/`, every parametrized schema test fails at the
`registry.get(profile_id) is not None` step. The total-count
assertion in `TestRegistryContract` also fails on the current
9-profile baseline (will pass once the legacy YAMLs are deleted
in the green phase).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import get_args

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


PROFILES_DIR = Path(__file__).resolve().parents[2] / "config" / "profiles"

NEW_PROFILES = (
    "default",
    "social",
    "theatrical",
    "ooh",
)


@pytest.fixture(scope="module")
def registry():
    """Single isolated registry for all profile-load tests in this
    module. Tmp user-dir so we don't mutate the real ~/Library state."""
    from logic.studio_profile_registry import StudioProfileRegistry
    with tempfile.TemporaryDirectory() as tmp:
        reg = StudioProfileRegistry(
            user_dir=Path(tmp) / "profiles",
            baselines_dir=PROFILES_DIR,
        )
        yield reg


# ── Per-profile schema validation ──────────────────────────────────


@pytest.mark.parametrize("profile_id", NEW_PROFILES)
class TestProfileSchema:
    def test_yaml_parses_via_registry(self, registry, profile_id):
        profile = registry.get(profile_id)
        assert profile is not None, (
            f"profile {profile_id!r} failed to load — check YAML "
            f"validity at config/profiles/{profile_id}.yaml"
        )

    def test_id_matches_filename(self, registry, profile_id):
        profile = registry.get(profile_id)
        assert profile.id == profile_id, (
            f"file is {profile_id}.yaml but `id:` is {profile.id!r}"
        )

    def test_extends_references_a_real_profile(self, registry, profile_id):
        profile = registry.get(profile_id)
        if profile.extends is None:
            return
        parent = registry.get(profile.extends)
        assert parent is not None, (
            f"{profile_id} extends {profile.extends!r} which doesn't "
            f"exist in the registry"
        )

    def test_every_prefix_tag_is_valid(self, registry, profile_id):
        profile = registry.get(profile_id)
        from core.surveyor import VALID_TAGS
        # Profile tags can also be the studio-profile vocabulary
        # (HERO/TT/LOGO/etc.) — those don't live in VALID_TAGS but
        # are valid tags per the gravity baseline. Allow either.
        from core.gravity import BASELINE_RULES
        allowed_tags = set(VALID_TAGS) | set(BASELINE_RULES.keys())
        for rule in profile.prefixes:
            assert rule.tag in allowed_tags, (
                f"{profile_id} prefix {rule.match!r} → tag={rule.tag!r} "
                f"is not a valid tag (allowed: {sorted(allowed_tags)})"
            )

    def test_every_prefix_gravity_is_valid(self, registry, profile_id):
        from models.studio_profile import GravityCode
        valid_gravities = set(get_args(GravityCode))
        profile = registry.get(profile_id)
        for rule in profile.prefixes:
            assert rule.gravity in valid_gravities, (
                f"{profile_id} prefix {rule.match!r} → "
                f"gravity={rule.gravity!r} is not a valid GravityCode "
                f"(valid: {sorted(valid_gravities)})"
            )

    def test_every_prefix_weight_is_valid(self, registry, profile_id):
        from models.studio_profile import WeightCode
        valid_weights = set(get_args(WeightCode))
        profile = registry.get(profile_id)
        for rule in profile.prefixes:
            assert rule.weight in valid_weights, (
                f"{profile_id} prefix {rule.match!r} → "
                f"weight={rule.weight!r} is not a valid WeightCode "
                f"(valid: {sorted(valid_weights)})"
            )

    def test_resolve_returns_rule_for_each_prefix(self, registry, profile_id):
        """Sanity: every authored prefix actually matches its own
        prefix string — catches typos / wrong escape characters."""
        profile = registry.get(profile_id)
        for rule in profile.prefixes:
            sample_name = rule.match + "TEST"
            resolved = profile.resolve(sample_name)
            assert resolved is not None, (
                f"{profile_id} prefix {rule.match!r} doesn't match "
                f"{sample_name!r} via profile.resolve"
            )


# ── Cross-cutting registry contract ────────────────────────────────


class TestRegistryContract:
    def test_all_four_profiles_loaded(self, registry):
        ids = {p.id for p in registry.list_profiles()}
        for profile_id in NEW_PROFILES:
            assert profile_id in ids, (
                f"{profile_id!r} not loaded by registry"
            )

    def test_total_profile_count(self, registry):
        # Slot 11.5 Phase 2 — 9-profile baseline collapsed to 4:
        # default / social / theatrical / ooh. The legacy set
        # (Universal_HV, A24, Disney+, Netflix, 87N, sports/news/
        # trailer/agency_commercial) is deleted in the green phase
        # and remapped per `PROFILE_MIGRATION_MAP`.
        ids = {p.id for p in registry.list_profiles()}
        assert ids == set(NEW_PROFILES), (
            f"expected exactly {sorted(NEW_PROFILES)} after Phase 2 "
            f"reduction; got {sorted(ids)}"
        )

    def test_no_duplicate_profile_ids(self, registry):
        ids = [p.id for p in registry.list_profiles()]
        assert len(ids) == len(set(ids)), (
            f"duplicate profile ids: {sorted(ids)}"
        )
