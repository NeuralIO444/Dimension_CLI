# Contract with MographJailed

Pinned: Dimension_CLI 1.0.0.

```text
dimension.probe      dimension --json catalog profiles
dimension.safezone   dimension --json safe-zone plan --preset <spec>
dimension.conform    dimension --json conform --source <path> --preset <spec>
```

Stdout is one document. `status` is `OK` or `ERROR`. Logs are on stderr. MographJailed wraps that document. It does not expect `ok` / `data` on this stdout.

The binary is `dimension` on `PATH`, or `MJ_DIMENSION_BIN`. `project.conform` in MographJailed is not this engine.
