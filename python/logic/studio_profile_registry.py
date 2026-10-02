# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/logic/studio_profile_registry.py
Dimension Studio Profile registry — file I/O for v5.2 Nomenclature.

Profiles live as YAML in:
    ~/Library/Application Support/NeuralIO_Dimension/profiles/<id>.yaml

The repo also ships baseline profiles in `config/profiles/*.yaml` —
those are the starter set (Universal_HV, Netflix, Disney+, A24).
On first launch the registry seeds the user dir from the repo
baselines if the user dir is empty; subsequent launches read user
profiles only so studio edits aren't clobbered.

State (which profile is active) lives in:
    ~/Library/Application Support/NeuralIO_Dimension/state.json

Public API:
    REGISTRY = StudioProfileRegistry()
    REGISTRY.list_profiles()                  → list[StudioProfile]
    REGISTRY.get(profile_id)                  → StudioProfile | None
    REGISTRY.get_active()                     → StudioProfile | None
    REGISTRY.set_active(profile_id)           → bool (persists state.json)
    REGISTRY.save(profile)                    → str (path written to)
    REGISTRY.reload()                         → None
    REGISTRY.user_dir                         → Path (read-only)
    REGISTRY.to_yaml(profile)                 → str (canonical serialiser)
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from core.logger import log
from logic.user_support_dir import user_support_dir
from models.studio_profile import (
    StudioProfile,
    ProfileRule,
    SafeArea,
    merge_extends,
)
PROFILES_SUBDIR = "profiles"
STATE_FILENAME = "state.json"


# ── Slot 11.5 Phase 2 — profile reduction migration ─────────────────
#
# The 9-profile baseline (Universal_HV / Netflix / Disney+ / A24 /
# 87N / sports_broadcast / news_broadcast / agency_commercial /
# trailer_theatrical) was retired in favour of a flat four-profile
# model keyed to use case (default / social / theatrical / ooh).
# This block is the source of truth for the retired-id remap; the
# README at docs/archive/studio-profiles-pre-rationalization/ is
# the human-readable mirror — keep them in sync.

NEW_PROFILE_IDS: frozenset = frozenset(
    {"default", "social", "theatrical", "ooh"}
)

PROFILE_MIGRATION_MAP: Dict[str, str] = {
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

# state.json key marking the one-time user-dir clean-reseed.
# Idempotent: subsequent registry inits skip the wipe when this
# is true.
_MIGRATION_V1_FLAG = "profile_migration_v1_applied"

# Fallback id used when the active profile in state.json refers
# to a retired id that isn't in PROFILE_MIGRATION_MAP (e.g. a
# never-shipped or hand-typed id).
_DEFAULT_FALLBACK_ID = "default"


def _repo_baselines_dir() -> Path:
    """Starter profiles bundled with the package (or the repo checkout)."""
    from logic.config_paths import config_path
    return config_path("profiles")


class StudioProfileRegistry:
    """Filesystem-backed registry. Lazy-loads on first call; cache the
    instance and call `reload()` only when you know YAML changed under
    your feet (e.g. user edited a file via OPEN IN $EDITOR)."""

    def __init__(self,
                 user_dir: Optional[Path] = None,
                 baselines_dir: Optional[Path] = None):
        self.user_dir: Path = user_dir or (user_support_dir() / PROFILES_SUBDIR)
        self.baselines_dir: Path = baselines_dir or _repo_baselines_dir()
        self.state_path: Path = (
            user_dir.parent / STATE_FILENAME if user_dir
            else user_support_dir() / STATE_FILENAME
        )
        self._cache: Dict[str, StudioProfile] = {}
        self._loaded = False

    # ── Bootstrapping ────────────────────────────────────────────

    def _seed_user_dir_from_baselines(self) -> int:
        """Copy repo `config/profiles/*.yaml` into the user dir if the
        user dir is empty or missing. Returns count copied. Idempotent."""
        if not self.baselines_dir.exists():
            return 0
        self.user_dir.mkdir(parents=True, exist_ok=True)
        existing = {p.name for p in self.user_dir.glob("*.yaml")}
        copied = 0
        for src in sorted(self.baselines_dir.glob("*.yaml")):
            if src.name in existing:
                continue
            shutil.copy(src, self.user_dir / src.name)
            copied += 1
        if copied:
            log.info("Studio profiles seeded from baselines",
                     extra={"count": copied, "user_dir": str(self.user_dir)})
        return copied

    # ── Slot 11.5 Phase 2 — migration ────────────────────────────

    def _read_state_dict(self) -> dict:
        """Tolerant state.json reader — returns {} on missing file
        or parse failure so callers can treat it as a plain dict."""
        if not self.state_path.exists():
            return {}
        try:
            with self.state_path.open("r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:  # noqa: BLE001
            return {}

    def _write_state_dict(self, state: dict) -> bool:
        """Atomic state.json write via tmp + os.replace. Mirrors
        `set_active`'s persistence pattern."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, self.state_path)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to persist registry state",
                        extra={"error": str(e)})
            return False

    def _migrate_user_dir_if_needed(self) -> bool:
        """One-time clean-reseed when transitioning from the
        9-profile baseline (v5.6.0) to the 4-profile baseline
        (v6.0).

        Wipes every *.yaml in `user_dir` so the subsequent
        baseline-seed pass replaces them with the new canonical
        set. The migration flag in state.json
        (`profile_migration_v1_applied`) makes this idempotent —
        once set, the wipe never re-runs, so user edits to v6
        profiles survive relaunches.

        Locked decisions (Slot 11.5 spec):
          - Silent fallback. No toast, no prompt — user discovers
            the new profile set on first launch.
          - Clean reseed, NOT a merge. User edits to retired
            profiles (Disney+.yaml etc.) are discarded.

        Returns True if migration ran, False if skipped via flag."""
        state = self._read_state_dict()
        if state.get(_MIGRATION_V1_FLAG):
            return False

        wiped = 0
        if self.user_dir.exists():
            for yaml_file in self.user_dir.glob("*.yaml"):
                try:
                    yaml_file.unlink()
                    wiped += 1
                except Exception:  # noqa: BLE001 — best-effort
                                  # delete; reseed will overwrite
                                  # any leftover anyway.
                    pass

        # Set the flag BEFORE the baseline reseed runs so a crash
        # mid-seed doesn't cause an infinite delete loop on the
        # next launch. Worst case if seed fails: user dir empty,
        # registry falls through to the no-active-profile path,
        # and the next launch's `_seed_user_dir_from_baselines`
        # (which is additive) repopulates from the bundled
        # baselines.
        state[_MIGRATION_V1_FLAG] = True
        self._write_state_dict(state)
        log.info("Profile user-dir migration completed",
                 extra={"wiped": wiped,
                        "user_dir": str(self.user_dir)})
        return True

    def _migrate_active_profile_if_needed(self) -> bool:
        """Silent remap of state.json's `active_studio_profile`
        when it refers to a retired profile id.

        Lookup order:
          1. PROFILE_MIGRATION_MAP — explicit retired → new mapping.
          2. Fallback: `default` for unknown / never-shipped ids.

        Re-saves state.json with the new id so subsequent reads
        skip the remap path (idempotent until a user edits the
        active id again).

        Returns True if remap happened, False otherwise."""
        state = self._read_state_dict()
        active_id = state.get("active_studio_profile")
        if not active_id:
            return False
        if active_id in NEW_PROFILE_IDS:
            return False

        new_id = PROFILE_MIGRATION_MAP.get(active_id, _DEFAULT_FALLBACK_ID)
        state["active_studio_profile"] = new_id
        if self._write_state_dict(state):
            log.info("Active studio profile migrated",
                     extra={"from": active_id, "to": new_id})
            return True
        return False

    # ── Loading ──────────────────────────────────────────────────

    def reload(self) -> None:
        """Re-read the user dir from disk."""
        self._cache.clear()
        self._loaded = False
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        # Slot 11.5 Phase 2 — one-time user-dir clean-reseed must
        # run BEFORE the baseline seed, so the seed populates a
        # freshly-wiped dir instead of being short-circuited by
        # stale legacy YAMLs that already exist there.
        try:
            self._migrate_user_dir_if_needed()
        except Exception as e:  # noqa: BLE001
            log.warning("Profile user-dir migration failed",
                        extra={"error": str(e)})

        try:
            self._seed_user_dir_from_baselines()
        except Exception as e:
            log.warning("Profile baseline seed failed",
                        extra={"error": str(e)})

        # Slot 11.5 Phase 2 — silently remap a retired active
        # profile id (Disney+ etc.) to its v6 replacement. Runs
        # after baselines are seeded so the new id resolves to a
        # real profile on the subsequent get_active() call.
        try:
            self._migrate_active_profile_if_needed()
        except Exception as e:  # noqa: BLE001
            log.warning("Active-profile migration failed",
                        extra={"error": str(e)})

        try:
            import yaml  # type: ignore
        except ImportError:
            log.warning("pyyaml missing — Studio Profile Registry disabled")
            self._loaded = True
            return

        if not self.user_dir.exists():
            self._loaded = True
            return

        # First pass: load every profile raw (no extends resolution yet).
        raw: Dict[str, StudioProfile] = {}
        for path in sorted(self.user_dir.glob("*.yaml")):
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                profile = self._coerce_yaml(data, path)
                if profile is not None:
                    raw[profile.id] = profile
            except Exception as e:
                log.warning("Profile parse failed",
                            extra={"path": str(path), "error": str(e)})

        # Load user-created JSON profiles
        try:
            from data.user_profile_storage import UserProfileStore
            store = UserProfileStore(self.user_dir)
            for path in sorted(self.user_dir.glob("*.json")):
                if path.name == "state.json":
                    continue
                try:
                    user_prof = store.load(path.slice if hasattr(path, "slice") else path.stem)
                    if user_prof is not None:
                        studio_prof = self._coerce_user_profile(user_prof)
                        if studio_prof is not None:
                            raw[studio_prof.id] = studio_prof
                except Exception as e:
                    log.warning("User profile load failed",
                                extra={"path": str(path), "error": str(e)})
        except Exception as e:
            log.warning("User profile store init or load failed",
                        extra={"error": str(e)})

        # Second pass: resolve `extends:` chains. Tolerates one level of
        # forward declaration (parent loaded later in dir order) by
        # iterating up to N times where N = profile count.
        resolved: Dict[str, StudioProfile] = {}
        pending = dict(raw)
        for _ in range(len(raw) + 1):
            if not pending:
                break
            progress = False
            for pid, profile in list(pending.items()):
                if profile.extends is None:
                    resolved[pid] = profile
                    pending.pop(pid)
                    progress = True
                    continue
                parent = resolved.get(profile.extends)
                if parent is None:
                    # Parent not yet resolved — try next pass.
                    continue
                resolved[pid] = merge_extends(profile, parent)
                pending.pop(pid)
                progress = True
            if not progress:
                # Cycle or missing parent — keep child as-is, log.
                for pid, profile in pending.items():
                    log.warning("Profile extends could not resolve",
                                extra={"id": pid, "extends": profile.extends})
                    resolved[pid] = profile
                break

        self._cache = resolved
        self._loaded = True
        log.info("Studio profiles loaded",
                 extra={"count": len(resolved), "ids": sorted(resolved)})

    @staticmethod
    def _coerce_user_profile(user_prof) -> Optional[StudioProfile]:
        """Convert a UserProfile storage model to a StudioProfile logic model."""
        overrides = user_prof.overrides or {}
        
        # Build ProfileRule objects
        prefixes: List[ProfileRule] = []
        for raw_rule in overrides.get("prefixes", []):
            if not isinstance(raw_rule, dict):
                continue
            match = str(raw_rule.get("match", "")).rstrip("*").strip()
            if not match:
                continue
            try:
                prefixes.append(ProfileRule(
                    match=match,
                    tag=str(raw_rule.get("tag", "UNCLASS")),
                    gravity=str(raw_rule.get("gravity", "center")),
                    scale=float(raw_rule.get("scale", 1.0)),
                    weight=str(raw_rule.get("weight", "normal")),
                ))
            except Exception as e:
                log.warning("User profile rule rejected",
                            extra={"id": user_prof.id, "match": match, "error": str(e)})

        type_overrides = {
            str(k): float(v) for k, v in overrides.get("type_overrides", {}).items()
        }

        sa_raw = overrides.get("safe_area")
        if sa_raw:
            try:
                safe_area = SafeArea(**{k: float(v) for k, v in sa_raw.items()})
            except Exception:
                safe_area = SafeArea()
        else:
            safe_area = SafeArea()

        try:
            return StudioProfile(
                id=user_prof.id,
                display_name=user_prof.name,
                revision="v1.0.0",
                updated=user_prof.updated_at,
                author="User",
                description=f"User-created custom profile based on {user_prof.base_profile_id}",
                color="#8a4af3",
                extends=user_prof.base_profile_id,
                prefixes=prefixes,
                type_overrides=type_overrides,
                safe_area=safe_area,
            )
        except Exception as e:
            log.warning("User profile validation failed",
                        extra={"id": user_prof.id, "error": str(e)})
            return None

    @staticmethod
    def _coerce_yaml(data: dict, path: Path) -> Optional[StudioProfile]:
        """Translate YAML's natural shape (which uses snake_case-ish
        keys) into the StudioProfile model. Tolerant of small variations
        — `displayName`/`display_name`, `typeOverrides`/`type_overrides`,
        `safeArea`/`safe_area`."""
        if not isinstance(data, dict):
            return None

        def pick(*keys):
            for k in keys:
                if k in data and data[k] is not None:
                    return data[k]
            return None

        prefixes_raw = pick("prefixes") or []
        prefixes: List[ProfileRule] = []
        for raw_rule in prefixes_raw:
            if not isinstance(raw_rule, dict):
                continue
            # Strip a trailing '*' from match if the YAML uses glob style.
            match = str(raw_rule.get("match", "")).rstrip("*").strip()
            if not match:
                continue
            try:
                prefixes.append(ProfileRule(
                    match=match,
                    tag=str(raw_rule.get("tag", "UNCLASS")),
                    gravity=str(raw_rule.get("gravity", "center")),
                    scale=float(raw_rule.get("scale", 1.0)),
                    weight=str(raw_rule.get("weight", "normal")),
                ))
            except Exception as e:
                log.warning("Profile rule rejected",
                            extra={"path": str(path),
                                   "match": match, "error": str(e)})

        ovr_raw = pick("type_overrides", "typeOverrides") or {}
        type_overrides = {
            str(k): float(v) for k, v in (ovr_raw or {}).items()
        }

        sa_raw = pick("safe_area", "safeArea") or {}
        try:
            safe_area = SafeArea(**{k: float(v) for k, v in sa_raw.items()})
        except Exception:
            safe_area = SafeArea()

        try:
            return StudioProfile(
                id=str(pick("id", "profile") or path.stem),
                display_name=str(pick("display_name", "displayName") or ""),
                revision=str(pick("revision") or "v0.1.0"),
                updated=str(pick("updated") or ""),
                author=str(pick("author") or ""),
                description=str(pick("description") or ""),
                color=str(pick("color") or "#4a7e9a"),
                extends=pick("extends"),
                prefixes=prefixes,
                type_overrides=type_overrides,
                safe_area=safe_area,
                extreme_ribbon=pick("extreme_ribbon", "extremeRibbon"),
            )
        except Exception as e:
            log.warning("Profile validation failed",
                        extra={"path": str(path), "error": str(e)})
            return None

    # ── Public API ───────────────────────────────────────────────

    def list_profiles(self) -> List[StudioProfile]:
        self._ensure_loaded()
        # Sort: pinned-feel order — Universal_HV first if present, then
        # alphabetical. Keeps the sidebar predictable.
        items = list(self._cache.values())
        items.sort(key=lambda p: (p.id != "Universal_HV", p.id.lower()))
        return items

    def get(self, profile_id: str) -> Optional[StudioProfile]:
        self._ensure_loaded()
        return self._cache.get(profile_id)

    @staticmethod
    def _rules_equal(a: ProfileRule, b: ProfileRule) -> bool:
        """Byte-identical-for-provenance-purposes comparison, shared by
        `rule_provenance` (which rule is "inherited" for the CEP Profile
        Editor's extends badge) and `_extract_overrides` (which rule
        actually gets persisted as an override in the YAML). Audit
        finding, 2026-08-27: these two used to compare the same four
        fields independently in two places — if `ProfileRule` ever
        gains a field, updating one comparison and not the other would
        silently desync what the editor SHOWS as inherited from what
        save() actually TREATS as inherited. One comparison, one place
        to update.
        """
        return (
            a.tag == b.tag
            and a.gravity == b.gravity
            and a.scale == b.scale
            and a.weight == b.weight
        )

    def rule_provenance(self, profile: StudioProfile) -> List[dict]:
        """Track B / B3 (2026-08-26) — per-rule inherited-vs-local
        annotation for `profile_cli.py show` / the CEP Profile Editor.

        `profile.prefixes` is already the merged list `_load_all`
        produced (child rules first, then the parent's non-colliding
        rules copied in verbatim — see `merge_extends`); this walks it
        against the immediate parent's own merged rule set to tell
        which entries are byte-identical carryovers (inherited) vs.
        rules `profile` itself defines or overrides (local). Same
        comparison `_extract_overrides` uses for the save path, just
        emitted per-rule instead of filtered to overrides-only.

        Only distinguishes "local to this profile" vs "inherited from
        its immediate `extends` parent" — matches the plan's minimal
        scope (a single extends badge, not a full ancestry graph).
        """
        parent = self.get(profile.extends) if profile.extends else None
        parent_rules = {r.match: r for r in parent.prefixes} if parent else {}
        out: List[dict] = []
        for r in profile.prefixes:
            p_r = parent_rules.get(r.match)
            inherited = bool(p_r is not None and self._rules_equal(p_r, r))
            entry = r.model_dump()
            entry["is_inherited"] = inherited
            entry["source_profile"] = profile.extends if inherited else profile.id
            out.append(entry)
        return out

    def get_active(self) -> Optional[StudioProfile]:
        # Slot 11.5 Phase 2 — force the load + migration pipeline
        # to run BEFORE we read state.json for the active id.
        # Otherwise a retired id captured here pre-migration would
        # miss the cache lookup and fall through to the default
        # fallback, even though migration rewrote state.json under
        # us a moment later. Triggering _ensure_loaded first means
        # by the time we read, state.json is already post-migration.
        self._ensure_loaded()
        active_id = self._read_active_id()
        if active_id:
            p = self.get(active_id)
            if p is not None:
                return p
        # Default fallback. Slot 11.5 Phase 2 — `default` replaced
        # the v5.2 `Universal_HV` master baseline.
        return self.get(_DEFAULT_FALLBACK_ID) or (
            self.list_profiles()[0] if self.list_profiles() else None
        )

    def set_active(self, profile_id: str) -> bool:
        if self.get(profile_id) is None:
            log.warning("Cannot set unknown profile active",
                        extra={"id": profile_id})
            return False
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            state = {}
            if self.state_path.exists():
                try:
                    with self.state_path.open("r", encoding="utf-8") as f:
                        state = json.load(f) or {}
                except Exception:
                    state = {}
            state["active_studio_profile"] = profile_id
            tmp = self.state_path.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, self.state_path)
            log.info("Active studio profile set", extra={"id": profile_id})
            return True
        except Exception as e:
            log.warning("Failed to persist active profile",
                        extra={"id": profile_id, "error": str(e)})
            return False

    def _read_active_id(self) -> Optional[str]:
        if not self.state_path.exists():
            return None
        try:
            with self.state_path.open("r", encoding="utf-8") as f:
                data = json.load(f) or {}
            v = data.get("active_studio_profile")
            return str(v) if isinstance(v, str) and v else None
        except Exception:
            return None

    def save(self, profile: StudioProfile) -> str:
        """Write profile to user dir as YAML. Returns the path."""
        self.user_dir.mkdir(parents=True, exist_ok=True)
        target = self.user_dir / f"{profile.id}.yaml"
        body = self.to_yaml(profile)
        tmp = target.with_suffix(".yaml.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, target)
        # Refresh cache.
        self._cache[profile.id] = profile
        log.info("Profile saved", extra={"path": str(target)})
        return str(target)

    def save_user_profile(self, profile: StudioProfile) -> str:
        """Save a user-created profile to disk as JSON."""
        if profile.id in NEW_PROFILE_IDS:
            raise ValueError("Cannot modify base profile")
            
        from data.user_profile_storage import UserProfile, UserProfileStore
        import datetime
        
        # Look up parent profile if extends is set
        parent = None
        if profile.extends:
            parent = self.get(profile.extends)
            
        # Extract overrides relative to parent
        overrides = self._extract_overrides(profile, parent)
        
        store = UserProfileStore(self.user_dir)
        existing = store.load(profile.id)
        
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        created_at = existing.created_at if existing else now
        updated_at = now
        
        user_prof = UserProfile(
            id=profile.id,
            name=profile.display_name or profile.id,
            base_profile_id=profile.extends or "default",
            overrides=overrides,
            created_at=created_at,
            updated_at=updated_at,
        )
        
        path = store.save(user_prof)
        # Reload cache to reflect saved profile
        self.reload()
        return path

    def delete_profile(self, profile_id: str) -> None:
        """Delete a user-created profile from disk by ID."""
        if profile_id in NEW_PROFILE_IDS:
            raise ValueError("Cannot delete base profile")
            
        from data.user_profile_storage import UserProfileStore
        store = UserProfileStore(self.user_dir)
        store.delete(profile_id)
        self.reload()

    @staticmethod
    def _extract_overrides(profile: StudioProfile, parent: Optional[StudioProfile]) -> dict:
        if not parent:
            return {
                "prefixes": [r.model_dump() for r in profile.prefixes],
                "type_overrides": profile.type_overrides,
                "safe_area": profile.safe_area.model_dump(),
            }
        
        parent_rules = {r.match: r for r in parent.prefixes}
        overrides_prefixes = []
        for r in profile.prefixes:
            p_r = parent_rules.get(r.match)
            if not p_r or not StudioProfileRegistry._rules_equal(p_r, r):
                overrides_prefixes.append(r.model_dump())
                
        overrides_type = {}
        for tag, scale in profile.type_overrides.items():
            if tag not in parent.type_overrides or parent.type_overrides[tag] != scale:
                overrides_type[tag] = scale
                
        overrides_sa = {}
        if (profile.safe_area.top != parent.safe_area.top or
            profile.safe_area.right != parent.safe_area.right or
            profile.safe_area.bottom != parent.safe_area.bottom or
            profile.safe_area.left != parent.safe_area.left):
            overrides_sa = profile.safe_area.model_dump()
            
        return {
            "prefixes": overrides_prefixes,
            "type_overrides": overrides_type,
            "safe_area": overrides_sa,
        }

    @staticmethod
    def to_yaml(profile: StudioProfile) -> str:
        """Emit YAML in the layout the design HTML uses — order matters
        for diff-friendliness across studio edits."""
        lines = []
        lines.append(f"# ~/Library/Application Support/Dimension/profiles/{profile.id}.yaml")
        if profile.revision and profile.updated:
            lines.append(f"# revision: {profile.revision}  ·  updated: {profile.updated}")
        lines.append("")
        lines.append(f"id:           {profile.id}")
        if profile.display_name:
            lines.append(f'display_name: "{profile.display_name}"')
        lines.append(f'revision:     "{profile.revision}"')
        if profile.author:
            lines.append(f'author:       "{profile.author}"')
        if profile.updated:
            lines.append(f'updated:      "{profile.updated}"')
        if profile.color:
            lines.append(f'color:        "{profile.color}"')
        if profile.extends:
            lines.append(f'extends:      "{profile.extends}"')
        if profile.description:
            lines.append("description: |")
            for line in profile.description.splitlines() or [profile.description]:
                lines.append(f"  {line}")
        lines.append("")
        lines.append("safe_area:")
        lines.append(f"  top:    {profile.safe_area.top}")
        lines.append(f"  right:  {profile.safe_area.right}")
        lines.append(f"  bottom: {profile.safe_area.bottom}")
        lines.append(f"  left:   {profile.safe_area.left}")
        lines.append("")
        lines.append("prefixes:")
        for r in profile.prefixes:
            lines.append(f'  - match:   "{r.match}"')
            lines.append(f"    tag:     {r.tag}")
            lines.append(f"    gravity: {r.gravity}")
            lines.append(f"    scale:   {r.scale:.2f}")
            lines.append(f"    weight:  {r.weight}")
        if profile.type_overrides:
            lines.append("")
            lines.append("type_overrides:")
            for tag, value in profile.type_overrides.items():
                lines.append(f"  {tag}: {value:.2f}")
        return "\n".join(lines) + "\n"


# Module-level singleton used by the UI layer. Tests should construct
# their own StudioProfileRegistry instance with explicit dirs.
REGISTRY = StudioProfileRegistry()
