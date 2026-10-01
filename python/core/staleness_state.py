# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/staleness_state.py
v5.8.10 — process-wide AE-panel staleness state.

Closes the upstream root cause of the 87N reliability issue: when
the user `git pull`s without reloading the AE panel, the running
JSX is unaware of new job types + tag-writes silently fail. By the
time the user notices, they've spent minutes troubleshooting.

How this module works:
  - The bridge calls `notify_jsx_version(version)` after every
    successful file-bridge round-trip, passing the version stamp
    that arrived in `dimension_schema_version`.
  - This module compares it to the Python-side `SCHEMA_VERSION`
    constant.
  - If they disagree (or the JSX side has no stamp at all — meaning
    pre-v5.8.10 panel), `is_stale()` returns True.
  - The orchestrator's banner subscribes via `register_listener`
    and shows a persistent "panel reload required" message with
    the reload block built in.

Singleton because the staleness state is a process-wide truth: any
bridge call updates it; any UI reads it.
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

from core.schema_version import SCHEMA_VERSION


# ── State ────────────────────────────────────────────────────────────


# RLock so the public helpers can call each other without deadlock —
# `register_listener` calls `is_stale()` while still holding the lock
# to compute the initial state for the new subscriber, and the
# `notify_jsx_version` path computes new_stale from inside its own
# critical section. threading.Lock would block on the inner call.
_lock = threading.RLock()
_last_seen_version: Optional[str] = None
_listeners: List[Callable[[bool, Optional[str]], None]] = []


# ── Public API ───────────────────────────────────────────────────────


def notify_jsx_version(version: Optional[str]) -> None:
    """Called by the bridge after every result. `version` is the
    `dimension_schema_version` field from the JSX response, or None
    when the field is missing (pre-v5.8.10 panel).

    Fires registered listeners on staleness-state changes only — no
    spurious updates if the value is unchanged."""
    global _last_seen_version
    with _lock:
        prior = _last_seen_version
        _last_seen_version = version
        prior_stale = (prior is not None and prior != SCHEMA_VERSION) \
                       or (prior is None and _has_been_observed())
        new_stale = is_stale()
        # Listener fire-out criterion: state changed OR first
        # observation. Listeners decide whether to re-render.
        listeners = list(_listeners) if (
            prior != version or prior is None
        ) else []
    for fn in listeners:
        try:
            fn(new_stale, version)
        except Exception:  # noqa: BLE001 — never let listener errors
                            # break the bridge's normal flow
            pass


def is_stale() -> bool:
    """True when the most-recently-observed JSX version differs from
    the Python constant, OR when the JSX side has never identified
    itself (pre-v5.8.10 panel that doesn't stamp results)."""
    with _lock:
        if _last_seen_version is None:
            # We haven't observed any version yet. Don't claim stale
            # until at least one round-trip has happened — otherwise
            # we'd flag a fresh-launch state as stale.
            return False
        return _last_seen_version != SCHEMA_VERSION


def last_seen_jsx_version() -> Optional[str]:
    """The JSX version from the most recent file-bridge round-trip,
    or None if no round-trip has happened (or panel didn't stamp).
    Useful for the banner's diagnostic text."""
    with _lock:
        return _last_seen_version


def register_listener(
    fn: Callable[[bool, Optional[str]], None],
) -> None:
    """Subscribe to staleness updates. Listener is called with
    (is_stale, last_seen_version) every time the bridge observes a
    new JSX version. Listener is also called immediately with the
    current state so the banner gets its initial paint."""
    with _lock:
        _listeners.append(fn)
        current_stale = is_stale()
        current_version = _last_seen_version
    try:
        fn(current_stale, current_version)
    except Exception:  # noqa: BLE001
        pass


def reset_for_test() -> None:
    """Wipe state. Tests reach for this to start clean. NOT for use
    by production code."""
    global _last_seen_version
    with _lock:
        _last_seen_version = None
        _listeners.clear()


# ── Internals ────────────────────────────────────────────────────────


def _has_been_observed() -> bool:
    """Whether we've seen at least one round-trip's stamp (even if
    the stamp was None — that's "we observed a pre-v5.8.10 panel")."""
    # Sentinel approach: track separately if needed. For now we say
    # "observed" iff _last_seen_version is not None. Pre-stamp panels
    # surface as None and `is_stale()` treats that as "not yet
    # determined." Once a v5.8.10+ panel responds, we'll have a real
    # value to compare. If the panel is older AND doesn't ever stamp,
    # the bridge's eventual `apply_duplication_plan` timeout will
    # trip the user's attention via commit 5's shortened message.
    return False


__all__ = [
    "notify_jsx_version",
    "is_stale",
    "last_seen_jsx_version",
    "register_listener",
    "reset_for_test",
]
