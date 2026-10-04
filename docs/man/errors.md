# Error codes

`--json` failures are one document: `{"status":"ERROR","code":"...","error":"..."}`.
`code` is stable. The message is for people and may change.

| Exit | Meaning | What to do |
|---|---|---|
| 0 | ok, including an `ae probe` that found no poller | read `reachable` / the hint |
| 1 | the engine refused the job | the message names the flag or the file |
| 2 | the command line was wrong | `dimension <command> --help` |
| 65 | `LUT_UNSCRIPTABLE` | AE cannot script a LUT path; use derive |
| 69 | `AE_UNAVAILABLE` | a live-AE command, and AE is not there |

No failure deletes a comp. Provenance writes are best-effort: a database error is logged and does not fail the conform.
