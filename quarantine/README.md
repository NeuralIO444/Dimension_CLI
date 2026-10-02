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

---

Quarantined 2026-10-02 (PR #25, CEP abandonment):

Matt killed CEP on 2026-10-02: no farewell v6.1 panel release, no AE QA, the
Dimension CEP repo will be archived once the CLI ships. These 38 modules test
CEP/JSX artifacts (`Scripts/Dimension_Assets/*.jsx`, `cep/`, `Babysitter.jsx`)
that were deliberately left behind in the extraction — they can never pass in
this repo and will never be revived here. Kept (not deleted) as the record of
what the CEP surface was tested for, in case anything needs porting to a
future UXP shell.

| Module | Needs | Count |
|---|---|---|
| `test_babysitter_*.py` (11 modules) | `Scripts/Dimension_Assets/Babysitter.jsx` | 107 |
| `test_sovereign_core_*.py` (2) | `Scripts/Dimension_Assets/SovCore_*.jsx` | 32 |
| `test_*_jsx*.py`, `test_*_js_*.py`, `test_cep_js_syntax.py` | `cep/`, `*.jsx` mirrors | 32 |
| `test_schema_version.py`, `test_cryptographic_handshake.py`, `test_tag_registry.py` | `Scripts/Dimension_Assets/version.jsx`, JSX tag mirror | 20 |
| `test_progress_parity.py`, `test_expression_scaling_harness.py` | Node + Babysitter.jsx | 24 |
| `test_*_reachability*.py` (jsx/field), `test_loop_path_guard.py` | JSX/CEP scan targets | 8 |
| `test_panel_slicing_bridge.py`, `test_report_dashboard_assets.py`, `test_studio_deck_contracts.py` | `cep/` panel assets | 12 |
| `test_verify_math_and_pillars_tool.py`, `test_silent_audio_render_queue.py`, `test_in_verification_contracts.py`, `test_gpu_16k_allocation_guard.py`, `test_dimension_server_inject.py`, `test_surveyor_typo.py`, `test_comment_directives.py`, `test_chunk_payload_contract.py`, `test_fixes_1_3_4.py`, `test_perf1_telemetry_markers.py` | various `Scripts/*.jsx`, `cep/` | 43 |
