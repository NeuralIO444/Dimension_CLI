# Dimension_CLI

**One film. Dozens of deliverables. Zero hand-retargeting.**

A single film release generates dozens of creative deliverables
across the campaign lifecycle: pre-release announcements,
in-theaters-now assets, home-entertainment pre-street and
post-street variants, retailer-specific variants, region-specific
variants — and aspect-specific variants for every social platform
and broadcast format the campaign touches.

Today, this work is manual. A motion graphics designer opens a
horizontal master comp, retargets it to vertical for Reels,
manually repositions every layer, swaps the retailer button,
updates the date, renders, names the file by hand. Then does it
again for the next state. Then the next platform. Then the next
region.

**Dimension automates the deterministic parts.** The designer tags
each layer once — HERO, TT, LEGAL, LOGO, BG… — and the engine
encodes where layers go per target, what they're called, and which
safe zones they must obey. Tag once, conform everywhere:

```bash
pip install dimension-cli

dimension survey /path/to/scrape_manifest.json
dimension conform --source /path/to/scrape_manifest.json --preset tiktok_video
```

`dimension` is the headless format-conform engine extracted from
[Dimension](https://github.com/NeuralIO444/Dimension) (the After
Effects CEP panel). The panel was one shell on this engine; the
engine is the product. No CEP, no JSX, no panel — just the math,
the pipeline, and a clean command tree.

---

## Install

```bash
pip install dimension-cli
# or editable, from source:
pip install -e ".[test]"
```

Dependencies are exact PyPI pins only — no git-SHA dependencies —
so the package installs offline from a local wheelhouse. Provenance
uses stdlib `sqlite3`; nothing else to configure.

Requires Python 3.11+.

---

## Five-minute quickstart

```bash
# 1. See the delivery targets (794 built-in presets)
dimension catalog presets

# 2. Check a preset's safe zone before you conform
dimension safe-zone plan --preset tiktok_video

# 3. Classify layers on a scraped manifest (writes tags back in place)
dimension survey scrape_manifest.json --dry-run   # preview first
dimension survey scrape_manifest.json             # commit tags

# 4. Conform: manifest -> chunk JSON + report, headless
dimension conform --source scrape_manifest.json --preset tiktok_video

# 5. Before anything touches AE: what WILL be duplicated?
dimension duplication preview --manifest scrape_manifest.json \
    --width 1080 --height 1920

# 6. Resolve the output comp name (collision-bumping included)
dimension naming resolve --source Hero_Campaign --preset-id tiktok_video
```

Applying conformed chunks back into After Effects ("inject") is a
separate live-AE step via the bridge — `dimension ae probe` tells
you whether AE is reachable.

---

## Command tree

`[headless]` commands run anywhere. `[live AE]` commands need After
Effects running with the Dimension poller
(`Scripts/Dimension_Launcher.jsx` → File → Scripts).

| Command | What it does |
|---|---|
| `conform --source … --preset …` | Run the pipeline: scale → lerp → occlusion → chunks `[headless]` |
| `survey <manifest>` | Tag-classifier heuristics over a scrape manifest `[headless]` |
| `safe-zone plan --preset …` / `list` | Which mask a preset resolves to + GO/NUDGE/CUTOFF coverage `[headless]` |
| `occlusion zones --preset …` / `check` | SOE zone fractions; relayout pass over conformed layers `[headless]` |
| `naming resolve --source …` | Output comp name with `_vN` collision bumping `[headless]` |
| `lut validate` / `derive` / `derive-smart` | Local LUT math (parse, synthesize) `[headless]` |
| `lut inject` | **Honest-fail**: AE can't script a LUT path (exit 65, `LUT_UNSCRIPTABLE`) |
| `duplication preview` | What *will* be duplicated for this conform `[headless]` |
| `duplication cleanup-report` | **Read-only** orphan report — never deletes `[headless]` |
| `provenance duplicates` / `runs` / `import` | Query `.dimension/dimension.db` (provenance, not names) `[headless]` |
| `catalog presets` / `show` / `profiles` | Delivery targets + studio profiles `[headless]` |
| `target add` / `remove` | User custom targets ("Save as Preset") `[headless]` |
| `ae probe` | AE/poller reachability check (never sends a job) `[live AE]` |
| `ae mask show --preset …` / `hide` | Safe-zone overlay in the active AE comp `[live AE]` |

Every command supports `--json` (see below). `dimension <command> --help`
documents each flag.

---

## The machine face: `--json`

Built for scripting and for MographJailed, which invokes `dimension`
as a subprocess:

```bash
dimension --json safe-zone plan --preset tiktok_video
```

- **stdout:** exactly one JSON document. Zero decorative output.
- **stderr:** all logs (the engine's structured logger already writes there).
- **exit codes:** `0` ok · `1` error · `2` usage ·
  `65` `LUT_UNSCRIPTABLE` (the op is impossible on AE's scripting
  platform — a permanent limitation, not a bug) ·
  `69` `AE_UNAVAILABLE` (a live-AE command with no AE reachable).

Error payloads are JSON too:
`{"status": "ERROR", "code": "...", "error": "..."}`.

The default (no `--json`) is the human face: readable summaries on
stdout. A Textual TUI is planned as the interactive human face; it
will call the same `dimension.ops` functions the CLI calls — no
business logic lives in the argument handlers.

---

## Scope guards

From [docs/VISION.md](docs/VISION.md) — enforced, not aspirational.
When a proposed subcommand doesn't serve the delivery-matrix
problem, it doesn't ship. Dimension is **not**:

- a creative tool (no drawing, keyframing, compositing — it
  conforms work already designed in AE),
- an asset manager,
- a renderer (it prepares comps for AE's render queue; it doesn't
  encode video),
- AI-driven layout (the operator tags; Dimension applies
  deterministic rules — no vision models),
- a project management tool,
- a localization service.

It also never deletes: `duplication cleanup-report` is read-only
by decision, and duplicate detection queries provenance
(`.dimension/dimension.db`), never filename regexes.

---

## Provenance

Every comp the engine creates is recorded in the project's
`.dimension/dimension.db` SQLite store (tables: `creations`,
`runs`). One DB per project directory; manifests and reports stay
JSON. Query it:

```bash
dimension provenance duplicates --project-dir /path/to/project
dimension provenance runs --project-dir /path/to/project --limit 10
```

---

## License

Dual-licensed: **PolyForm Noncommercial 1.0.0** + commercial.
See [LICENSE](LICENSE). Commercial use requires a separate license.

---

## Links

- Product vision & scope guards: [docs/VISION.md](docs/VISION.md)
- Build program & definition of done: [GAMEPLAN.md](GAMEPLAN.md)
- Original CEP panel repo (farewell release, then archived):
  [NeuralIO444/Dimension](https://github.com/NeuralIO444/Dimension)
