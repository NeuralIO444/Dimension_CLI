# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Dimension Engine — I/O Utilities
Atomic write-and-rename + SHA-256 manifest hashing.
All disk writes in the pipeline use atomic_write() to prevent AE from
reading a half-written file during the race condition window.
"""

import os
import hashlib
import json
import tempfile
import time
from typing import Any, Callable, Optional

# Exponential-backoff defaults for robust_read_json. 5 attempts with the
# delay doubling from 20ms gives 20+40+80+160 = 300ms of total waiting
# across 4 gaps — the budget ADR 03 specifies for NTFS lock contention.
_READ_MAX_ATTEMPTS = 5
_READ_INITIAL_DELAY_S = 0.02


def robust_read_json(
    path: str,
    *,
    max_attempts: int = _READ_MAX_ATTEMPTS,
    initial_delay_s: float = _READ_INITIAL_DELAY_S,
    _sleep: Optional[Callable[[float], None]] = None,
) -> Any:
    """Read + parse JSON, retrying briefly on transient lock/torn-read errors.

    TASK-ENG-05 / ADR 03. On Windows, a reader hitting a file that an
    antivirus scanner, the search indexer, or AE itself still holds open
    gets `PermissionError: [WinError 32]`. The file bridge exchanges JSON
    constantly, so a single unlucky read aborts a conform for a condition
    that clears in milliseconds.

    Retries on:
      - `PermissionError` — the WinError 32 case above.
      - `json.JSONDecodeError` — a torn read of a file written
        non-atomically. Python-side writers use `atomic_write_json`, but
        JSX-side writers do not all go through an atomic rename, so a
        reader can observe a partially-flushed file.

    Deliberately does NOT retry `FileNotFoundError`:
      - `os.replace()` is atomic, so a reader never sees a file briefly
        absent mid-rename — absence means genuinely absent.
      - Callers throughout this codebase use absence as a control-flow
        signal ("no sidecar yet", "no prior log"). Burning 300ms on every
        such check would be a silent, repo-wide slowdown.

    On exhaustion the LAST exception is re-raised unchanged, so callers
    keep the specific error type (and the WinError number) they already
    handle. Never raises a wrapper type.

    `_sleep` is injectable for tests; production passes None (time.sleep).
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
    sleep = _sleep if _sleep is not None else time.sleep

    delay = initial_delay_s
    last_exc: Optional[BaseException] = None
    for attempt in range(max_attempts):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (PermissionError, json.JSONDecodeError) as exc:
            last_exc = exc
            # No sleep after the final attempt — that delay buys nothing
            # and just makes the caller's failure path slower.
            if attempt < max_attempts - 1:
                sleep(delay)
                delay *= 2
    assert last_exc is not None  # unreachable: loop runs >= 1 time
    raise last_exc


def atomic_write_json(path: str, data: Any, indent: int = 4) -> str:
    """
    Write JSON to a .tmp file then atomically rename to the target path.
    Returns the SHA-256 hex digest of the written content.
    """
    content = json.dumps(data, indent=indent)
    return atomic_write_text(path, content)


def atomic_write_text(path: str, content: str) -> str:
    """
    Write raw text atomically. Returns SHA-256 hex digest of the content.
    """
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    dir_name = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        os.unlink(tmp_path)
        raise
    return digest


def write_hash_sidecar(manifest_path: str, digest: str) -> None:
    """Write <manifest_path>.sha256 containing the hex digest (atomic)."""
    sidecar = manifest_path + ".sha256"
    atomic_write_text(sidecar, digest)


def verify_manifest_hash(manifest_path: str) -> bool:
    """
    Read manifest_path and compare its SHA-256 against the sidecar.

    Contract (Bug G C3, restored 2026-04-29):
      - sidecar present + match → returns True (intact)
      - sidecar present + mismatch → raises ValueError (corruption
        detected, conform must halt)
      - sidecar missing → raises FileNotFoundError. Caller is expected
        to interpret this as the "AE-panel scrape, no Python seal"
        path and proceed without a check. Bug G's JSX-side sidecar
        invalidation (C2) ensures missing-sidecar means a JSX-only
        write happened intentionally — never a stale-sidecar drift.

    Pre-Bug-G this raised on missing-sidecar too, which produced
    nuisance ERRORs for legitimate AE-panel scrapes. v5.1.17's
    `soft_check_manifest_integrity` papered over that by going
    advisory-only — but the soft variant also silenced real
    mismatches (Bug G's symptom). Strict semantics restored;
    callers handle FileNotFoundError as the legacy-no-sidecar path.
    """
    sidecar = manifest_path + ".sha256"
    if not os.path.exists(sidecar):
        raise FileNotFoundError(f"Hash sidecar missing: {sidecar}. Run a fresh scrape.")
    with open(sidecar, "r") as f:
        expected = f.read().strip()
    with open(manifest_path, "r", encoding="utf-8") as f:
        content = f.read()
    actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if actual != expected:
        raise ValueError(
            f"INTEGRITY FAILURE: scrape_manifest.json hash mismatch.\n"
            f"  Expected: {expected}\n"
            f"  Actual:   {actual}\n"
            "File may be corrupted. Re-scrape from After Effects."
        )
    return True
