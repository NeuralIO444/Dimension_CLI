# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_profile_migration.py
Slot 11.5 Commit A — RED tests for the Phase 2 profile reduction
migration (9 → 4: default / social / theatrical / ooh).

Spec (locked):
  - State.json carrying an old `active_studio_profile` id maps to
    its replacement at registry init; unknown old ids fall back to
    `default`.
  - User-dir `~/Library/Application Support/Dimension/profiles/*.yaml`
    is deleted-and-reseeded on first launch after the migration ships
    (clean slate; user-dir edits to retired profiles are discarded).
  - Migration is idempotent — once the flag's set in state.json a
    second init does not re-delete the user dir.

Migration table (locked):
    A24, Disney+, trailer_theatrical            → theatrical
    Netflix, agency_commercial                  → social
    Universal_HV, news_broadcast,
        sports_broadcast, 87N                   → default
    (unknown old id)                            → default
    (no ooh remap — `ooh` is brand-new in v6)

Until the green phase ships the new YAMLs + the migration table +
the registry hook, every test below fails on ImportError /
AttributeError / missing-key. Imports of new symbols live INSIDE
test bodies.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


REPO_BASELINES_DIR = (
    Path(__file__).resolve().parents[2] / "config" / "profiles"
)


NEW_PROFILE_IDS = frozenset({"default", "social", "theatrical", "ooh"})

# Locked migration table — green phase must surface this as a
# constant on the registry module.
EXPECTED_MIGRATION_MAP = {
    "A24":                "theatrical",
    "Disney+":            "theatrical",
    "trailer_theatrical": "theatrical",
    "Netflix":            "social",
    "agency_commercial":  "social",
    "Universal_HV":       "default",
    "news_broadcast":     "default",
    "sports_broadcast":   "default",
    "87N":                "default",
}


def _build_registry(tmp_path: Path):
    """Build an isolated registry rooted at tmp_path so the real
    `~/Library/Application Support/Dimension` is never touched."""
    from logic.studio_profile_registry import StudioProfileRegistry
    user_dir = tmp_path / "profiles"
    state_parent = tmp_path
    # `state_path` is computed as `user_dir.parent / state.json` per
    # the StudioProfileRegistry constructor; passing `user_dir` keeps
    # state.json alongside the profiles dir under tmp_path.
    return StudioProfileRegistry(
        user_dir=user_dir,
        baselines_dir=REPO_BASELINES_DIR,
    )


def _write_state(state_path: Path, active_profile_id: str | None,
                 migration_applied: bool = False) -> None:
    state: dict = {}
    if active_profile_id is not None:
        state["active_studio_profile"] = active_profile_id
    if migration_applied:
        # Pinned flag name — green phase must mark migrations with
        # this key so re-init is a no-op.
        state["profile_migration_v1_applied"] = True
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))


# ── Migration table contract ───────────────────────────────────────


class TestMigrationMapShape:
    def test_migration_map_exposed_on_registry_module(self):
        from logic import studio_profile_registry as spr
        # Pinned name: `PROFILE_MIGRATION_MAP`.
        assert hasattr(spr, "PROFILE_MIGRATION_MAP"), (
            "logic.studio_profile_registry must export "
            "PROFILE_MIGRATION_MAP for the 9→4 migration"
        )
        for old, new in EXPECTED_MIGRATION_MAP.items():
            assert spr.PROFILE_MIGRATION_MAP.get(old) == new, (
                f"PROFILE_MIGRATION_MAP[{old!r}] should be {new!r}; "
                f"got {spr.PROFILE_MIGRATION_MAP.get(old)!r}"
            )

    def test_migration_targets_are_subset_of_new_ids(self):
        from logic.studio_profile_registry import PROFILE_MIGRATION_MAP
        targets = set(PROFILE_MIGRATION_MAP.values())
        # Every replacement must be one of the four new ids.
        assert targets.issubset(NEW_PROFILE_IDS), (
            f"every migration target must be in {NEW_PROFILE_IDS}; "
            f"stray targets: {targets - NEW_PROFILE_IDS}"
        )


# ── Active-profile remap ───────────────────────────────────────────


class TestActiveProfileRemap:
    @pytest.mark.parametrize("old_id,new_id",
                              list(EXPECTED_MIGRATION_MAP.items()))
    def test_old_active_id_remaps_to_replacement(self, tmp_path,
                                                   old_id, new_id):
        # Seed state.json with the retired id.
        reg = _build_registry(tmp_path)
        _write_state(reg.state_path, active_profile_id=old_id)
        # First registry call triggers migration.
        active = reg.get_active()
        assert active is not None, (
            f"active profile must not be None after migrating "
            f"{old_id!r} → {new_id!r}"
        )
        assert active.id == new_id, (
            f"old id {old_id!r} should map to {new_id!r}; "
            f"got active.id={active.id!r}"
        )

    def test_unknown_old_profile_id_falls_back_to_default(self, tmp_path):
        reg = _build_registry(tmp_path)
        _write_state(reg.state_path, active_profile_id="NeverShipped")
        active = reg.get_active()
        assert active is not None
        assert active.id == "default", (
            "unknown old ids must fall back to `default`, not None"
        )

    def test_state_json_is_rewritten_to_new_id(self, tmp_path):
        """The migration step must persist the remapped id so a
        second cold start doesn't re-trigger the remap loop."""
        reg = _build_registry(tmp_path)
        _write_state(reg.state_path, active_profile_id="Disney+")
        reg.get_active()
        on_disk = json.loads(reg.state_path.read_text())
        assert on_disk.get("active_studio_profile") == "theatrical", (
            "state.json must be re-saved with the new id after "
            "migration; got "
            f"{on_disk.get('active_studio_profile')!r}"
        )


# ── User-dir clean reseed ──────────────────────────────────────────


class TestUserDirCleanReseed:
    def test_stale_yaml_deleted_on_migration(self, tmp_path):
        """Stale 87N.yaml in the user dir must be removed by the
        migration. Only the 4 new YAMLs should remain after init."""
        reg = _build_registry(tmp_path)
        reg.user_dir.mkdir(parents=True, exist_ok=True)
        # Plant a stale retired profile.
        stale = reg.user_dir / "87N.yaml"
        stale.write_text("id: 87N\nrevision: stale\nprefixes: []\n")
        # No migration flag yet → migration must run.
        _write_state(reg.state_path, active_profile_id="87N")

        reg.get_active()

        remaining = {p.stem for p in reg.user_dir.glob("*.yaml")}
        assert remaining == NEW_PROFILE_IDS, (
            f"user-dir post-migration must contain exactly "
            f"{sorted(NEW_PROFILE_IDS)}; got {sorted(remaining)}"
        )

    def test_migration_idempotent(self, tmp_path):
        """Second init with the flag already set must NOT re-delete
        the user dir AND must not re-seed legacy baselines back
        in. A user who edited a v6 profile (post-migration) keeps
        their edits after a relaunch; the user dir should still
        contain ONLY the 4 new YAMLs."""
        reg = _build_registry(tmp_path)
        reg.user_dir.mkdir(parents=True, exist_ok=True)
        # Pretend the migration has already run — plant exactly
        # the 4 new YAMLs in the user dir.
        for pid in NEW_PROFILE_IDS:
            (reg.user_dir / f"{pid}.yaml").write_text(
                f"id: {pid}\nprefixes: []\n"
            )
        _write_state(reg.state_path, active_profile_id="default",
                     migration_applied=True)
        # User-edited keeper inside the v6 set.
        keeper = reg.user_dir / "default.yaml"
        keeper.write_text("id: default\n# user edit\nprefixes: []\n")
        original_body = keeper.read_text()

        reg.reload()  # force a re-init pass
        reg.get_active()

        assert keeper.exists(), (
            "idempotent migration must not delete an existing v6 "
            "user-dir YAML"
        )
        assert keeper.read_text() == original_body, (
            "idempotent migration must not overwrite a user-edited "
            "v6 YAML"
        )
        # Hard contract: idempotent second-init must NOT reseed
        # legacy baselines. User dir stays at exactly the 4 new
        # ids. Fails in the current state because
        # `_seed_user_dir_from_baselines` is strictly additive —
        # it will copy any baseline YAML the user dir lacks.
        remaining = {p.stem for p in reg.user_dir.glob("*.yaml")}
        assert remaining == NEW_PROFILE_IDS, (
            f"idempotent re-init must NOT seed legacy baselines back "
            f"into a migrated user dir; expected {sorted(NEW_PROFILE_IDS)} "
            f"got {sorted(remaining)}"
        )
