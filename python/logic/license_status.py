# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/logic/license_status.py

Python port of cep/js/license.js's pure `computeLicenseStatus()` derivation,
so dimension_server.py (a separate, long-running Python process backing the
external Dashboard) can gate its own pipeline-triggering endpoint the same
way the CEP panel gates SCAN/EXECUTE, without either side re-deriving its
own copy of the trial-expiry date math. Both sides read the same shared
state file (see preferences_state.py's _state_path()), written by whichever
side the user actually activates through (today: the CEP panel only — the
Dashboard has no activation UI of its own by design, see the M1 spec and
2026-07-21 Dashboard redesign plan).

Keep this in lockstep with cep/js/license.js::computeLicenseStatus() and
::_bypassEligible()/_bypassActive() — same priority chain, same field
names, same fail-closed behavior on unparseable dates. Do not let the two
implementations drift; that's the exact "producer/consumer contract must
agree from both ends" class of bug this codebase has hit before (see
CLAUDE.md's sharp edges on schema-vs-wire mismatches).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from logic.preferences_state import preferences

TRIAL_DAYS = 30
_MS_PER_DAY = 24 * 3600 * 1000


def _parse_iso_to_ms(value: Optional[str]) -> Optional[float]:
    """Returns None for empty/unparseable input (JS: NaN), never raises —
    mirrors computeLicenseStatus()'s `!isFinite(expiresMs)` fail-closed
    branch relying on a non-throwing parse."""
    if not value:
        return None
    try:
        v = value[:-1] + "+00:00" if value.endswith("Z") else value
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000.0
    except (ValueError, TypeError):
        return None


def _iso(ms: float) -> str:
    return (
        datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def compute_license_status(
    prefs: Dict[str, Any], now_ms: Optional[float] = None, bypass: bool = False
) -> Dict[str, Any]:
    """Faithful port of cep/js/license.js:55-154. Pure function, no I/O —
    same priority chain: bypass -> revoked -> none -> full -> trial/expired."""
    if now_ms is None:
        now_ms = datetime.now(timezone.utc).timestamp() * 1000.0
    prefs = prefs or {}

    if bypass:
        return {
            "status": "bypass",
            "daysRemaining": None,
            "expiresAt": None,
            "email": prefs.get("license_email") or None,
            "product": None,
            "canUsePipeline": True,
            "needsOnlineActivate": False,
        }

    if prefs.get("license_status") == "revoked":
        return {
            "status": "revoked",
            "daysRemaining": 0,
            "expiresAt": prefs.get("license_expires_at") or None,
            "email": prefs.get("license_email") or None,
            "product": prefs.get("license_product") or None,
            "canUsePipeline": False,
            "needsOnlineActivate": False,
        }

    if not prefs.get("license_key") or not prefs.get("license_activated_at"):
        return {
            "status": "none",
            "daysRemaining": None,
            "expiresAt": None,
            "email": prefs.get("license_email") or None,
            "product": None,
            "canUsePipeline": False,
            "needsOnlineActivate": True,
        }

    if prefs.get("license_product") == "full":
        return {
            "status": "full",
            "daysRemaining": None,
            "expiresAt": prefs.get("license_expires_at") or None,
            "email": prefs.get("license_email") or None,
            "product": "full",
            "canUsePipeline": True,
            "needsOnlineActivate": False,
        }

    # Trial. Compare against the STORED expires_at when present (set once
    # at first activation) rather than recomputing from activated_at here
    # — keeps this function agreeing with whatever wrote license_expires_at,
    # same reasoning as the JS source's own comment.
    expires_raw = prefs.get("license_expires_at")
    if expires_raw:
        expires_ms = _parse_iso_to_ms(expires_raw)
    else:
        activated_ms = _parse_iso_to_ms(prefs.get("license_activated_at"))
        expires_ms = (
            activated_ms + TRIAL_DAYS * _MS_PER_DAY
            if activated_ms is not None
            else None
        )

    if expires_ms is None:
        # Corrupt/unparseable date — fail closed, not open.
        return {
            "status": "expired",
            "daysRemaining": 0,
            "expiresAt": None,
            "email": prefs.get("license_email") or None,
            "product": "trial",
            "canUsePipeline": False,
            "needsOnlineActivate": False,
        }

    if now_ms >= expires_ms:
        return {
            "status": "expired",
            "daysRemaining": 0,
            "expiresAt": _iso(expires_ms),
            "email": prefs.get("license_email") or None,
            "product": "trial",
            "canUsePipeline": False,
            "needsOnlineActivate": False,
        }

    days_remaining = int((expires_ms - now_ms) // _MS_PER_DAY)
    return {
        "status": "trial",
        "daysRemaining": days_remaining,
        "expiresAt": _iso(expires_ms),
        "email": prefs.get("license_email") or None,
        "product": "trial",
        "canUsePipeline": True,
        "needsOnlineActivate": False,
    }


def bypass_eligible(repo_root: Optional[str], frozen: bool) -> bool:
    """Mirrors license.js's _bypassEligible(): only a real dev checkout
    (repo_root set, NOT a frozen/packaged build) with a sibling .git dir is
    ever eligible — never honored in a production ZXP install."""
    if frozen or not repo_root:
        return False
    try:
        return os.path.isdir(os.path.join(repo_root, ".git"))
    except OSError:
        return False


def bypass_active(prefs: Dict[str, Any], repo_root: Optional[str], frozen: bool) -> bool:
    """Mirrors license.js's _bypassActive()."""
    if not bypass_eligible(repo_root, frozen):
        return False
    env_bypass = os.environ.get("DIMENSION_LICENSE_BYPASS") in ("1", "true")
    prefs_bypass = prefs.get("license_dev_bypass") is True
    return env_bypass or prefs_bypass


def get_current_status(repo_root: Optional[str], frozen: bool) -> Dict[str, Any]:
    """Reloads preferences fresh from disk before computing status.

    dimension_server.py is a long-running process — caching a status
    computed at process start would silently ignore a license activated
    (or expired) via the CEP panel afterward. Same class of staleness as
    CLAUDE.md's "JSX writes state Python doesn't observe in real time"
    sharp edge, applied to a long-lived Python process instead of a
    stale-manifest read.
    """
    preferences.reload()
    prefs = {
        "license_key": preferences.license_key,
        "license_status": preferences.license_status,
        "license_product": preferences.license_product,
        "license_product_id": preferences.license_product_id,
        "license_email": preferences.license_email,
        "license_activated_at": preferences.license_activated_at,
        "license_expires_at": preferences.license_expires_at,
        "license_dev_bypass": preferences.license_dev_bypass,
    }
    bypass = bypass_active(prefs, repo_root, frozen)
    return compute_license_status(prefs, bypass=bypass)
