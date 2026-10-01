# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_bridge_contracts_jsx.py
PR-B Layer 2 — JSX-written fixture contract tests.

What these tests cover (and why they cannot use synthetic dicts)
-----------------------------------------------------------------
Layer 1 (test_bridge_jobs.py) verifies the Pydantic schemas are
internally consistent. Layer 3 (test_bridge_validation.py) verifies
validation fires at the right edges. Both layers use synthetic
dicts — appropriate for testing what they test.

Layer 2 (this file) verifies that the schemas accept what JSX
ACTUALLY WRITES. Synthetic dicts cannot prove this. PR #45 + PR #48
both shipped because Python tests were green against synthetic
dicts that didn't reflect what JSX produced.

Each test in this file consumes a real result.json file captured
from a live AE round-trip via
`python/scripts/capture_bridge_fixtures.py`. The fixtures live at
`python/tests/fixtures/bridge/<job-type>-OK.json` with sibling
`.meta.json` files recording capture metadata.

Status: scaffolded
------------------
The fixtures are pending a manual AE capture pass. Until a fixture
file exists on disk, the corresponding test is `pytest.skip()`'d
with a message pointing at the README. Skipped tests are honest
about their state — synthetic placeholder fixtures would reproduce
the exact anti-pattern PR-B exists to prevent.

The skip markers auto-lift once each fixture file lands. No code
change required to "enable" the tests — they activate as
fixtures appear.

See:
- python/tests/fixtures/bridge/README.md (capture procedure)
- python/scripts/capture_bridge_fixtures.py (helper script)
- CLAUDE.md "Anti-patterns — Trusting synthetic Python test
  fixtures..." (the case study this layer guards against)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from models.bridge_contract import (  # noqa: E402
    BRIDGE_SCHEMA_VERSION,
    BridgeDuplicatePlanResult,
    BridgeMaskToggleResult,
    BridgeQueryLayerStateResult,
    BridgeScrapeResult,
    BridgeSelectLayerResult,
    BridgeTagWriteResult,
    parse_bridge_result,
)

parse_result = parse_bridge_result


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "bridge"

_SKIP_MSG = (
    "Awaiting fixture capture — see "
    "python/tests/fixtures/bridge/README.md"
)


def _load_fixture(name: str) -> dict | None:
    """Load a fixture by name, or return None if it doesn't exist
    yet. Tests use the None return to skip rather than fail."""
    path = FIXTURES_DIR / f"{name}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── One test per fixture, with skip-when-missing guard ───────────


class TestBridgeFixtureRoundTrip:
    """Each test parses a real JSX-written result through the
    Pydantic schema and asserts the discriminator dispatched to
    the right concrete class. The schema version stamp is verified
    matches the current BRIDGE_SCHEMA_VERSION (a fixture from a
    pre-PR-B build would fail this check, prompting recapture).
    """

    def test_scrape_fixture_parses(self):
        payload = _load_fixture("scrape-OK")
        if payload is None:
            pytest.skip(_SKIP_MSG)
        result = parse_result(payload)
        assert isinstance(result, BridgeScrapeResult)
        assert result.status == "OK"
        assert result.schema_version == BRIDGE_SCHEMA_VERSION

    def test_tag_write_fixture_parses(self):
        payload = _load_fixture("tag-write-OK")
        if payload is None:
            pytest.skip(_SKIP_MSG)
        result = parse_result(payload)
        assert isinstance(result, BridgeTagWriteResult)
        assert result.status == "OK"
        assert result.schema_version == BRIDGE_SCHEMA_VERSION
        # tag-write success should echo the uid + tag
        assert result.uid is not None
        assert result.tag is not None

    def test_tag_write_clear_fixture_parses(self):
        payload = _load_fixture("tag-write-clear-OK")
        if payload is None:
            pytest.skip(_SKIP_MSG)
        result = parse_result(payload)
        assert isinstance(result, BridgeTagWriteResult)
        # clear path: tag is None on success
        assert result.tag is None

    def test_select_layer_fixture_parses(self):
        payload = _load_fixture("select-layer-OK")
        if payload is None:
            pytest.skip(_SKIP_MSG)
        result = parse_result(payload)
        assert isinstance(result, BridgeSelectLayerResult)
        assert result.schema_version == BRIDGE_SCHEMA_VERSION

    def test_query_layer_state_fixture_parses(self):
        payload = _load_fixture("query-layer-state-OK")
        if payload is None:
            pytest.skip(_SKIP_MSG)
        result = parse_result(payload)
        assert isinstance(result, BridgeQueryLayerStateResult)
        assert result.schema_version == BRIDGE_SCHEMA_VERSION

    def test_duplicate_plan_fixture_parses(self):
        payload = _load_fixture("duplicate-plan-OK")
        if payload is None:
            pytest.skip(_SKIP_MSG)
        result = parse_result(payload)
        assert isinstance(result, BridgeDuplicatePlanResult)
        # Pin the wire-shape correction from commit 4: errors is
        # an int count, log is an inline dict (not a path string).
        if result.errors is not None:
            assert isinstance(result.errors, int)
        if result.log is not None:
            assert isinstance(result.log, dict)

    def test_mask_toggle_fixture_parses(self):
        payload = _load_fixture("mask-toggle-OK")
        if payload is None:
            pytest.skip(_SKIP_MSG)
        result = parse_result(payload)
        assert isinstance(result, BridgeMaskToggleResult)
        assert result.schema_version == BRIDGE_SCHEMA_VERSION


class TestFixtureMetadata:
    """The capture helper writes a .meta.json sidecar for each
    fixture. These tests verify the metadata is well-formed when
    fixtures exist — useful for catching capture-script bugs
    without re-running the manual AE pass."""

    @pytest.mark.parametrize("fixture_name", [
        "scrape-OK", "tag-write-OK", "tag-write-clear-OK",
        "select-layer-OK", "query-layer-state-OK", "duplicate-plan-OK",
        "mask-toggle-OK",
    ])
    def test_fixture_has_metadata_sibling(self, fixture_name):
        fixture_path = FIXTURES_DIR / f"{fixture_name}.json"
        meta_path = FIXTURES_DIR / f"{fixture_name}.meta.json"
        if not fixture_path.is_file():
            pytest.skip(_SKIP_MSG)
        assert meta_path.is_file(), (
            f"fixture {fixture_name}.json exists but "
            f"{fixture_name}.meta.json is missing — "
            f"recapture via python/scripts/capture_bridge_fixtures.py"
        )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        # Required metadata fields documented in fixtures/bridge/README.md
        for field in ("capture_date", "dimension_version",
                      "bridge_version", "jsx_version",
                      "jsx_bridge_version", "source_comp",
                      "capture_args"):
            assert field in meta, (
                f"{fixture_name}.meta.json missing {field!r}"
            )

    @pytest.mark.parametrize("fixture_name", [
        "scrape-OK", "tag-write-OK", "tag-write-clear-OK",
        "select-layer-OK", "query-layer-state-OK", "duplicate-plan-OK",
        "mask-toggle-OK",
    ])
    def test_fixture_metadata_matches_current_bridge_version(
        self, fixture_name,
    ):
        """A fixture captured under an older BRIDGE_SCHEMA_VERSION
        should fail parsing in TestBridgeFixtureRoundTrip — but
        this test gives a clearer error message: the fixture is
        stale and needs recapture."""
        fixture_path = FIXTURES_DIR / f"{fixture_name}.json"
        meta_path = FIXTURES_DIR / f"{fixture_name}.meta.json"
        if not fixture_path.is_file() or not meta_path.is_file():
            pytest.skip(_SKIP_MSG)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["bridge_version"] == BRIDGE_SCHEMA_VERSION, (
            f"{fixture_name} captured under bridge version "
            f"{meta['bridge_version']!r} but current is "
            f"{BRIDGE_SCHEMA_VERSION!r}. Recapture via "
            f"python/scripts/capture_bridge_fixtures.py to refresh."
        )
