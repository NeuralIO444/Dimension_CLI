# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_profile_cli_lifecycle.py — #401.

StudioProfileRegistry.save_user_profile()/.delete_profile() implement a
full custom-profile lifecycle (create a new profile extending a base,
extract overrides, atomic JSON write via UserProfileStore; delete by
id, guarding base profiles) but had zero CLI or UI caller. This proves
the new `profile_cli.py create`/`delete` subcommands that wire them up.

Follows test_profile_cli_safe_area_roundtrip.py's subprocess-isolation
pattern: StudioProfileRegistry is a module-level singleton rooted at
the real `~/Library/Application Support/...` profiles dir, so each
command runs in its own subprocess with HOME redirected to a throwaway
tmp dir — this also proves persistence survives past the writing
process, the same "CEP execFiles a fresh profile_cli.py per command"
shape production uses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

REPO_PY = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CLI = os.path.join(REPO_PY, "profile_cli.py")


def _run(args, home, expect_ok=True):
    env = dict(os.environ)
    env["HOME"] = str(home)
    proc = subprocess.run(
        [sys.executable, CLI] + args,
        cwd=REPO_PY, capture_output=True, text=True, timeout=60, env=env,
    )
    if expect_ok:
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)
    return proc


class TestCreate:
    def test_create_clones_base_profile_rules(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()

        result = _run(["create", "my_studio", "--extends", "default",
                        "--display-name", "My Studio"], home)
        assert result["id"] == "my_studio"
        assert result["extends"] == "default"
        assert result["path"]

        show_result = _run(["show", "my_studio"], home)
        assert show_result["display_name"] == "My Studio"
        assert show_result["extends"] == "default"
        # Cloned from default -- same rule count as the base it copied.
        default_show = _run(["show", "default"], home)
        assert len(show_result["rules"]) == len(default_show["rules"])

    def test_create_appears_in_list(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        _run(["create", "listed_studio", "--extends", "social"], home)
        listing = _run(["list"], home)
        ids = [p["id"] for p in listing]
        assert "listed_studio" in ids
        entry = next(p for p in listing if p["id"] == "listed_studio")
        assert entry["is_base"] is False
        assert entry["extends"] == "social"

    def test_create_persists_across_fresh_process(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        _run(["create", "persisted_studio", "--extends", "default"], home)
        # Fresh subprocess = fresh REGISTRY singleton = fresh load from disk.
        show_result = _run(["show", "persisted_studio"], home)
        assert show_result["id"] == "persisted_studio"

    def test_create_with_missing_base_profile_fails(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        proc = _run(["create", "orphan", "--extends", "no_such_base"], home, expect_ok=False)
        assert proc.returncode != 0
        assert "not found" in proc.stderr

    def test_create_colliding_with_a_base_profile_id_fails(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        proc = _run(["create", "default", "--extends", "social"], home, expect_ok=False)
        assert proc.returncode != 0
        assert "already exists" in proc.stderr

    def test_create_duplicate_custom_id_fails(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        _run(["create", "dup_studio", "--extends", "default"], home)
        proc = _run(["create", "dup_studio", "--extends", "social"], home, expect_ok=False)
        assert proc.returncode != 0
        assert "already exists" in proc.stderr


class TestDelete:
    def test_delete_removes_a_custom_profile(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        _run(["create", "to_delete", "--extends", "default"], home)
        result = _run(["delete", "to_delete"], home)
        assert result == {"id": "to_delete", "deleted": True}

        listing = _run(["list"], home)
        ids = [p["id"] for p in listing]
        assert "to_delete" not in ids

    def test_delete_unknown_profile_fails(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        proc = _run(["delete", "never_existed"], home, expect_ok=False)
        assert proc.returncode != 0
        assert "not found" in proc.stderr

    def test_delete_base_profile_is_rejected(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        proc = _run(["delete", "default"], home, expect_ok=False)
        assert proc.returncode != 0
        assert "Cannot delete base profile" in proc.stderr

        # Base profile must still be intact and usable afterward.
        show_result = _run(["show", "default"], home)
        assert show_result["id"] == "default"
