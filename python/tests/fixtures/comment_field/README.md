# Comment-field Fixtures — PR-E.1 Layer 2 Contract Tests

This folder holds **real JSX-written scrape manifests** captured
from a live After Effects round-trip. The Layer 2 contract test at
`python/tests/test_comment_gardener_jsx.py` consumes these to
verify the `comment` field actually surfaces from JSX through to
the Python `LayerModel`. Synthetic-fixture-anti-pattern guard,
same pattern as the bridge fixtures at `../bridge/`.

A schema unit test (Layer 1) verifies the classifier is
internally consistent. This Layer 2 test verifies the JSX→Python
wire actually carries the field. Both required; neither replaces
the other.

## Current fixtures

| File | Captured | Layers | Non-null comments | Notes |
|---|---|---|---|---|
| `scrape-87n-no-comments.json` | 2026-04-28 | 19 | 0 | The "key presence" guard. Catches a regression that drops `comment` from `V5_LAYER_KEYS` or `LayerModel`. Source: `87N_Reels_DEV_87Neon_Anima_HD_01_mc` on Universal_HV profile, post-PR-E.1 commit 2 scrape. |

## Coverage gap (follow-up)

The current fixture is the "all comments null" case — every layer
record has the `comment` key but no layer carries actual content.
The contract test against this fixture proves **key presence and
type** but cannot verify the **round-trip of non-empty content**.

To close that gap, a follow-up capture is needed:

1. In AE, set a layer comment by hand (e.g. select a layer, open
   Layer → Comment, type something like "render at 4K").
2. Run a fresh scrape via SEND TO DIMENSION.
3. Confirm the captured manifest's `comment` field carries the
   exact text, including case, spacing, and any special characters.
4. Add the captured manifest as a new fixture, e.g.
   `scrape-with-foreign-comment.json`.
5. Extend the Layer 2 test to assert that classifier runs on the
   captured manifest and produces the expected CommentClass for
   each known layer.

This is tracked as a follow-up issue (analogous to PR-B's #50
fixture-capture follow-up). PR-E.1 ships with the key-presence
guard; the richer fixture lands in a small follow-up commit.

## Capture procedure (general)

The fixture capture procedure mirrors PR-B's bridge-fixture
flow — copy the post-scrape `scrape_manifest.json` directly:

```bash
# After running a scrape against the target comp:
cp scrape_manifest.json python/tests/fixtures/comment_field/<descriptive-name>.json

# Verify the fixture parses through LayerModel:
python3 -c "
import json
from python.models.scrape_manifest import ScrapeManifest
data = json.load(open('python/tests/fixtures/comment_field/<name>.json'))
m = ScrapeManifest(**data)
print(f'parsed {len(m.layers)} layers')
"
```

Update this README with a row in the Current fixtures table for
each new capture: filename, capture date, layer count, non-null
comment count, and a note on what scenario it represents.

## What's NOT in this folder

- No synthetic placeholder fixtures. None.
- No hand-edited manifests. The `comment` field's whole point is
  to round-trip studio content verbatim — modifying a fixture's
  comment value would break the anti-pattern guard's premise.
- If you need to test classifier behavior on synthetic content,
  use `python/tests/test_comment_gardener.py` (Layer 1) — that's
  the appropriate layer for synthetic strings.
