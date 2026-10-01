# Dimension — Product Vision

**Status:** Living document
**Last updated:** 2026-10-01
**Owner:** NeuralIO 444

Carried over from Dimension (the After Effects CEP panel) into
Dimension_CLI, reframed: the panel was one shell on the engine.
The engine is the product. This document is the north star for
scope decisions. When in doubt about whether a subcommand belongs
in v1.0, v1.5, or v2.0+, this doc is the reference.

---

## Executive summary

Dimension is an **automation engine for delivery-format conform in
entertainment marketing**. It takes one master After Effects
composition and produces conformed output for one or more delivery
targets — different aspect ratios, safe zones, filename
conventions, and (eventually) different copy/asset variants per
state.

The product ships in three phases:

- **v1.0 — Format Conform Engine.** Per-layer semantic tagging and
  gravity-based placement for cross-aspect conform, as a headless
  CLI: `dimension conform`, `dimension survey`, `dimension safe-zone`,
  `dimension occlusion`, `dimension naming`, `dimension duplication`.
- **v1.5 — Compositional Reflow.** Multi-layer composition
  awareness, vertical-zone semantics, and safe-zone-aware placement
  during reflow.
- **v2.0+ — Variant Matrix Engine.** Asset substitution, state
  machines, locale support, and filename-as-manifest grammar for
  generating full delivery matrices from a single master.

**v1.0 wheelhouse: film marketing.** The patterns and tag taxonomy
are designed around how studios and distributors produce film
campaigns — theatrical, home entertainment, streaming, festival.
This is where Dimension launches and where the beta proves value.

Each phase is a real, shippable, valuable product. v1.0 is enough
to justify a beta. v1.5 is enough to win studio operators as
paying users. v2.0+ is enough to replace existing manual workflows
entirely.

---

## The problem space

Studios, distributors, and creative agencies produce **delivery
matrices** for entertainment marketing.

A single film release generates dozens of creative deliverables
across the campaign lifecycle: pre-release announcement assets,
in-theaters-now assets, home-entertainment pre-street, post-street
"now available" variants, retailer-specific variants, region-specific
variants, state-specific variants, and aspect-specific variants for
every social platform and broadcast format the campaign touches.

Today, this work is manual. A motion graphics designer opens a
horizontal master comp, retargets it to vertical for Reels,
manually repositions every layer, swaps the retailer button,
updates the date, renders, names the file by hand. Then does it
again for the next state. Then the next platform. Then the next
region.

**Dimension automates this.** Not by replacing the designer's
creative judgment, but by encoding the deterministic parts — where
layers go per target, what they're called, when they swap — in a
per-comp tagging system that the designer applies once and reuses
across the entire matrix.

The CLI is the truest form of that idea: a scriptable,
deterministic engine that takes a tagged comp and produces the
matrix. Tag once (`dimension survey`), conform everywhere
(`dimension conform --preset <target>`), check the plan before it
runs (`dimension duplication preview`, `dimension safe-zone plan`).

---

## Distributor archetypes

Dimension supports the same core pattern across very different
operational scales:

- **Major studio.** High-volume output (50+ titles/year × full
  matrix per title). **Value:** automate the template-driven matrix
  work, freeing designers for higher-value creative.
- **Mini-major / prestige.** Mid-volume, bespoke creative, small
  teams. **Value:** scale up output without scaling up team.
- **Indie distributor.** Lean 2–4 person teams. **Value:**
  templates and automation a small team can maintain.
- **Streaming originals.** Algorithm-driven artwork variants per
  region/audience. **Value:** rapid variant generation.
- **Festival / repertory.** Single-designer ops. **Value:**
  templates that punch above their weight.

When designing features, ask "does this serve all archetypes, or
just one?" Features that serve only the major-studio archetype
are v2.0+ enterprise tier, not v1.0 baseline.

---

## v1.0 — Format Conform Engine (this CLI)

The operator tags each layer in a master comp with a semantic tag
from a fixed vocabulary (`dimension survey` classifies
heuristically; the artist corrects once). Dimension applies
per-tag gravity rules to place each layer in the target aspect,
scaled to fit/fill, within the target preset's safe zone
(`dimension conform`).

### Semantic tags (current vocabulary)

- **HERO** — primary subject, center gravity
- **BOX_ART** — packshot/poster, center gravity, scale-aware
- **TT** — typography (text/legals), top gravity
- **SUP** — superimposed text, top gravity
- **LEGAL** — copyright/rating/MPAA, bottom gravity
- **LOGO** — studio/distributor logo, bottom-corner gravity
- **BG** — background, fill
- **ARTWORK** — decorative, center gravity
- **GUIDE** — non-rendering reference, passthrough

### Per-target safe zones

Each target preset (TikTok, Instagram Reels, etc.) defines its safe
area. Resolution order is channel-first (D2, 2026-10-01): per-target
PNG → prefix-stripped PNG → subcategory PNG → multi-panel gap
geometry → channel-derived vector masks → inset/pack fallback →
honest `MASK_MISSING`. Inspect any preset with
`dimension safe-zone plan --preset <id>`.

### Output filename conventions

Conformed comps follow the `{source}_{preset}` naming grammar with
`_vN` collision bumping. Preview with
`dimension naming resolve --source <comp> --preset-id <id>`.

### Provenance, not names

Every comp the engine creates is recorded in the project's
`.dimension/dimension.db` SQLite store. Duplicate/orphan detection
queries provenance — never filename regexes. Inspect with
`dimension provenance duplicates`.

### What v1.0 does NOT do

- Compose layers as a group (each layer placed independently)
- Detect collisions between layers
- Substitute copy or assets per variant
- Generate multiple state variants from one master
- Auto-detect tags via vision models
- Delete anything (`dimension duplication cleanup-report` is
  read-only by decision, 2026-10-01)

---

## v1.5 — Compositional Reflow

**Ships:** post-v1.0. Target: engine support first, CLI flags second.

v1.5 makes Dimension aware that a comp is a composition — not a
pile of independent layers. Layers sharing a semantic role (five
TT-tagged type lines) distribute vertically as a stack. The target
frame gains named zones (header / hero / CTA / legal-footer); tags
bind to zones. Safe-zone obedience moves *into* placement
decisions instead of post-hoc nudging.

---

## v2.0+ — Variant Matrix Engine

**Ships:** long-term vision.

The full automation product: master comp + delivery-matrix spec →
every state × format × locale × asset combination as named output
files. Asset substitution (retailer buttons, dates, CTA copy,
format bars, logos, locale text), campaign state machines
(theatrical / home-entertainment / streaming / festival), locale
support, and filename-as-manifest grammar.

v1.0 ships film-marketing-shaped. v2.0+ generalizes to episodic,
streaming originals, music, sports, live events. Beyond v2.0 is
genuinely open territory and gets scoped when the time comes.

---

## What Dimension is NOT

Explicit scope guards — enforced as CLI scope guards. When a
proposed subcommand doesn't serve the delivery-matrix problem,
it doesn't ship:

- **Not a creative tool.** Dimension doesn't replace After
  Effects. It conforms work already designed in AE. No drawing,
  no keyframing, no compositing subcommands.
- **Not an asset manager.** Source assets live elsewhere;
  Dimension references them. No library, no ingest pipeline.
- **Not a renderer.** It prepares conformed comps for AE's render
  queue / Adobe Media Encoder. It doesn't encode video.
- **Not AI-driven layout.** No vision models guess where layers
  go. The operator tags; Dimension applies deterministic rules.
  Vision-model auto-tagging is out of scope through v2.0+.
- **Not a project management tool.** No approvals, revisions, or
  review cycles. It produces files; humans manage process.
- **Not a localization service.** v2.0 swaps copy/assets per
  region; it doesn't translate text or culturally adapt creative.

---

## CLI architecture mapping

The vision phases map onto the CLI's shape:

| Layer | Role | Example |
|---|---|---|
| `dimension.ops` | pure engine operations (no I/O framing) | `conform_ops.run_conform_op()` |
| `dimension` CLI | human-readable subcommand tree | `dimension conform --preset tiktok_video` |
| `dimension --json` | machine face for scripting / MographJailed | `dimension --json safe-zone plan --preset …` |
| TUI (future) | interactive terminal UI over the same ops | btop/opencode-style dashboard |

Rules that fall out of this:

1. No business logic in CLI handlers — the TUI calls ops, never
   the argument parser.
2. Every command supports `--json`: one JSON document on stdout,
   logs on stderr, meaningful exit codes.
3. Commands that need live After Effects live under `dimension ae`
   and fail with `AE_UNAVAILABLE` (exit 69) when no poller is
   reachable — never a traceback, never a hang.
4. The engine stays offline-installable: PyPI pins only, no
   git-SHA dependencies, stdlib sqlite3 for provenance.

---

## Open design questions

Kept from the original vision doc; resolved as phases ship:

1. **Tag taxonomy completeness.** Do the 9 tags cover episodic,
   music, sports? Extensions become TODOs per vertical.
2. **Zone semantics.** Should distributors define their own
   v1.5 vertical zones, or is header/hero/CTA/legal-footer the
   opinionated default?
3. **Filename grammar formality.** Regex, CFG, or typed schema —
   with per-tenant override?
4. **Asset library architecture.** Where do v2.0's buttons, dates,
   and copy variations live?
5. **State machine ownership.** Hardcoded per campaign type, or
   config-driven with archetype defaults?
6. **Vision-model auto-tagging.** At what v-number, if ever?
7. **Pricing model implications.** Per-archetype tiers — when?

---

## How this document changes

Edit when a phase ships and learnings refine the next phase, an
open design question gets answered, a scope guard changes, or a
new archetype emerges. Don't edit for a single PR (that's the
changelog) or a bug (that's an issue).
