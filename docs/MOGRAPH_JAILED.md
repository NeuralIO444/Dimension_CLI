# MographJailed prong

MographJailed calls this CLI. This CLI does not call MographJailed.

The locked request surface is three allowlisted operations. MJ execs a fixed argv; it does not open a shell.

| MJ command | Required args | argv |
|---|---|---|
| `dimension.probe` | none | `dimension --json catalog profiles` |
| `dimension.safezone` | `spec` preset id | `dimension --json safe-zone plan --preset <spec>` |
| `dimension.conform` | `path` manifest, `spec` preset id | `dimension --json conform --source <path> --preset <spec>` |

Preset ids are `[A-Za-z0-9_:-]{1,128}`. `path` must already exist. stdout is one JSON document; logs stay on stderr. Exit `69` from MJ means the `dimension` binary was not found (`MJ_DIMENSION_BIN` or `PATH`).

`project.conform` in MographJailed is a different operation (studio naming plan). Do not alias it to this engine.
