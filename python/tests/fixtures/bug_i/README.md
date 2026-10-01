# Bug I fixture capture procedure

This directory holds real-data captures for the Bug I rest-pose
scrape contract test.

## Required fixtures

The contract test in `test_bug_i_camera_rest_pose.py` activates
when both files below are present:

- `87n_playhead_at_zero.json` — 87N HD comp scraped with playhead
  parked at frame 0 of the active comp.
- `87n_playhead_at_162.json` — same comp, same session, scraped
  with playhead parked at frame 162 (or any frame materially
  inside the camera's animation range).

If either is missing, the test skips with a clear message.

## Capture procedure (Phase 3, post-PR-W merge)

1. Check out `fix/bug-i-camera-rest-pose-scrape` (or the merged
   commit on `main`).
2. Reload the AE Dimension panel so it picks up the post-Bug-I
   `Sovereign_Core.jsx` and `SovCore_Value.jsx`.
3. Open the canonical 87N HD source comp.
4. Park the playhead at frame 0. Trigger a scrape via the panel.
   Save the resulting `scrape_manifest.json` to this directory as
   `87n_playhead_at_zero.json`.
5. Park the playhead at frame 162. Trigger another scrape.
   Save as `87n_playhead_at_162.json`.
6. Run `pytest python/tests/test_bug_i_camera_rest_pose.py -v`.
   Expect all assertions to pass — both manifests should report
   identical camera fields (rest-pose anchor is playhead-invariant).

If the assertions fail, the JSX fix did not deploy correctly —
verify the panel reload and the on-disk JSX content before
investigating the test logic.

## Why two fixtures, not one

The contract Bug I asserts is *determinism* — scrape produces
the same camera values regardless of playhead. A single fixture
proves nothing about determinism. The pair makes the regression
net immediate: any future change that re-introduces a playhead
read on either site (`Sovereign_Core.jsx::scrape` or
`SovCore_Value.jsx::readStatic`) will surface as a delta between
the two captured manifests.
