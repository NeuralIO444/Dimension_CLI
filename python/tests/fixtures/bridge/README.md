# Bridge Fixtures — PR-B Layer 2 Contract Tests

This folder holds **real JSX-written result.json payloads** captured
from a live After Effects round-trip. The Layer 2 contract tests at
`python/tests/test_bridge_contracts_jsx.py` consume these fixtures to
verify that the Pydantic schemas in `python/models/bridge_jobs.py`
accept what the running JSX panel actually writes.

This is the **synthetic-fixture-anti-pattern guard** documented in
CLAUDE.md. A schema unit test (Layer 1) verifies the schema is
internally consistent. This Layer 2 test verifies it matches wire
reality. Both are required; neither replaces the other.

## Status

The capture pass is a **manual AE step** that ships separately from
the rest of PR-B. Until fixtures land here, the Layer 2 tests are
marked `pytest.skip()` with this message:

> Awaiting fixture capture — see python/tests/fixtures/bridge/README.md

Skipped tests are honest about their state. Synthetic placeholder
fixtures would reproduce the exact anti-pattern PR-B exists to
prevent.

## What's needed (5 fixtures)

The Layer 2 fixtures requiring a live AE round-trip:

- [ ] `tag-write-OK.json` — apply HERO to a known UID
- [ ] `tag-write-clear-OK.json` — clear a tag from the same UID
- [ ] `select-layer-OK.json` — select a layer by UID
- [ ] `query-layer-state-OK.json` — read position/scale off a known UID
- [ ] `duplicate-plan-OK.json` — run a minimal duplication plan
- [ ] `mask-toggle-OK.json` — toggle a safe-zone mask import

A 5th fixture (`scrape-OK.json`) is **synthesizable from any session
archive** at `logs/archive/Session_*/scrape_manifest.json`. The
capture helper does this automatically — no live AE required.

## Capture procedure

1. Open AE with the Dimension panel loaded and a comp visible in
   the Composition panel.
2. From the repo root, run the capture helper for each fixture:

   ```bash
   # Synthesize the scrape fixture from the latest session archive
   python python/scripts/capture_bridge_fixtures.py scrape

   # For the live captures, supply a real layer UID from your comp
   python python/scripts/capture_bridge_fixtures.py tag-write --uid <UID>
   python python/scripts/capture_bridge_fixtures.py tag-write-clear --uid <UID>
   python python/scripts/capture_bridge_fixtures.py select-layer --uid <UID>
   python python/scripts/capture_bridge_fixtures.py query-layer-state --uid <UID>
   python python/scripts/capture_bridge_fixtures.py duplicate-plan
   python python/scripts/capture_bridge_fixtures.py mask-toggle --mask-path /path/to/mask.png

   # Or all in one go
   python python/scripts/capture_bridge_fixtures.py all --uid <UID>
   ```

3. The script writes each fixture as `<name>-OK.json` plus a
   sibling `<name>-OK.meta.json` with capture metadata (date,
   versions, source comp, args).

4. Run the Layer 2 tests to confirm the new fixtures parse:

   ```bash
   pytest python/tests/test_bridge_contracts_jsx.py -v
   ```

5. The skip markers auto-lift once each fixture file exists. Commit
   the fixtures + meta files to the repo.

## Fixture metadata format

Each `<name>-OK.json` has a sibling `<name>-OK.meta.json` recording:

- `capture_date` — ISO 8601 UTC timestamp
- `dimension_version` — Python `SCHEMA_VERSION` at capture
- `bridge_version` — Python `BRIDGE_SCHEMA_VERSION` at capture
- `jsx_version` — `DIMENSION_SCHEMA_VERSION` from version.jsx
- `jsx_bridge_version` — `DIMENSION_BRIDGE_SCHEMA_VERSION` from version.jsx
- `source_comp` — active AE comp name at capture
- `capture_args` — kwargs passed to the bridge method

## When to re-capture

Re-capture only when the wire format changes — and a wire format
change requires bumping `BRIDGE_SCHEMA_VERSION` first. The flow:

1. Edit `python/models/bridge_jobs.py` to reflect the new shape
2. Bump `BRIDGE_SCHEMA_VERSION` in `python/core/schema_version.py`
   AND `Scripts/Dimension_Assets/version.jsx`
3. Update the JSX side (handler emits the new field, etc.)
4. Re-capture the affected fixtures
5. Update the fixture's `.meta.json` file (overwritten automatically
   by the capture script)
6. Commit the schema change + fixture refresh together

## What's NOT in this folder

- No synthetic placeholder fixtures. None. Ever.
- No `_test.json` or `_example.json` files that look like
  fixtures but aren't real captures.
- No fixtures from manually-edited result dicts. Capture is
  always live.

If a developer working on PR-B-followup or a future bridge change
finds themselves wanting to write a fixture by hand, that's the
synthetic-fixture anti-pattern starting to creep back in. Either:

(a) capture from a real round-trip via the helper script, or
(b) write a Layer 1 unit test instead — synthetic dicts are fine
    there, just not at Layer 2.
