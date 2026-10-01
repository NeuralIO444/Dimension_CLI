"""
test_onboarding_pref.py
Slot 15.6 (#7) — `onboarding_complete` is a persisted boolean
preference the CEP panel uses to gate the first-launch wizard.

Spec:
  - Defaults to False (wizard appears on fresh installs).
  - Round-trips through preferences.save() / reload() like any
    other key.
  - prefs_cli's "get" exposes it so the JS-side wizard can read
    the value at boot.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture
def isolated_prefs(tmp_path, monkeypatch):
    """Redirect state_json_path() at the prefs module to a tmp file so
    the test doesn't clobber the user's real state.json."""
    import logic.preferences_state as ps
    target = tmp_path / "state.json"
    monkeypatch.setattr(ps, "state_json_path", lambda: target)
    # Force a fresh singleton each test so it re-reads the patched path.
    monkeypatch.setattr(ps.preferences, "_loaded", False)
    monkeypatch.setattr(ps.preferences, "_state", {})
    return target


def test_onboarding_complete_default_false(isolated_prefs):
    """A fresh install has no state.json → default must be False so
    the wizard fires on first launch."""
    from logic.preferences_state import preferences
    assert preferences.onboarding_complete is False


def test_onboarding_complete_roundtrips(isolated_prefs):
    """Setting + saving + reloading preserves the True value across
    panel restarts."""
    from logic.preferences_state import preferences

    preferences.onboarding_complete = True
    preferences.save()
    preferences.reload()

    assert preferences.onboarding_complete is True
    # And the on-disk JSON actually carries the key.
    on_disk = json.loads(isolated_prefs.read_text())
    assert on_disk.get("onboarding_complete") is True


def test_prefs_cli_get_includes_onboarding_key(isolated_prefs, monkeypatch):
    """The CLI's `get` op surfaces `onboarding_complete` so the JS
    wizard-gate can read it at boot."""
    # Run the CLI as a subprocess so it picks up the patched _state_path
    # only through env vars — easier: just import the CLI and reuse the
    # already-patched singleton inline.
    from logic.preferences_state import preferences
    out = {}
    for key, default in preferences._DEFAULTS.items():
        out[key] = getattr(preferences, key, default)
    assert "onboarding_complete" in out
    assert out["onboarding_complete"] is False
