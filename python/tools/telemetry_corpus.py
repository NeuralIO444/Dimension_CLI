#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/telemetry_corpus.py
Phase 2 of the autonomous-engineering initiative — the "hooks feeding back
into the harness" ask, built on infrastructure that already exists rather
than a new logging system: `core/logger.py` already writes every
`log.warning`/`log.info(..., extra={...})` call as newline-delimited JSON to
`logs/dimension.log`. This tool reads that stream and extracts the subset
worth retaining as a permanent, deduped corpus — real degenerate geometry
and silent-skip conditions actually seen in production conforms, for Phase 4
(the PR-diff-aware generator) and Phase 5 (fuzzing) to draw seeds from,
instead of only synthetic hand-written cases.

Deliberately does NOT touch any conform-pipeline source file to add new
`extra` tagging — every filter here matches on the message text or the
originating module of EXISTING log calls. Tagging pipeline call sites with a
dedicated event-class marker is real, but it's a pipeline-file change and
waits for explicit approval / the Phase 7 policy carve-out, not bundled in
here.

Usage:
  python3 python/tools/telemetry_corpus.py                 # scan logs/, append new entries
  python3 python/tools/telemetry_corpus.py --log-dir logs/tests
  python3 python/tools/telemetry_corpus.py --dry-run        # report counts, don't write
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CORPUS = _REPO_ROOT / ".dimension" / "telemetry" / "corpus.jsonl"

# Message substrings worth retaining verbatim, regardless of module/level —
# these are known, established signal strings already used elsewhere in the
# codebase (grep for them before assuming a new one belongs here).
_MESSAGE_PATTERNS = (
    "MASK_MISSING",
    "SHATTER GUARD",
    "Camera depth-axis K diverges",
    "scene-preserve bypass",
    "collapse_warnings",
)

# Modules whose WARNING/ERROR-level lines are worth retaining even without a
# specific message match — these are conform-pipeline-adjacent, so anything
# they warn about is plausibly a near-miss or unusual-geometry signal.
_WATCHED_MODULES = frozenset({
    "scale_engine", "scale_engine_narrow", "scale_engine_widen",
    "scale_engine_edr", "occlusion_engine", "conform_passes",
    "safe_zone_resolver", "variant_gate", "gravity", "comment_gardener",
    "placement_units", "matrix_math",
})


def _iter_log_files(log_dir: Path) -> Iterator[Path]:
    base = log_dir / "dimension.log"
    if base.is_file():
        yield base
    for p in sorted(glob.glob(str(log_dir / "dimension.log.*"))):
        yield Path(p)


def _iter_records(log_dir: Path) -> Iterator[Dict[str, Any]]:
    for path in _iter_log_files(log_dir):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
        except OSError:
            continue


def _is_telemetry_worthy(record: Dict[str, Any]) -> bool:
    msg = str(record.get("msg", ""))
    if any(p in msg for p in _MESSAGE_PATTERNS):
        return True
    level = str(record.get("level", "")).upper()
    module = str(record.get("module", ""))
    if level in ("WARNING", "ERROR") and module in _WATCHED_MODULES:
        return True
    return False


def _dedup_key(record: Dict[str, Any]) -> str:
    # Same message + same non-timestamp payload = same near-miss, seen
    # again. Excludes ts/level/module so the key is about content, not
    # when/where it was logged.
    stable = {k: v for k, v in record.items() if k not in ("ts",)}
    blob = json.dumps(stable, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build_corpus(log_dir: Path, corpus_path: Path, dry_run: bool = False) -> Dict[str, int]:
    existing_keys: set[str] = set()
    if corpus_path.is_file():
        with open(corpus_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    existing_keys.add(json.loads(line)["_key"])
                except (ValueError, KeyError):
                    continue

    scanned = 0
    worthy = 0
    new_entries: List[Dict[str, Any]] = []

    for record in _iter_records(log_dir):
        scanned += 1
        if not _is_telemetry_worthy(record):
            continue
        worthy += 1
        key = _dedup_key(record)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        entry = dict(record)
        entry["_key"] = key
        new_entries.append(entry)

    if not dry_run and new_entries:
        os.makedirs(corpus_path.parent, exist_ok=True)
        with open(corpus_path, "a", encoding="utf-8") as f:
            for entry in new_entries:
                f.write(json.dumps(entry, default=str) + "\n")

    return {"scanned": scanned, "worthy": worthy, "new": len(new_entries)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log-dir", default="logs", help="Directory containing dimension.log (default: logs/)")
    parser.add_argument("--out", default=str(_DEFAULT_CORPUS), help=f"Corpus output path (default: {_DEFAULT_CORPUS})")
    parser.add_argument("--dry-run", action="store_true", help="Report counts without writing")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    if not log_dir.is_absolute():
        log_dir = _REPO_ROOT / log_dir
    corpus_path = Path(args.out)

    stats = build_corpus(log_dir, corpus_path, dry_run=args.dry_run)
    print(
        f"Scanned {stats['scanned']} log lines from {log_dir} — "
        f"{stats['worthy']} telemetry-worthy, {stats['new']} new "
        f"({'dry run, not written' if args.dry_run else f'appended to {corpus_path}'})"
    )


if __name__ == "__main__":
    main()
