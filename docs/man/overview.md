# Dimension

Dimension is the format-conform engine. Tag a layer once. Conform it everywhere.

Current line: Dimension_CLI 1.0.0
Sister runtime: MographJailed (protocol v1)
Machine face: `dimension --json`

## What it does

A film release needs dozens of deliverables. Dimension does the deterministic part: where a tagged layer goes, what the output comp is named, and which safe zone it must obey. It does not draw, keyframe, render, or invent layout.

## Quick commands

```text
dimension            this card
dimension ops        every command, one line each
dimension man        local help
dimension man errors exit codes and what to do
dimension catalog presets
dimension safe-zone plan --preset tiktok_video
dimension conform --source scrape_manifest.json --preset tiktok_video
```

`[headless]` runs anywhere. `ae` commands need After Effects and the Dimension poller. They never send a job from `ae probe`.

## Production rules

No CEP. No panel. Logs on stderr. One JSON document on stdout when `--json` is set. Duplicate detection uses `.dimension/dimension.db`, not filenames. The CLI never deletes a comp.

Topics: `dimension man commands`, `dimension man errors`, `dimension man mograph`.

Tutorials: `docs/tutorials/`. Live After Effects is a workstation gate, not CI.
