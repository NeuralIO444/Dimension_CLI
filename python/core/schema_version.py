# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/schema_version.py
v5.8.10 — single source of truth for the Python-side schema version.

Two version constants live here. They are NOT the same thing.

  SCHEMA_VERSION         — product / panel staleness version.
                           Bumps for UI changes, feature releases,
                           any panel-level update where the user
                           should reload. Bridge stamps this onto
                           every result via `dimension_schema_version`
                           so the staleness detector can surface a
                           reload banner if Python and panel drift.

  BRIDGE_SCHEMA_VERSION  — file-bridge contract version (PR-B).
                           Bumps ONLY when the JSX↔Python wire
                           contract changes — new job type, new
                           result field, new status enum value,
                           or any change to the descriptors in
                           python/models/bridge_jobs.py. Mismatch
                           triggers structured-error refusal so
                           contract drift is loud, not silent.

Both are shared with JSX via Scripts/Dimension_Assets/version.jsx.
Both sides MUST agree exactly. Tests verify each pair via
`assert_versions_match()` (product) and `assert_bridge_versions_match()`
(bridge).

When you bump either here, bump the corresponding constant in
Scripts/Dimension_Assets/version.jsx. The two version fields can
move independently — that is the point of having two.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


SCHEMA_VERSION = "5.8.13"

# PR-B (2026-04-27) — file-bridge contract version. See
# python/models/bridge_jobs.py for the schemas this version stamps.
BRIDGE_SCHEMA_VERSION = "1.0"


def jsx_version_path() -> Path:
    """Path to the JSX-side version file. Used by tests + by the
    staleness detector to read the on-disk JSX version (vs the
    AE-side runtime version which arrives via the file-bridge)."""
    return (Path(__file__).resolve().parents[2]
            / "Scripts" / "Dimension_Assets" / "version.jsx")


_VERSION_RE = re.compile(
    r'\$\.global\.DIMENSION_SCHEMA_VERSION\s*=\s*"([^"]+)"'
)

_BRIDGE_VERSION_RE = re.compile(
    r'\$\.global\.DIMENSION_BRIDGE_SCHEMA_VERSION\s*=\s*"([^"]+)"'
)


def read_jsx_version_from_disk() -> Optional[str]:
    """Parse the JSX-side product version constant from disk. None
    on file-missing or parse-failure (defensive — staleness
    detection should never crash the orchestrator)."""
    try:
        text = jsx_version_path().read_text(encoding="utf-8")
    except OSError:
        return None
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


def read_jsx_bridge_version_from_disk() -> Optional[str]:
    """Parse the JSX-side BRIDGE schema version from disk. None on
    file-missing or parse-failure. Returns None — not a raise — if
    the JSX file pre-dates PR-B and has no bridge version constant
    yet; callers can decide whether absence is a hard failure."""
    try:
        text = jsx_version_path().read_text(encoding="utf-8")
    except OSError:
        return None
    m = _BRIDGE_VERSION_RE.search(text)
    return m.group(1) if m else None


def assert_versions_match() -> None:
    """Raise AssertionError if the Python product version and the
    JSX product version on disk disagree. Used by tests + by a
    startup health check; never called at hot-path runtime."""
    on_disk = read_jsx_version_from_disk()
    if on_disk is None:
        raise AssertionError(
            f"JSX version file missing or unparseable: "
            f"{jsx_version_path()}"
        )
    if on_disk != SCHEMA_VERSION:
        raise AssertionError(
            f"SCHEMA_VERSION mismatch: Python says {SCHEMA_VERSION!r}, "
            f"JSX (on disk) says {on_disk!r}. Bump both together — "
            f"see python/core/schema_version.py and "
            f"Scripts/Dimension_Assets/version.jsx."
        )


def assert_bridge_versions_match() -> None:
    """Raise AssertionError if the Python BRIDGE_SCHEMA_VERSION
    and the JSX-side `DIMENSION_BRIDGE_SCHEMA_VERSION` on disk
    disagree. PR-B introduced this — bridge contract drift is
    surfaced loudly via structured-error refusal at runtime, but
    a CI test on this assertion catches mismatches before code
    even tries to dispatch."""
    on_disk = read_jsx_bridge_version_from_disk()
    if on_disk is None:
        raise AssertionError(
            f"JSX bridge schema version not found in "
            f"{jsx_version_path()} — expected a "
            f"`$.global.DIMENSION_BRIDGE_SCHEMA_VERSION = \"...\";` "
            f"declaration. Add one matching "
            f"BRIDGE_SCHEMA_VERSION = {BRIDGE_SCHEMA_VERSION!r}."
        )
    if on_disk != BRIDGE_SCHEMA_VERSION:
        raise AssertionError(
            f"BRIDGE_SCHEMA_VERSION mismatch: "
            f"Python says {BRIDGE_SCHEMA_VERSION!r}, "
            f"JSX (on disk) says {on_disk!r}. Bump both together — "
            f"see python/core/schema_version.py and "
            f"Scripts/Dimension_Assets/version.jsx."
        )


__all__ = [
    "SCHEMA_VERSION",
    "BRIDGE_SCHEMA_VERSION",
    "jsx_version_path",
    "read_jsx_version_from_disk",
    "read_jsx_bridge_version_from_disk",
    "assert_versions_match",
    "assert_bridge_versions_match",
]
