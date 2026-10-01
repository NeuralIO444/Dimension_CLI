# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_schema_version.py
v5.8.10 — verify the Python + JSX schema-version constants stay in sync.

If this test fails, you bumped one and forgot the other. Bump both
together — see python/core/schema_version.py and
Scripts/Dimension_Assets/version.jsx.
"""

from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


class TestSchemaVersionAlignment:
    def test_python_and_jsx_versions_match(self):
        from core.schema_version import (
            SCHEMA_VERSION, read_jsx_version_from_disk,
        )
        on_disk = read_jsx_version_from_disk()
        assert on_disk is not None, (
            "JSX version file missing or unparseable — "
            "Scripts/Dimension_Assets/version.jsx must exist + "
            "expose `$.global.DIMENSION_SCHEMA_VERSION = \"...\";`"
        )
        assert on_disk == SCHEMA_VERSION, (
            f"version mismatch: Python={SCHEMA_VERSION!r} "
            f"vs JSX={on_disk!r}. Bump both together."
        )

    def test_assert_versions_match_helper_passes(self):
        from core.schema_version import assert_versions_match
        # Should not raise on a correctly-aligned tree.
        assert_versions_match()

    def test_jsx_version_path_resolves(self):
        from core.schema_version import jsx_version_path
        p = jsx_version_path()
        assert p.is_file(), f"expected {p} to exist"
        assert p.name == "version.jsx"


class TestBridgeSchemaVersionAlignment:
    """PR-B (2026-04-27) — verify the BRIDGE_SCHEMA_VERSION constant
    is in sync between Python and JSX.

    BRIDGE_SCHEMA_VERSION is distinct from SCHEMA_VERSION (the
    product/staleness version). The bridge version stamps onto every
    job and result; mismatch raises BridgeSchemaVersionError on
    parse. This test guards against bumping one side without the
    other before runtime even has a chance to surface the
    structured error."""

    def test_python_and_jsx_bridge_versions_match(self):
        from core.schema_version import (
            BRIDGE_SCHEMA_VERSION, read_jsx_bridge_version_from_disk,
        )
        on_disk = read_jsx_bridge_version_from_disk()
        assert on_disk is not None, (
            "JSX bridge schema version not found — "
            "Scripts/Dimension_Assets/version.jsx must expose "
            "`$.global.DIMENSION_BRIDGE_SCHEMA_VERSION = \"...\";`"
        )
        assert on_disk == BRIDGE_SCHEMA_VERSION, (
            f"bridge version mismatch: Python={BRIDGE_SCHEMA_VERSION!r} "
            f"vs JSX={on_disk!r}. Bump both together — see "
            f"python/core/schema_version.py and "
            f"Scripts/Dimension_Assets/version.jsx."
        )

    def test_assert_bridge_versions_match_helper_passes(self):
        from core.schema_version import assert_bridge_versions_match
        # Should not raise on a correctly-aligned tree.
        assert_bridge_versions_match()

    def test_bridge_constant_re_exported_from_models(self):
        """The schemas module re-exports BRIDGE_SCHEMA_VERSION for
        convenience. The two references must be the same value."""
        from core.schema_version import BRIDGE_SCHEMA_VERSION as core_v
        from models.bridge_jobs import BRIDGE_SCHEMA_VERSION as models_v
        assert core_v == models_v
