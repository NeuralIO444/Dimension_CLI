# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Stage 4 — inject dispatch (Babysitter pump in After Effects).

Dispatch is file-bridge only: an inbox job descriptor consumed by the
poller (`Scripts/Dimension_Launcher.jsx`'s legacy panel, or the ported
`cep/jsx/host.jsx` poller — see `.pipeline/plan.md`). The socket-based
`run_socket_inject` / `build_socket_inject_job` transport was removed
(host.jsx poller port, 2026-07) once its only caller
(`dimension_server.py::run_batch_thread`) was confirmed to hit an
invalid `"type": "inject"` wire descriptor — `InjectJob` is untyped and
parsed via `parse_inject_job()`, never through the typed `BridgeJob`
union `build_socket_inject_job` fed into. See `.pipeline/plan.md` §2/§3
for the full investigation.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from bridge.sovereign_bridge import BRIDGE_SCHEMA_VERSION
from core.logger import log

# Statuses that represent a finished Babysitter pump run. Mirrors
# dimension_server.py::TERMINAL_INJECT_STATUSES (Track A Stage 1,
# 2026-08-26) — NOT imported from there. dimension_server.py imports
# `process_inject_logs` / `dispatch_file_bridge_inject` from this module
# at its own top level (dimension_server.py:25-28) BEFORE its own
# TERMINAL_INJECT_STATUSES is defined (dimension_server.py:188).
# Importing it back from here would be a circular import that fails at
# interpreter start — dimension_server would still be mid-import with no
# such attribute yet. Traced in .pipeline/plan.md (Track A Stage 2b, §0).
# If a fourth terminal status is ever added, update BOTH copies AND
# docs/architecture/track-a-beacon-contract.md §7.4.
TERMINAL_INJECT_STATUSES = ("COMPLETE", "FAILED", "ABORTED")

# Percentage the Dashboard's inject progress bar shows for each real
# Babysitter phase anchor. Must mirror cep/js/inject_progress.js's
# INJECT_PHASE_PROGRESS exactly (Track A Stage 2, 2026-08-26) — kept in
# sync by convention (Python and CEP JS share no module system), not by
# import. The previous keys here (SETUP/DUPLICATIONS/REWIRES/CHUNKS/
# AUDIT/COMPLETE) were dead: Babysitter emits `phase` values INJECT /
# REWIRE / REPORT only — see docs/architecture/track-a-beacon-contract.md
# §2/§3 for the byte-level verification against Babysitter.jsx. Do not
# add a key here without first confirming Babysitter actually emits that
# exact phase string via `msg.phase` (not the internal-state `pump.enter`
# marker — see the contract doc §3 note on why that one is excluded).
INJECT_PHASE_PROGRESS = {
    "INJECT": 93,
    "REWIRE": 94,
    "REPORT": 98,
}

# Chunk-tick interpolation band — mirrors cep/js/inject_progress.js's
# CHUNK_BAND_START / CHUNK_BAND_END exactly. The per-chunk progress line
# Babysitter emits (`{chunkIndex, totalChunks, layerName, layerIndex,
# totalLayers, timestamp}`) carries no `phase` key by design (beacon
# contract §2, chunk tick) so it is handled by `_compute_chunk_pct`
# below, separately from INJECT_PHASE_PROGRESS.
CHUNK_BAND_START = 95
CHUNK_BAND_END = 97


@dataclass
class InjectResult:
    completed: bool
    failed: bool
    logs: list[dict[str, Any]]
    warnings: dict[str, int]
    # Added Track A Stage 2b (2026-08-26). Additive/defaulted — existing
    # fields are unchanged and the sole construction site (below) already
    # uses keyword arguments, so this is fully backwards compatible.
    # Distinguishes a deliberate/cooperative abort from both success and
    # the ambiguous completed=False/failed=False fallthrough it replaces.
    # Do not remove or rename completed/failed/logs/warnings — see
    # CLAUDE.md schema discipline and docs/architecture/
    # track-a-beacon-contract.md §7.4 (bullet 4).
    aborted: bool = False
    failed_layer: Optional[str] = None
    failed_layer_uid: Optional[str] = None
    failed_detail: Optional[str] = None


def load_monolithic_manifest(chunk_manifest_path: str) -> dict[str, Any]:
    """Expand chunk_manifest.json into a single in-memory layer list."""
    with open(chunk_manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    layers: list[dict[str, Any]] = []
    base_dir = os.path.dirname(os.path.abspath(chunk_manifest_path))
    for chunk_path in manifest.get("chunk_paths", []):
        abs_chunk_path = os.path.join(base_dir, chunk_path)
        with open(abs_chunk_path, "r", encoding="utf-8") as cf:
            chunk_data = json.load(cf)
            layers.extend(chunk_data.get("layers", []))

    manifest["layers"] = layers
    manifest.pop("chunk_paths", None)
    return manifest


def dispatch_file_bridge_inject(
    project_root: str,
    manifest_path: str,
    log_path: str,
) -> str:
    """Write a file-bridge inject job to `.dimension_inbox/`. Returns job path."""
    inbox = os.path.join(project_root, ".dimension_inbox")
    os.makedirs(inbox, exist_ok=True)

    assets_dir = os.path.join(project_root, "Scripts", "Dimension_Assets")
    job_id = uuid.uuid4().hex
    name = f"job_{job_id}.json"
    tmp_path = os.path.join(inbox, name + ".tmp")
    final_path = os.path.join(inbox, name)

    job_payload = {
        "manifest": os.path.abspath(manifest_path),
        "log": os.path.abspath(log_path),
        "babysitter": os.path.abspath(os.path.join(assets_dir, "Babysitter.jsx")),
        "auditor": os.path.abspath(os.path.join(assets_dir, "Auditor.jsx")),
        "ts": time.time(),
        "version": 1,
        "schema_version": BRIDGE_SCHEMA_VERSION,
    }

    from models.bridge_jobs import parse_inject_job

    parse_inject_job(job_payload)

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(job_payload, f)
    os.replace(tmp_path, final_path)
    # "Job dispatched" is the exact msg qa_common.summarize_log_events /
    # session_correlate key on (the retired Qt launcher used to emit it;
    # the CEP-era file-bridge dispatch had no log line at all, so the
    # correlator's job_dispatched signal was dead). session_id is
    # genuinely not in scope here — the chunk manifest carries no
    # session id and report session ids are generated per-report inside
    # run_conform — so this line stays timestamp-correlated (M8 note).
    log.info("Job dispatched", extra={"inbox": inbox, "job": name})
    return final_path


def _compute_chunk_pct(chunk_index: Any, total_chunks: Any) -> Optional[float]:
    """Interpolate progress within [CHUNK_BAND_START, CHUNK_BAND_END] for
    the chunk that just finished. Mirrors
    cep/js/inject_progress.js::computeChunkProgressPct exactly (same
    guards, same fraction formula) so the Dashboard and CEP panel agree.

    Returns None — caller must leave the bar where it is — if
    `total_chunks` is missing, zero, negative, non-numeric, or a bool
    (Python's bool is an int subclass; JS's `typeof` excludes booleans
    from 'number', so this mirrors that exclusion deliberately).
    """
    if isinstance(total_chunks, bool) or not isinstance(total_chunks, (int, float)) or total_chunks <= 0:
        return None
    if isinstance(chunk_index, bool) or not isinstance(chunk_index, (int, float)):
        return None
    clamped_index = max(0, min(chunk_index, total_chunks - 1))
    fraction = (clamped_index + 1) / total_chunks
    return CHUNK_BAND_START + fraction * (CHUNK_BAND_END - CHUNK_BAND_START)


def process_inject_logs(
    logs: list[dict[str, Any]],
    *,
    on_phase: Optional[Callable[[str, int], None]] = None,
    on_log_line: Optional[Callable[[str], None]] = None,
) -> InjectResult:
    """Parse Babysitter pump messages into completion state + warning counts."""
    warnings = {"skips": 0, "rewire": 0}
    completed = False
    failed = False
    aborted = False
    failed_layer: Optional[str] = None
    failed_layer_uid: Optional[str] = None
    failed_detail: Optional[str] = None

    for msg in logs:
        line = json.dumps(msg)
        if on_log_line:
            on_log_line(line)

        chunk_index = msg.get("chunkIndex")
        if on_phase and isinstance(chunk_index, (int, float)) and not isinstance(chunk_index, bool):
            # Per-chunk progress line — no `phase` key by design (see
            # docs/architecture/track-a-beacon-contract.md §2, chunk tick).
            # Checked ahead of the phase branch below to mirror
            # cep/js/main.js:1092's trigger condition and early-return
            # exclusivity, though the two never overlap in practice.
            chunk_pct = _compute_chunk_pct(chunk_index, msg.get("totalChunks"))
            if chunk_pct is not None:
                total_disp = msg.get("totalChunks")
                on_phase(f"chunk {int(chunk_index) + 1} of {total_disp}", round(chunk_pct))
        else:
            phase = (msg.get("phase") or "").upper()
            if phase and on_phase:
                pct = INJECT_PHASE_PROGRESS.get(phase)
                if pct:
                    on_phase(phase, pct)

            if phase == "REWIRE" and "not found" in (msg.get("detail") or ""):
                warnings["rewire"] += 1

        if msg.get("event") == "telemetry" and (msg.get("phase") or "") == "mirror_setup_skips":
            not_found = msg.get("not_found_count", 0)
            create_failed = msg.get("create_failed_count", 0)
            warnings["skips"] += not_found + create_failed

        if msg.get("status") == "FAILED":
            failed = True
            if not failed_layer and msg.get("failed_layer"):
                failed_layer = str(msg.get("failed_layer"))
            if not failed_layer_uid and msg.get("failed_layer_uid"):
                failed_layer_uid = str(msg.get("failed_layer_uid"))
            if not failed_detail and (msg.get("failed_detail") or msg.get("error")):
                failed_detail = str(msg.get("failed_detail") or msg.get("error"))
        elif msg.get("status") == "COMPLETE":
            completed = True
        elif msg.get("status") == "ABORTED":
            aborted = True

    return InjectResult(
        completed=completed,
        failed=failed,
        logs=logs,
        warnings=warnings,
        aborted=aborted,
        failed_layer=failed_layer,
        failed_layer_uid=failed_layer_uid,
        failed_detail=failed_detail,
    )