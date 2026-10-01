# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_git_hooks.py

Guards the versioned git hooks in `.githooks/` and the JSX re-bundle
helper `tools/bundle_jsx.sh` (2026-09-01 audit, finding A2: a bundle
built on one branch survived `git checkout main` because `cep/jsx/` is
gitignored, so AE ran JSX the checked-out branch did not contain).

Three contracts:

1. The hooks exist and are executable — a hook that loses its +x bit
   is silently skipped by git.
2. `tools/bundle_jsx.sh --list` yields exactly the file list that
   `package.sh`'s `JSX_BUNDLED` array declares (parsed the same way
   `test_cep_jsx_bundle_sync.py` parses it). The script must not grow
   its own copy of the list.
3. `.githooks/pre-push` refuses a push whose remote ref is
   `refs/heads/main` and allows any other ref, honouring the
   `DIMENSION_ALLOW_MAIN_PUSH=1` escape hatch.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_cep_jsx_bundle_sync import _parse_jsx_bundled_list  # noqa: E402

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_HOOKS_DIR = os.path.join(_REPO_ROOT, ".githooks")
_BUNDLE_SH = os.path.join(_REPO_ROOT, "tools", "bundle_jsx.sh")
_BUNDLE_DIR = os.path.join(_REPO_ROOT, "cep", "jsx")

_HOOKS = ("pre-commit", "post-checkout", "post-merge", "pre-push")

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="hooks are bash scripts; no bash on PATH"
)


class TestHookFiles:
    @pytest.mark.parametrize("hook", _HOOKS)
    def test_hook_exists_and_is_executable(self, hook):
        path = os.path.join(_HOOKS_DIR, hook)
        assert os.path.isfile(path), f".githooks/{hook} is missing"
        assert os.access(path, os.X_OK), (
            f".githooks/{hook} is not executable — git will silently skip it. "
            "Run tools/install_hooks.sh (or chmod +x)."
        )

    def test_bundle_script_exists_and_is_executable(self):
        assert os.path.isfile(_BUNDLE_SH)
        assert os.access(_BUNDLE_SH, os.X_OK)


class TestBundleJsxScript:
    def test_list_matches_package_sh(self):
        out = subprocess.run(
            ["bash", _BUNDLE_SH, "--list"], capture_output=True, text=True, check=True
        )
        listed = [line for line in out.stdout.splitlines() if line.strip()]
        assert listed == _parse_jsx_bundled_list(), (
            "tools/bundle_jsx.sh --list disagrees with package.sh's JSX_BUNDLED. "
            "The script must parse package.sh, never carry its own list."
        )

    def test_check_mode_reports_sync_state(self):
        if not os.path.isdir(_BUNDLE_DIR):
            pytest.skip("cep/jsx/ not present (bundle never built here)")
        out = subprocess.run(
            ["bash", _BUNDLE_SH, "--check"], capture_output=True, text=True
        )
        # Either fully in sync (exit 0, no STALE lines) or every stale
        # file is named (exit 1). Anything else is a script bug.
        stale_lines = [ln for ln in out.stdout.splitlines() if ln.startswith("STALE")]
        assert out.returncode in (0, 1)
        assert (out.returncode == 1) == bool(stale_lines)


class TestPrePushHook:
    _HOOK = os.path.join(_HOOKS_DIR, "pre-push")

    def _run(self, stdin_line: str, env_extra: dict | None = None):
        env = {k: v for k, v in os.environ.items() if k != "DIMENSION_ALLOW_MAIN_PUSH"}
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["bash", self._HOOK], input=stdin_line, capture_output=True, text=True, env=env
        )

    def test_refuses_main(self):
        r = self._run("refs/heads/main aaa refs/heads/main bbb\n")
        assert r.returncode == 1
        assert "Refusing to push directly to main" in r.stderr

    def test_allows_feature_branch(self):
        r = self._run("refs/heads/feat/x aaa refs/heads/feat/x bbb\n")
        assert r.returncode == 0

    def test_escape_hatch(self):
        r = self._run(
            "refs/heads/main aaa refs/heads/main bbb\n",
            env_extra={"DIMENSION_ALLOW_MAIN_PUSH": "1"},
        )
        assert r.returncode == 0
