# Dimension Endgame — Game Plan

**Date:** 2026-10-01 · **Owner:** NeuralIO 444
**Status:** in progress — builders active on Phase 1+2

## The vision

`Dimension_CLI` (public: https://github.com/NeuralIO444/Dimension_CLI) — Dimension's
format-conform math engine (safe zones, occlusion, deterministic naming, LUT synthesis,
orphan detection, provenance logging) as a clean, powerful, offline-installable CLI that
any shell can drive: a MographJailed prong today, a UXP panel tomorrow.

The CEP panel gets one final v6.1 release and is then **archived**. No new CEP
investment, ever. This is how Dimension stops being a forever-project.

## Definition of done (the anti-forever-project clause)

- [x] `Dimension_CLI` v1.0.0: public, pip-installable, `dimension --help` works,
      README quickstart, CI green, offline install verified.
- [ ] `Dimension` repo: final v6.1 release tagged, ZXP attached to a GitHub Release,
      repo archived, README banner pointing to `Dimension_CLI`.
- [ ] Zero P0 issues open anywhere. Everything else closed as wontfix/documented
      with a reason.
- [ ] After that: maintenance mode. New ideas go to MographJailed or new repos —
      never back into Dimension.

## Phase 1 — Green engine (in flight)

- D2 = channel-first safe-zone precedence (maintainer decided 2026-10-01): move Leg 2.4
  below Leg 3 in `safe_zone_resolver.py`, update the 3–4 scope tests.
- D3 = keep `naming_context` (maintainer decided 2026-10-01): TRANSITIONAL allowlist
  entry in `test_reachability.py`, "staged 2026-09-09, wiring pending". No delete,
  no wiring.
- ruff + pytest green in `Dimension_CLI` (8 failures → 0).
- Merge #552 (ruff fix) in the `Dimension` repo so its main isn't red going into
  the final release.

## Phase 2 — Extract & pare (in flight)

- Engine minus CEP/JSX/panel → `Dimension_CLI`. (Scraper stays Dimension-only
  per the #549 decision — hard boundary.)
- One unified `dimension` CLI: clean subcommand tree, `--json` on every command,
  logs to stderr, real exit codes, flag naming mirrors MographJailed's locked
  Sequoia-native conventions (MJ adapts to nothing — the CLI flexes).
- Provenance, not names (#551): `.dimension/duplication_log.json` becomes the
  source of truth for duplicate detection; the dead regex predicate is deleted.
- LUT injection killed cleanly: #547 (honest-fail `LUT_UNSCRIPTABLE`) merges,
  #494 reclassified as permanent AE platform limitation and documented.
  Levels Smart Match is the supported backend. No workaround chase.
- Offline-installable: PyPI-pinned deps only, no git-SHA dependencies
  (protects the offline-first build philosophy). Verified with no network.
- License: keep Dimension's PolyForm Noncommercial + commercial dual-license
  (maintainer to confirm — recommended: yes, it's the established pattern).

## Phase 3 — Ship the CLI (public)

- README: what it is, install, 5-minute quickstart, which commands need live AE
  vs. which run headless.
- v1.0.0 tag → GitHub Release. CI green.
- Announce wherever the maintainer wants.

## Phase 4 — Retire the CEP repo

- Land the small queue or descope it, in order: #547 → #546 (needs a live
  AE QA) → #540 (needs #551 provenance fix + QA; **descope to report-only**
  if the full fix is too much for a farewell release).
- D4 version number → 6.1.0 (maintainer to confirm). CHANGELOG `[Unreleased]` →
  versioned heading. First `v*` tag → `release.yml` builds the ZXP.
- GitHub Release with the ZXP attached. Archive the repo. README banner →
  `Dimension_CLI`.
- Remaining issues closed explicitly: #346/#541 parked unless the maintainer runs the
  5-point AE QA; #494's workaround dropped.

## Phase 5 — Later (NOT part of done)

- `mj dimension` prong integration into MographJailed.
- UXP shell when Adobe ships AE UXP panels (none exist for AE as of 2026-10-01 —
  verified; readiness only until then).

## Decisions needed from the maintainer (one list)

1. License for the public CLI: keep dual PolyForm Noncommercial + commercial?
   (recommended: yes)
2. D4: final panel release version — 6.1.0? (recommended: yes)
3. Live AE QA — run it for #546 / #540 / #541, or explicitly park them?
4. #540: fix properly with provenance, or descope to report-only for the
   farewell release? (recommended: descope)

## Standing rules carried in

- Builders open PRs; the maintainer merges. Small PRs, green CI, verified by hand.
- No CEP/UX work — engine and CLI only.
- Never touch another builder's branch; never close the maintainer's issues without his word.
- MographJailed's interface is locked: Dimension_CLI conforms to it.
