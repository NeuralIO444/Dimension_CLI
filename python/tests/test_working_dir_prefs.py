# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_working_dir_prefs.py
Configurable working directory (2026-08-23) — Python half of the contract.

The panel resolves where Dimension writes its output from two persisted
preferences. This module proves the Python side of that contract:

  - `working_dir_mode` / `working_dir_custom` exist in `_DEFAULTS`, so
    `prefs_cli.py set` accepts them instead of rejecting them as unknown
    keys (the same gate `license_*` had to clear in M1 PR1).
  - The shipped defaults are "auto" and "" — a fresh install resolves
    beside the AE project with no configuration.
  - Both keys round-trip through save() / reload() and are visible to
    `prefs_cli get`, which is the only channel cep/js/working_dir_ui.js
    has for reading them.

Deliberately NOT tested here: the resolution rule itself. That lives in
cep/js/working_dir.js and is covered by cep/tests/working_dir.test.js
(`npm test`) — it is JS and not importable from pytest. Splitting it
this way is intentional: CLAUDE.md's standing warning is that a Python
fixture must never be trusted to prove a JS/JSX-side contract.

Follows test_onboarding_pref.py's isolated_prefs pattern so this never
touches the real ~/Library state.json.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PREFS_CLI = os.path.join(_REPO, "python", "prefs_cli.py")

_WORKING_DIR_KEYS = {
    "working_dir_mode": "auto",
    "working_dir_custom": "",
}


@pytest.fixture
def isolated_prefs(tmp_path, monkeypatch):
    """Redirect state_json_path() to a tmp file so the user's real
    state.json is never read or written by these tests."""
    import logic.preferences_state as ps
    target = tmp_path / "state.json"
    monkeypatch.setattr(ps, "state_json_path", lambda: target)
    monkeypatch.setattr(ps.preferences, "_loaded", False)
    monkeypatch.setattr(ps.preferences, "_state", {})
    return target


class TestDefaults:
    def test_both_keys_present_in_defaults(self):
        """`prefs_cli set` rejects any key absent from _DEFAULTS, so a
        missing entry here silently breaks the panel's save path."""
        from logic.preferences_state import _Preferences
        for key in _WORKING_DIR_KEYS:
            assert key in _Preferences._DEFAULTS, f"{key} missing from _DEFAULTS"

    def test_default_values_are_auto_and_empty(self):
        """Fresh install must resolve beside the AE project without the
        user configuring anything."""
        from logic.preferences_state import _Preferences
        for key, expected in _WORKING_DIR_KEYS.items():
            assert _Preferences._DEFAULTS[key] == expected

    def test_fresh_install_reads_defaults(self, isolated_prefs):
        """No state.json on disk at all — attribute access must fall
        through to the default rather than raising."""
        import logic.preferences_state as ps
        assert not isolated_prefs.exists()
        assert ps.preferences.working_dir_mode == "auto"
        assert ps.preferences.working_dir_custom == ""


class TestRoundTrip:
    def test_custom_path_survives_save_and_reload(self, isolated_prefs):
        import logic.preferences_state as ps
        ps.preferences.working_dir_mode = "custom"
        ps.preferences.working_dir_custom = "/Volumes/Work/Dimension"
        ps.preferences.save()

        ps.preferences.reload()
        assert ps.preferences.working_dir_mode == "custom"
        assert ps.preferences.working_dir_custom == "/Volumes/Work/Dimension"

    def test_save_does_not_clobber_unrelated_keys(self, isolated_prefs):
        """save() is read-modify-write; writing a working-dir key must
        not drop active_studio_profile or any other foreign key."""
        import logic.preferences_state as ps
        isolated_prefs.write_text(
            json.dumps({"active_studio_profile": "social", "enable_soe": False}),
            encoding="utf-8",
        )
        ps.preferences.reload()
        ps.preferences.working_dir_mode = "custom"
        ps.preferences.save()

        on_disk = json.loads(isolated_prefs.read_text(encoding="utf-8"))
        assert on_disk["active_studio_profile"] == "social"
        assert on_disk["enable_soe"] is False
        assert on_disk["working_dir_mode"] == "custom"

    def test_path_with_spaces_round_trips_verbatim(self, isolated_prefs):
        """Real client folders have spaces; a path mangled on the way
        through would send output somewhere the panel cannot find."""
        import logic.preferences_state as ps
        messy = "/Users/matt/Desktop/87N Reels (converted)/Dimension"
        ps.preferences.working_dir_custom = messy
        ps.preferences.save()
        ps.preferences.reload()
        assert ps.preferences.working_dir_custom == messy


class TestPrefsCli:
    """The CLI is the panel's only channel to these values."""

    def _run(self, args, home):
        env = dict(os.environ, HOME=str(home))
        return subprocess.run(
            [sys.executable, _PREFS_CLI] + args,
            capture_output=True, text=True, env=env, cwd=_REPO,
        )

    def test_get_exposes_both_keys(self, tmp_path):
        r = self._run(["get"], tmp_path)
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        for key, expected in _WORKING_DIR_KEYS.items():
            assert key in data, f"{key} absent from `prefs_cli get`"
            assert data[key] == expected

    def test_set_then_get_round_trip(self, tmp_path):
        assert self._run(["set", "working_dir_mode", "custom"], tmp_path).returncode == 0
        assert self._run(
            ["set", "working_dir_custom", "/tmp/dim_wd_test"], tmp_path).returncode == 0

        data = json.loads(self._run(["get"], tmp_path).stdout)
        assert data["working_dir_mode"] == "custom"
        assert data["working_dir_custom"] == "/tmp/dim_wd_test"

    def test_path_is_not_coerced_to_a_number(self, tmp_path):
        """prefs_cli._coerce turns bare digits into ints. A directory
        named e.g. "2026" must still come back as a string, or
        path.join() in the panel would throw on it."""
        r = self._run(["set", "working_dir_custom", "/tmp/2026"], tmp_path)
        assert r.returncode == 0, r.stderr
        data = json.loads(self._run(["get"], tmp_path).stdout)
        assert isinstance(data["working_dir_custom"], str)
        assert data["working_dir_custom"] == "/tmp/2026"
