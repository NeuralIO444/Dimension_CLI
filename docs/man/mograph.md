# MographJailed

MographJailed calls this CLI. This CLI does not call MographJailed.

```text
dimension.probe       dimension --json catalog profiles
dimension.safezone    dimension --json safe-zone plan --preset <spec>
dimension.conform     dimension --json conform --source <path> --preset <spec>
```

`spec` is the preset id. `path` is an existing manifest. The binary is `dimension` on `PATH`, or `MJ_DIMENSION_BIN`.

`project.conform` in MographJailed is a naming plan. It is not this engine.
