# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_target_store_migration_guard.py

`TargetStoreManager._migrate_legacy_presets()` imports a v4.1 `presets.json`
and then DESTRUCTIVELY renames it to `presets.json.v41.bak` so the import
only happens once. That is right for a real end-user install, where the file
is a leftover config.

It is very wrong inside the repo, where `presets.json` is a tracked, shipped
sample file. The migration is therefore guarded by "is there a repo marker
next to it?".

WHY THIS TEST EXISTS: that guard used `os.path.isdir(repo_root/".git")`. In a
linked `git worktree` the marker is a FILE — it contains
`gitdir: /path/to/.git/worktrees/<name>` — not a directory. isdir() returned
False, the guard fell straight through, and simply RUNNING PYTEST from a
worktree deleted the tracked `presets.json`. Found 2026-08-29, the first time
the suite was run from one.

The distinction is invisible in a normal clone, which is why it survived:
every prior run happened somewhere the marker really was a directory.
"""

from __future__ import annotations

import json
import os
import sys


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from logic.target_store import TargetStoreManager  # noqa: E402


LEGACY_PRESETS = [
    {"label": "Legacy Billboard", "width": 3000, "height": 1000},
]


def _fake_repo(tmp_path, marker: str | None):
    """Build a fake repo root two levels below `logic/target_store.py`.

    `marker` is "dir" (normal clone), "file" (linked worktree), or None
    (a frozen end-user install, where migration SHOULD run).
    """
    root = tmp_path / "repo"
    (root / "python" / "logic").mkdir(parents=True)
    (root / "presets.json").write_text(json.dumps(LEGACY_PRESETS))
    if marker == "dir":
        (root / ".git").mkdir()
    elif marker == "file":
        # Exactly what `git worktree add` writes.
        (root / ".git").write_text(
            "gitdir: /somewhere/.git/worktrees/my-worktree\n")
    return root


def _run_migration(tmp_path, monkeypatch, marker):
    """Run the migration with __file__ pointed at the fake repo."""
    root = _fake_repo(tmp_path, marker)
    fake_module_file = str(root / "python" / "logic" / "target_store.py")
    monkeypatch.setattr("logic.target_store.__file__", fake_module_file)

    store = TargetStoreManager.__new__(TargetStoreManager)
    store.store_path = str(tmp_path / "store.json")
    store._targets = []
    store.add_custom = lambda **kw: None   # migration's only side effect we ignore
    store._migrate_legacy_presets()
    return root


class TestRepoMarkerGuard:
    def test_a_normal_clone_is_protected(self, tmp_path, monkeypatch):
        root = _run_migration(tmp_path, monkeypatch, marker="dir")
        assert (root / "presets.json").is_file(), \
            "migration ate the tracked presets.json in a normal clone"
        assert not (root / "presets.json.v41.bak").exists()

    def test_a_git_worktree_is_protected(self, tmp_path, monkeypatch):
        """The regression. `.git` here is a FILE, not a directory — the
        distinction that made isdir() fall through and delete the file."""
        root = _run_migration(tmp_path, monkeypatch, marker="file")
        assert (root / "presets.json").is_file(), \
            "migration deleted the tracked presets.json inside a git worktree"
        assert not (root / "presets.json.v41.bak").exists(), \
            "migration archived the tracked presets.json inside a git worktree"

    def test_migration_still_runs_where_there_is_no_repo_marker(self, tmp_path, monkeypatch):
        """The guard must not become 'never migrate'. A real end-user install
        has no `.git` at all, and their abandoned v4.1 presets SHOULD be
        imported and archived — that is the whole point of the function."""
        root = _run_migration(tmp_path, monkeypatch, marker=None)
        assert not (root / "presets.json").exists(), \
            "legacy presets.json should have been archived for a real install"
        assert (root / "presets.json.v41.bak").is_file(), \
            "migration did not archive the legacy file"


class TestGuardImplementation:
    def test_guard_uses_exists_not_isdir(self):
        """Source-level backstop. The behavioural tests above cover the bug,
        but this names the exact call that caused it, so a future edit back to
        isdir() fails with an explanation rather than a mystery."""
        path = os.path.join(os.path.dirname(__file__), "..",
                            "logic", "target_store.py")
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        assert 'os.path.isdir(os.path.join(repo_root, ".git"))' not in src, (
            "the repo-marker guard is back on isdir(), which is False for a "
            "git worktree's .git FILE — see this module's docstring")
        assert 'os.path.exists(os.path.join(repo_root, ".git"))' in src
