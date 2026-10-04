# Commands

Everyday use prints sentences. Add `--json` for one document on stdout. Logs stay on stderr.

```text
conform --source <manifest> --preset <id>     scale, place, occlude, write chunks
survey <manifest>                             tag layers; --dry-run previews
safe-zone plan --preset <id>                  mask and GO/NUDGE/CUTOFF
safe-zone list                                masks on disk
occlusion zones --preset <id>                 zone fractions
occlusion check                               relayout over conformed layers
naming resolve --source <name> --preset-id <id>
lut validate | derive | derive-smart          local LUT math
lut inject                                    honest fail, exit 65
duplication preview                           what will be forked
duplication cleanup-report                    orphans, read-only
provenance duplicates | runs | import         SQLite store
catalog presets | show | profiles
target add | remove                           your presets
ae probe                                      is the poller there? sends nothing
ae mask show | hide                           overlay in the active comp
ops                                           this list
man [topic]                                   help
```

A missing `--preset` on conform names the flag and exits 1. That is an engine error, not a usage error.
