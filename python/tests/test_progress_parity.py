# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_progress_parity.py — Track A Phase A4 / Edge E14.

Cross-language test verifying exact numeric and contract parity between:
1. `python/stages/inject.py` (`INJECT_PHASE_PROGRESS`, `CHUNK_BAND_START`, `CHUNK_BAND_END`, `_compute_chunk_pct`)
2. `cep/js/inject_progress.js` (`DimInjectProgress.INJECT_PHASE_PROGRESS`, `computeChunkProgressPct`)
3. `docs/architecture/track-a-beacon-contract.md` constants
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INJECT_JS_PATH = _REPO_ROOT / "cep" / "js" / "inject_progress.js"

from stages.inject import (  # noqa: E402
    CHUNK_BAND_END,
    CHUNK_BAND_START,
    INJECT_PHASE_PROGRESS,
    _compute_chunk_pct,
)

NODE_BIN = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE_BIN is None, reason="node not found on PATH")


def _eval_js(expr: str) -> any:
    wrapped = f"""
    var DimInjectProgress = require({json.dumps(str(_INJECT_JS_PATH))});
    var result = ({expr});
    console.log(JSON.stringify(result));
    """
    proc = subprocess.run(
        [NODE_BIN, "-e", wrapped],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout.strip())


def test_inject_phase_progress_cross_language_parity():
    """Verify phase map matches in Python, JS, and values."""
    js_map = _eval_js("DimInjectProgress.INJECT_PHASE_PROGRESS")
    assert INJECT_PHASE_PROGRESS == js_map, "Python INJECT_PHASE_PROGRESS must match JS mirror"
    assert INJECT_PHASE_PROGRESS == {"INJECT": 93, "REWIRE": 94, "REPORT": 98}


def test_chunk_band_constants_cross_language_parity():
    """Verify chunk band constants match."""
    js_start = _eval_js("DimInjectProgress.CHUNK_BAND_START")
    js_end = _eval_js("DimInjectProgress.CHUNK_BAND_END")
    assert CHUNK_BAND_START == js_start == 95
    assert CHUNK_BAND_END == js_end == 97


@pytest.mark.parametrize(
    "chunk_idx,total_chunks",
    [
        (0, 1),
        (0, 5),
        (1, 5),
        (2, 5),
        (3, 5),
        (4, 5),
        (0, 100),
        (50, 100),
        (99, 100),
        (-1, 5),     # clamp below
        (10, 5),     # clamp above
        (0, 0),      # invalid total
        (0, -5),     # invalid negative total
        (True, 5),   # bool guard
        (0, False),  # bool guard
    ],
)
def test_chunk_computation_cross_language_parity(chunk_idx, total_chunks):
    """Verify _compute_chunk_pct in Python returns identical float to JS computeChunkProgressPct."""
    py_val = _compute_chunk_pct(chunk_idx, total_chunks)
    js_idx = json.dumps(chunk_idx)
    js_total = json.dumps(total_chunks)
    js_val = _eval_js(f"DimInjectProgress.computeChunkProgressPct({js_idx}, {js_total})")

    if py_val is None:
        assert js_val is None
    else:
        assert js_val is not None
        assert abs(py_val - js_val) < 1e-6


def test_multi_chunk_large_job_monotonic_progression():
    """Simulate a 10-chunk large job with interleaved telemetry, phase, and chunk beacons."""
    from stages.inject import process_inject_logs

    logs = [
        {"phase": "INJECT", "msg": "Starting inject"},
        {"event": "telemetry", "phase": "mirror_setup_skips", "not_found_count": 0, "create_failed_count": 0},
        {"phase": "REWIRE", "detail": "Rewiring 12 precomps"},
    ]

    for i in range(10):
        logs.extend([
            {"event": "telemetry", "msg": f"processing layer {i*30}"},
            {"chunkIndex": i, "totalChunks": 10, "layerName": f"Layer_{i}", "layerIndex": i+1, "totalLayers": 300},
        ])

    logs.extend([
        {"phase": "REPORT", "msg": "Compiling final QC report"},
        {"event": "qc_summary", "errors": 0},
        {"status": "COMPLETE"},
    ])

    recorded_pcts: list[int] = []

    def _record_phase(name: str, pct: int):
        recorded_pcts.append(pct)

    result = process_inject_logs(logs, on_phase=_record_phase)
    assert result.completed is True
    assert result.failed is False
    assert result.aborted is False

    # Check strictly non-decreasing progression
    for i in range(len(recorded_pcts) - 1):
        assert recorded_pcts[i] <= recorded_pcts[i+1], (
            f"Progress retrograded from {recorded_pcts[i]} to {recorded_pcts[i+1]} at index {i}"
        )

    # Initial phase is 93 (INJECT) or 94 (REWIRE)
    assert recorded_pcts[0] == 93
    assert 94 in recorded_pcts
    # Chunks interpolate in 95..97
    assert any(95 <= p <= 97 for p in recorded_pcts)
    # Final REPORT is 98
    assert recorded_pcts[-1] == 98

