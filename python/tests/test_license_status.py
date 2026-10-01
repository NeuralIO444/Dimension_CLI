# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_license_status.py

Covers logic/license_status.py — the Python port of cep/js/license.js's
computeLicenseStatus()/_bypassEligible()/_bypassActive(), added so
dimension_server.py (the Dashboard's backend, a separate long-running
Python process) can gate its own pipeline-triggering endpoint the same way
the CEP panel gates SCAN/EXECUTE, reading the same shared state.json.

Every branch here should have a matching branch in license.js's
computeLicenseStatus() (lines 55-154 as of this writing) — if a future
change to one side isn't mirrored here, that's exactly the kind of
producer/consumer drift CLAUDE.md's sharp edges warn about.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from logic.license_status import (
    bypass_active,
    bypass_eligible,
    compute_license_status,
    get_current_status,
)

_DAY_MS = 24 * 3600 * 1000


class TestComputeLicenseStatus:
    def test_bypass_wins_over_everything(self):
        prefs = {"license_status": "revoked", "license_email": "a@b.com"}
        s = compute_license_status(prefs, now_ms=1_000_000, bypass=True)
        assert s["status"] == "bypass"
        assert s["canUsePipeline"] is True
        assert s["daysRemaining"] is None
        assert s["email"] == "a@b.com"

    def test_revoked(self):
        prefs = {
            "license_status": "revoked",
            "license_expires_at": "2026-01-01T00:00:00Z",
            "license_email": "a@b.com",
            "license_product": "trial",
        }
        s = compute_license_status(prefs, now_ms=1_000_000, bypass=False)
        assert s["status"] == "revoked"
        assert s["canUsePipeline"] is False
        assert s["daysRemaining"] == 0
        assert s["expiresAt"] == "2026-01-01T00:00:00Z"
        assert s["product"] == "trial"

    def test_none_when_no_key(self):
        s = compute_license_status({}, now_ms=1_000_000, bypass=False)
        assert s["status"] == "none"
        assert s["canUsePipeline"] is False
        assert s["needsOnlineActivate"] is True

    def test_none_when_key_but_no_activated_at(self):
        prefs = {"license_key": "ABC-123"}
        s = compute_license_status(prefs, now_ms=1_000_000, bypass=False)
        assert s["status"] == "none"

    def test_full_product(self):
        prefs = {
            "license_key": "ABC-123",
            "license_activated_at": "2026-01-01T00:00:00Z",
            "license_product": "full",
        }
        s = compute_license_status(prefs, now_ms=1_000_000, bypass=False)
        assert s["status"] == "full"
        assert s["canUsePipeline"] is True
        assert s["daysRemaining"] is None

    def test_trial_with_days_remaining(self):
        now_ms = 1_700_000_000_000.0
        activated_ms = now_ms - 5 * _DAY_MS
        expires_ms = activated_ms + 30 * _DAY_MS
        prefs = {
            "license_key": "ABC-123",
            "license_activated_at": _to_iso(activated_ms),
            "license_expires_at": _to_iso(expires_ms),
        }
        s = compute_license_status(prefs, now_ms=now_ms, bypass=False)
        assert s["status"] == "trial"
        assert s["canUsePipeline"] is True
        assert s["daysRemaining"] == 25

    def test_trial_falls_back_to_activated_at_plus_30d_when_no_expires_at(self):
        now_ms = 1_700_000_000_000.0
        activated_ms = now_ms - 1 * _DAY_MS
        prefs = {
            "license_key": "ABC-123",
            "license_activated_at": _to_iso(activated_ms),
        }
        s = compute_license_status(prefs, now_ms=now_ms, bypass=False)
        assert s["status"] == "trial"
        assert s["daysRemaining"] == 29

    def test_trial_boundary_now_equals_expires_is_expired(self):
        now_ms = 1_700_000_000_000.0
        prefs = {
            "license_key": "ABC-123",
            "license_activated_at": _to_iso(now_ms - 30 * _DAY_MS),
            "license_expires_at": _to_iso(now_ms),
        }
        s = compute_license_status(prefs, now_ms=now_ms, bypass=False)
        assert s["status"] == "expired"
        assert s["canUsePipeline"] is False
        assert s["daysRemaining"] == 0

    def test_expired_past_expires_at(self):
        now_ms = 1_700_000_000_000.0
        prefs = {
            "license_key": "ABC-123",
            "license_activated_at": _to_iso(now_ms - 40 * _DAY_MS),
            "license_expires_at": _to_iso(now_ms - 10 * _DAY_MS),
        }
        s = compute_license_status(prefs, now_ms=now_ms, bypass=False)
        assert s["status"] == "expired"
        assert s["canUsePipeline"] is False

    def test_unparseable_expires_at_fails_closed_to_expired(self):
        prefs = {
            "license_key": "ABC-123",
            "license_activated_at": "2026-01-01T00:00:00Z",
            "license_expires_at": "not-a-real-date",
        }
        s = compute_license_status(prefs, now_ms=1_000_000, bypass=False)
        assert s["status"] == "expired"
        assert s["canUsePipeline"] is False
        assert s["expiresAt"] is None

    def test_unparseable_activated_at_with_no_expires_at_fails_closed(self):
        prefs = {
            "license_key": "ABC-123",
            "license_activated_at": "garbage",
        }
        s = compute_license_status(prefs, now_ms=1_000_000, bypass=False)
        assert s["status"] == "expired"
        assert s["canUsePipeline"] is False


def _to_iso(ms: float) -> str:
    from datetime import datetime, timezone
    return (
        datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class TestBypassEligibility:
    def test_frozen_never_eligible(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert bypass_eligible(str(tmp_path), frozen=True) is False

    def test_no_repo_root_not_eligible(self):
        assert bypass_eligible(None, frozen=False) is False

    def test_no_git_dir_not_eligible(self, tmp_path):
        assert bypass_eligible(str(tmp_path), frozen=False) is False

    def test_dev_checkout_with_git_is_eligible(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert bypass_eligible(str(tmp_path), frozen=False) is True

    def test_bypass_active_requires_eligibility_and_flag(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DIMENSION_LICENSE_BYPASS", raising=False)
        (tmp_path / ".git").mkdir()
        # Eligible repo, but flag off.
        assert bypass_active({"license_dev_bypass": False}, str(tmp_path), False) is False
        # Eligible repo, flag on.
        assert bypass_active({"license_dev_bypass": True}, str(tmp_path), False) is True

    def test_bypass_active_false_when_not_eligible_even_with_flag_on(self, tmp_path):
        # No .git dir — must not be honored regardless of the prefs flag.
        assert bypass_active({"license_dev_bypass": True}, str(tmp_path), False) is False

    def test_bypass_via_env_var(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        monkeypatch.setenv("DIMENSION_LICENSE_BYPASS", "1")
        assert bypass_active({"license_dev_bypass": False}, str(tmp_path), False) is True


@pytest.fixture
def isolated_prefs(tmp_path, monkeypatch):
    """Redirect state_json_path() so this test never touches the user's real
    state.json — same pattern as test_preferences_state.py's fixture."""
    import logic.preferences_state as ps
    target = tmp_path / "state.json"
    monkeypatch.setattr(ps, "state_json_path", lambda: target)
    monkeypatch.setattr(ps.preferences, "_loaded", False)
    monkeypatch.setattr(ps.preferences, "_state", {})
    return target


class TestGetCurrentStatus:
    def test_fresh_install_is_none_and_blocked(self, isolated_prefs):
        status = get_current_status(repo_root=None, frozen=False)
        assert status["status"] == "none"
        assert status["canUsePipeline"] is False

    def test_reflects_activation_written_by_another_process(self, isolated_prefs):
        """Simulates the CEP panel activating a trial (writing state.json
        directly, as prefs_cli.py's `set` does) while dimension_server.py
        is already running — get_current_status() must reload fresh each
        call, not cache a stale 'none' from process start."""
        from logic.preferences_state import preferences

        assert get_current_status(repo_root=None, frozen=False)["status"] == "none"

        preferences.license_key = "ABC-123"
        preferences.license_status = "trial"
        preferences.license_product = "trial"
        preferences.license_activated_at = "2026-01-01T00:00:00Z"
        preferences.license_expires_at = "2099-01-01T00:00:00Z"
        preferences.save()

        status = get_current_status(repo_root=None, frozen=False)
        assert status["status"] == "trial"
        assert status["canUsePipeline"] is True

    def test_bypass_short_circuits_even_with_expired_prefs(self, isolated_prefs, tmp_path):
        from logic.preferences_state import preferences

        (tmp_path / "repo" / ".git").mkdir(parents=True)
        preferences.license_status = "expired"
        preferences.license_dev_bypass = True
        preferences.save()

        status = get_current_status(repo_root=str(tmp_path / "repo"), frozen=False)
        assert status["status"] == "bypass"
        assert status["canUsePipeline"] is True
