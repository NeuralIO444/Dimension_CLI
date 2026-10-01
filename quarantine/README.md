# Quarantined tests

These test modules break pytest *collection* on the extraction stack and were
moved out of `python/tests/` (pytest's `testpaths`) to unblock CI signal on
PR #25. **Not deleted** — they are fault-fix-loop backlog and may be revived
or rewritten there.

Quarantined 2026-10-01 (PR #25, "[#19] Ship queue"):

| Module | Failure at collection | Root cause |
|---|---|---|
| `test_cep_jsx_bundle_sync.py` | `FileNotFoundError: .../package.sh` | References repo-root `package.sh`, which doesn't exist in this repo |
| `test_git_hooks.py` | `FileNotFoundError: .../package.sh` | Same — references `package.sh` |
| `test_debug_bundle.py` | `ModuleNotFoundError: pytest_dimension_ae` | Imports the AE-live harness dropped in PR #8 |
| `test_session_correlate.py` | `ModuleNotFoundError: pytest_dimension_ae` | Same — `from pytest_dimension_ae.probes import probe_ae` |
| `test_image_diff.py` | `ModuleNotFoundError: tests.cep_harness` | `from tests.cep_harness.image_diff import compare_images`; the `cep_harness` package doesn't exist |

To revive: fix the underlying import/path, move back to `python/tests/`,
and confirm `pytest --collect-only` is clean.
