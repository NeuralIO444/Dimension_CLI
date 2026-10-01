# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_preferences_state.py
M1 PR1 — trial-license prefs keys (docs/roadmap/2026-07-18-trial-license-system-spec.md §4.1).

Spec:
  - `_DEFAULTS` carries every `license_*` key so `prefs_cli.py set`
    doesn't reject them as unknown (G5 in the spec: state persists in
    the existing prefs store, no second config root).
  - Fresh install (no state.json) → status defaults to "none" and
    every other license field is empty/False — the CEP badge must
    read UNACTIVATED with no key present, not crash on a missing key.
  - Round-trips through preferences.save() / reload() like any other
    key (mirrors test_onboarding_pref.py's isolated_prefs pattern so
    this test never touches the real ~/Library state.json).
  - prefs_cli's "get" exposes every license key so cep/js/license.js
    can compute status purely from what the CLI returns.

This module deliberately does NOT test Gumroad activation or status
DERIVATION logic (trial/expired/full computation) — that's PR2's
`license.js` pure functions, which live in JS and aren't importable
here. This file only proves the Python-side data model is sound.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

_LICENSE_KEYS = {
    "license_key": "",
    "license_status": "none",
    "license_product": "",
    "license_product_id": "",
    "license_email": "",
    "license_activated_at": "",
    "license_expires_at": "",
    "license_last_check_at": "",
    "license_last_error": "",
    "license_dev_bypass": False,
}


@pytest.fixture
def isolated_prefs(tmp_path, monkeypatch):
    """Redirect state_json_path() at the prefs module to a tmp file so
    the test doesn't clobber the user's real state.json."""
    import logic.preferences_state as ps
    target = tmp_path / "state.json"
    monkeypatch.setattr(ps, "state_json_path", lambda: target)
    monkeypatch.setattr(ps.preferences, "_loaded", False)
    monkeypatch.setattr(ps.preferences, "_state", {})
    return target


class TestLicenseDefaults:
    def test_all_license_keys_present_in_defaults(self):
        from logic.preferences_state import preferences

        for key in _LICENSE_KEYS:
            assert key in preferences._DEFAULTS, (
                f"{key!r} missing from _DEFAULTS — prefs_cli.py set "
                f"{key} ... would reject it as an unknown preference"
            )

    def test_fresh_install_defaults_match_spec(self, isolated_prefs):
        """No state.json on disk → every license field reads its
        spec-mandated default. In particular license_status == "none"
        so the CEP badge shows UNACTIVATED, not a crash on a missing
        attribute."""
        from logic.preferences_state import preferences

        for key, expected in _LICENSE_KEYS.items():
            assert getattr(preferences, key) == expected

    def test_license_dev_bypass_defaults_false(self, isolated_prefs):
        """Explicit spec requirement (§6.6): bypass must never be
        silently on. A fresh install with no prefs file must not
        grant pipeline access."""
        from logic.preferences_state import preferences

        assert preferences.license_dev_bypass is False


class TestLicenseRoundTrip:
    def test_activation_fields_roundtrip(self, isolated_prefs):
        """Setting the fields an activation would write, saving, and
        reloading preserves them across panel restarts (same
        atomic-write path as every other pref)."""
        from logic.preferences_state import preferences

        preferences.license_key = "TEST-KEY-1234"
        preferences.license_status = "trial"
        preferences.license_product = "trial"
        preferences.license_product_id = "abc123"
        preferences.license_email = "tester@example.com"
        preferences.license_activated_at = "2026-07-18T00:00:00Z"
        preferences.license_expires_at = "2026-08-17T00:00:00Z"
        preferences.save()
        preferences.reload()

        assert preferences.license_key == "TEST-KEY-1234"
        assert preferences.license_status == "trial"
        assert preferences.license_product == "trial"
        assert preferences.license_product_id == "abc123"
        assert preferences.license_email == "tester@example.com"
        assert preferences.license_activated_at == "2026-07-18T00:00:00Z"
        assert preferences.license_expires_at == "2026-08-17T00:00:00Z"

        on_disk = json.loads(isolated_prefs.read_text())
        assert on_disk.get("license_status") == "trial"
        assert on_disk.get("license_key") == "TEST-KEY-1234"

    def test_save_does_not_clobber_unrelated_keys(self, isolated_prefs):
        """save() is read-modify-write — an unrelated key already on
        disk (e.g. active_studio_profile, written by a different
        module) must survive a license-only save."""
        from logic.preferences_state import preferences

        isolated_prefs.parent.mkdir(parents=True, exist_ok=True)
        isolated_prefs.write_text(json.dumps({"active_studio_profile": "social"}))
        preferences.reload()

        preferences.license_status = "trial"
        preferences.save()

        on_disk = json.loads(isolated_prefs.read_text())
        assert on_disk.get("active_studio_profile") == "social"
        assert on_disk.get("license_status") == "trial"


class TestPrefsCliSurface:
    def test_prefs_cli_get_includes_every_license_key(self, isolated_prefs):
        """Mirrors prefs_cli.py's `get` op (merge _DEFAULTS with loaded
        state) so the CEP panel's license.js can compute status from
        exactly what the CLI returns — no key the JS side needs is
        missing from this dict."""
        from logic.preferences_state import preferences

        out = {}
        for key, default in preferences._DEFAULTS.items():
            out[key] = getattr(preferences, key, default)

        for key in _LICENSE_KEYS:
            assert key in out

    def test_prefs_cli_set_accepts_license_key(self, isolated_prefs):
        """Unknown-key rejection (prefs_cli.py's `set` guard) must NOT
        fire for any license_* key — this is the concrete failure mode
        the spec's G5 requirement guards against."""
        from logic.preferences_state import preferences

        assert "license_key" in preferences._DEFAULTS
        assert "license_status" in preferences._DEFAULTS
        assert "license_dev_bypass" in preferences._DEFAULTS
