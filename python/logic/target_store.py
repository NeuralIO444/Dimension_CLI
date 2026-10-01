# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
TargetStore — persistence layer for the target database.

Holds three things on disk:
  • customs   — user-defined Target rows (signage, DOOH, one-offs)
  • favorites — list of Target ids the user has starred (built-in OR custom)
  • last_used — id of the most recently picked Target (for modal pre-select)

File location: ~/Library/Application Support/NeuralIO_Dimension/dimension_targets.json
                (matches the PID file / repo_path.txt convention used elsewhere)

The built-in catalog is NOT in this file. It lives in code at
`python/data/target_catalog.py` and ships with the app.

Migration: on first run, if the legacy v4.1 `presets.json` exists in the
repo root, its entries are imported as customs (labeled `[v4.1] <name>`)
and the original file is renamed to `presets.json.v41.bak` so we never
re-import it.
"""

import json
import os
import re
from typing import List, Optional

from pydantic import ValidationError

# Local module imports — `python/` is on sys.path via main_window.py's bootstrap.
from models.target import Target, TargetStore
from data.target_catalog import BUILTIN_TARGETS
from core.logger import log


APP_SUPPORT = os.path.expanduser("~/Library/Application Support/NeuralIO_Dimension")
STORE_PATH = os.path.join(APP_SUPPORT, "dimension_targets.json")


def _slugify(text: str) -> str:
    """Lowercase, alnum + underscores. Used for stable custom-target ids."""
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower())
    return s.strip("_") or "untitled"


class TargetStoreManager:
    """Lifecycle wrapper around TargetStore on disk. Owns load, save, mutate.

    The UI layer never touches the JSON file directly — it goes through this
    class so atomic-write semantics, validation, and the migration shim all
    live in one place.
    """

    def __init__(self, store_path: str = STORE_PATH):
        self.store_path = store_path
        self.store: TargetStore = TargetStore()
        self.load()

    # ─── disk ───────────────────────────────────────────────────────────

    def load(self) -> None:
        """Load the store from disk. On first run, run the v4.1 migration
        before persisting an empty file so the user immediately sees their
        old presets in the Custom drawer."""
        os.makedirs(APP_SUPPORT, exist_ok=True)

        if not os.path.exists(self.store_path):
            self.store = TargetStore()
            self._migrate_legacy_presets()
            self.save()
            return

        try:
            with open(self.store_path, "r") as f:
                raw = json.load(f)
            self.store = TargetStore.model_validate(raw)
            log.info("TargetStore loaded", extra={
                "customs": len(self.store.customs),
                "favorites": len(self.store.favorites),
                "last_used": self.store.last_used,
            })
        except (json.JSONDecodeError, ValidationError, OSError) as e:
            log.warning("TargetStore load failed; starting empty", extra={"err": str(e)})
            self.store = TargetStore()
            self.save()

        # Garbage-collect favorites that no longer resolve to anything (e.g.
        # a built-in id was renamed in code, or the user deleted a custom).
        self._prune_dangling_refs()

    def save(self) -> None:
        """Atomic write — tmp file + rename — so a crash mid-write can never
        leave a half-truncated JSON on disk."""
        tmp = self.store_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.store.model_dump(), f, indent=2)
        os.replace(tmp, self.store_path)

    # ─── lookups ────────────────────────────────────────────────────────

    def all_targets(self) -> List[Target]:
        """Built-ins + store customs + ~/.dimension/preset_customs.json.

        First-write-wins on id: factory builtins are never replaced, then
        ``dimension_targets.json`` customs, then the CEP/M5 JSON file.
        Disk-file rows are lookup-only — they are not written back into
        the TargetStore file.
        """
        seen = set()
        out: List[Target] = []
        for t in list(BUILTIN_TARGETS) + list(self.store.customs) + self._customs_from_disk():
            if t.id in seen:
                continue
            seen.add(t.id)
            out.append(t)
        return out

    def _customs_from_disk(self) -> List[Target]:
        from logic.preset_customs import load_customs, targets_from_customs

        return targets_from_customs(load_customs())

    def by_id(self, target_id: str) -> Optional[Target]:
        for t in self.all_targets():
            if t.id == target_id:
                return t
        return None

    # ─── customs CRUD ───────────────────────────────────────────────────

    def add_custom(
        self,
        *,
        label: str,
        width: int,
        height: int,
        aspect_label: Optional[str] = None,
        subcategory: str = "user",  # "user" or "dooh"
        duration: Optional[float] = None,
        fps: Optional[float] = None,
        output_name_template: Optional[str] = None,
    ) -> Target:
        """Create + persist a new custom target. Aspect ratio is auto-computed.
        ID is derived from a slug of the label, with a numeric suffix if it
        collides with an existing custom."""
        base_slug = _slugify(label)
        slug = base_slug
        n = 2
        existing_ids = {t.id for t in self.store.customs}
        while f"custom:{slug}" in existing_ids:
            slug = f"{base_slug}_{n}"
            n += 1

        if not aspect_label:
            ratio = width / height
            aspect_label = f"{ratio:.2f}:1" if ratio >= 1 else f"1:{(1/ratio):.2f}"

        target = Target.make(
            id=f"custom:{slug}",
            label=label,
            category="custom_signage",
            subcategory=subcategory,
            width=width,
            height=height,
            aspect_label=aspect_label,
            source="custom",
            duration=duration,
            fps=fps,
            output_name_template=output_name_template,
        )
        self.store.customs.append(target)
        self.save()
        log.info("Custom target added", extra={"id": target.id, "w": width, "h": height})
        return target

    def remove_custom(self, target_id: str) -> bool:
        before = len(self.store.customs)
        self.store.customs = [t for t in self.store.customs if t.id != target_id]
        # Removing a target also drops it from favorites and last_used.
        self.store.favorites = [fid for fid in self.store.favorites if fid != target_id]
        if self.store.last_used == target_id:
            self.store.last_used = None
        if len(self.store.customs) != before:
            self.save()
            return True
        return False

    # ─── favorites / last-used ──────────────────────────────────────────
    #
    # The accessor cluster that used to live here (is_favorite,
    # toggle_favorite, favorite_targets, set_last_used, last_used_target)
    # was deleted 2026-09-01: it backed a starred-targets UI that was
    # never built, and the reachability audit found zero callers outside
    # this module. `TargetStore.favorites` / `.last_used` are KEPT — they
    # are persisted fields in the user's targets.json, `remove_custom`
    # maintains them, and `_prune_dangling_refs` garbage-collects them, so
    # dropping them would be a data-schema change for no gain. Recover the
    # accessors from git history if the UI is ever built:
    #   git log --diff-filter=D -S toggle_favorite -- python/logic/target_store.py

    # ─── housekeeping ───────────────────────────────────────────────────

    def _prune_dangling_refs(self) -> None:
        """Drop favorites and last_used that don't resolve. Built-ins can be
        removed/renamed across app versions; this keeps the store self-healing."""
        valid_ids = {t.id for t in self.all_targets()}
        before_fav = len(self.store.favorites)
        self.store.favorites = [fid for fid in self.store.favorites if fid in valid_ids]
        if self.store.last_used and self.store.last_used not in valid_ids:
            self.store.last_used = None
        if len(self.store.favorites) != before_fav:
            log.info("Pruned dangling favorite refs", extra={"removed": before_fav - len(self.store.favorites)})
            self.save()

    # ─── v4.1 migration ─────────────────────────────────────────────────

    def _migrate_legacy_presets(self) -> None:
        """Read repo-root presets.json (v4.1 PresetManager format) once, on
        first run only. Convert each entry into a custom target so the user's
        old work isn't lost. Then rename the source file so we never re-import."""
        # Walk up from this file to find a plausible repo root that has a
        # presets.json. We don't import preset_manager.PRESETS_PATH because
        # that module's import has UI side effects.
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            repo_root = os.path.abspath(os.path.join(here, "..", ".."))
        except Exception:
            return

        # A frozen end-user install never has a `.git` next to its
        # (temp-extracted) source, so this signal only trips inside a dev
        # checkout or CI clone — exactly where `presets.json` is the
        # tracked, shipped sample file, not a real user's abandoned v4.1
        # config. Migrating (and destructively renaming) that tracked file
        # corrupts the working tree on every fresh clone; skip it there.
        #
        # exists(), NOT isdir(): in a linked `git worktree` the repo marker
        # is a FILE containing `gitdir: /path/to/.git/worktrees/<name>`, not
        # a directory. isdir() returned False there, the guard fell through,
        # and running pytest inside a worktree DELETED the tracked
        # presets.json — the exact corruption this check exists to prevent.
        # Observed 2026-08-29 the first time the suite was run from a
        # worktree.
        if os.path.exists(os.path.join(repo_root, ".git")):
            return

        legacy_path = os.path.join(repo_root, "presets.json")
        if not os.path.exists(legacy_path):
            return

        try:
            with open(legacy_path, "r") as f:
                legacy_data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("v4.1 presets.json unreadable; skipping migration", extra={"err": str(e)})
            return

        if not isinstance(legacy_data, list):
            log.warning("v4.1 presets.json is not a list; skipping migration")
            return

        migrated = 0
        for item in legacy_data:
            try:
                label = str(item.get("label") or item.get("id") or "Untitled v4.1 Preset")
                w = int(item["width"])
                h = int(item["height"])
            except (KeyError, TypeError, ValueError):
                continue

            # Skip if a built-in already covers this exact size — no point
            # adding noise to the Custom drawer for sizes we already have.
            if any(b.width == w and b.height == h for b in BUILTIN_TARGETS):
                continue

            tagged_label = f"[v4.1] {label}"
            self.add_custom(label=tagged_label, width=w, height=h, subcategory="user")
            migrated += 1

        # Move the legacy file out of the way so this only runs once.
        try:
            backup_path = legacy_path + ".v41.bak"
            os.replace(legacy_path, backup_path)
            log.info("v4.1 presets migrated", extra={"count": migrated, "backup": backup_path})
        except OSError as e:
            log.warning("v4.1 presets.json could not be archived", extra={"err": str(e)})
