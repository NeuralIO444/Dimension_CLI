#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/scripts/perf1_analyse_gap.py

PERF-1 Phase 2 — parses transfer_status.log after an inject run and
bisects the rewire→chunk.start gap using the four telemetry markers
shipped in commit 8990488 (plan.md §4.2):

    pump.enter              (every _pump() entry; t_ms)
    rewire_undo_close       (telemetry; duration_ms of app.endUndoGroup)
    pump.rewire.schedule    (anchor before scheduler handoff; t_ms)
    schedule_task_call      (telemetry; duration_ms of app.scheduleTask)

Segment model (telescoping — segments sum to the total by construction):

    t0 = rewire_undo_close.t_ms - duration_ms   undo-group close begins
    t1 = rewire_undo_close.t_ms                 undo-group close done
    t2 = pump.rewire.schedule.t_ms              rewire handler tail done
    t3 = schedule_task_call.t_ms                scheduleTask registered
    t4 = next pump.enter.t_ms (phase=chunk)     AE gave control back
    t5 = pump.chunk.start.t_ms (first chunk)    chunk work begins

    undo_close      = t1-t0    ← Hypothesis 2a: deferred recalc on commit
    handler_tail    = t2-t1    ← logging/bookkeeping after the commit
    schedule_reg    = t3-t2    ← the app.scheduleTask() call itself
    scheduler_dead  = t4-t3    ← Hypothesis 2b: AE busy before first fire
    chunk_entry     = t5-t4    ← _pump dispatch to chunk.start

The dominant segment tells us where the ~13.3s actually goes. This
script only does arithmetic on what JSX logged — it proves nothing by
itself; the log must come from a real AE inject run.

Usage (from repo root, after running the inject in AE):
    python3 python/scripts/perf1_analyse_gap.py [path-to-transfer_status.log]

Defaults to ./transfer_status.log. Pass --json for machine-readable
output. Exit code 0 = all four markers found and ordering sane;
1 = markers missing or ordering broken (stale JSX in AE is the usual
cause — check the panel's repoRoot and reload).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parse_lines(log_path: Path) -> list[dict]:
    """Read transfer_status.log; return parsed JSON records, skipping
    unparseable lines (JSX writes some non-JSON banner lines)."""
    out: list[dict] = []
    if not log_path.is_file():
        return out
    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _is(rec: dict, event: str | None = None, phase: str | None = None) -> bool:
    if event is not None and rec.get("event") != event:
        return False
    if phase is not None and rec.get("phase") != phase:
        return False
    return True


def analyse(records: list[dict]) -> dict:
    """Extract the rewire→first-chunk window and compute the segment
    table. Returns a dict with 'ok', 'errors', 'segments', 'total_ms',
    'dominant'. Uses the LAST rewire sequence in the log (the most
    recent inject run) so stale earlier runs in an appended log don't
    skew the result."""
    errors: list[str] = []

    # Anchor on the last rewire_undo_close in the log.
    undo_idx = None
    for i in range(len(records) - 1, -1, -1):
        if _is(records[i], event="telemetry", phase="rewire_undo_close"):
            undo_idx = i
            break
    if undo_idx is None:
        return {
            "ok": False,
            "errors": [
                "marker 'rewire_undo_close' not found — AE is likely running "
                "stale JSX (check panel repoRoot, reload panel, re-run inject)"
            ],
        }

    undo = records[undo_idx]
    if "t_ms" not in undo or "duration_ms" not in undo:
        errors.append("rewire_undo_close is missing t_ms/duration_ms fields")

    def _next(idx: int, **match) -> dict | None:
        for rec in records[idx + 1 :]:
            if _is(rec, **match):
                return rec
        return None

    rewire_sched = _next(undo_idx, event="pump.rewire.schedule")
    sched_call = _next(undo_idx, event="telemetry", phase="schedule_task_call")
    chunk_enter = None
    for rec in records[undo_idx + 1 :]:
        if _is(rec, event="pump.enter") and rec.get("phase") == "chunk":
            chunk_enter = rec
            break
    chunk_start = _next(undo_idx, event="pump.chunk.start")

    for name, rec in [
        ("pump.rewire.schedule", rewire_sched),
        ("schedule_task_call", sched_call),
        ("pump.enter (phase=chunk)", chunk_enter),
        ("pump.chunk.start", chunk_start),
    ]:
        if rec is None:
            errors.append(f"marker '{name}' not found after rewire_undo_close")
        elif "t_ms" not in rec:
            errors.append(f"marker '{name}' has no t_ms stamp")

    if errors:
        return {"ok": False, "errors": errors}

    t1 = undo["t_ms"]
    t0 = t1 - undo.get("duration_ms", 0)
    t2 = rewire_sched["t_ms"]
    t3 = sched_call["t_ms"]
    t4 = chunk_enter["t_ms"]
    t5 = chunk_start["t_ms"]

    stamps = [("t0", t0), ("t1", t1), ("t2", t2), ("t3", t3), ("t4", t4), ("t5", t5)]
    for (na, a), (nb, b) in zip(stamps, stamps[1:]):
        if b < a:
            errors.append(f"ordering broken: {nb} ({b}) < {na} ({a})")
    if errors:
        return {"ok": False, "errors": errors}

    segments = {
        "undo_close (endUndoGroup commit)": t1 - t0,
        "handler_tail (rewire cleanup)": t2 - t1,
        "schedule_reg (app.scheduleTask)": t3 - t2,
        "scheduler_dead (AE busy pre-fire)": t4 - t3,
        "chunk_entry (_pump -> chunk.start)": t5 - t4,
    }
    total = t5 - t0
    dominant = max(segments, key=lambda k: segments[k])
    return {
        "ok": True,
        "errors": [],
        "segments": segments,
        "total_ms": total,
        "dominant": dominant,
        "dominant_pct": (100.0 * segments[dominant] / total) if total > 0 else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PERF-1 rewire-gap bisection")
    ap.add_argument("log", nargs="?", default="transfer_status.log")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    records = parse_lines(Path(args.log))
    if not records:
        print(f"FAIL: no parseable JSON lines in {args.log}", file=sys.stderr)
        return 1

    result = analyse(records)
    if args.as_json:
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1

    if not result["ok"]:
        print("FAIL — gap bisection could not run:")
        for e in result["errors"]:
            print(f"  - {e}")
        return 1

    print("PERF-1 gap bisection (rewire → first chunk.start)")
    print(f"  total gap: {result['total_ms']} ms")
    print("  segment breakdown:")
    for name, ms in result["segments"].items():
        pct = 100.0 * ms / result["total_ms"] if result["total_ms"] else 0.0
        print(f"    {name:<40} {ms:>8} ms  ({pct:5.1f}%)")
    print(
        f"  DOMINANT: {result['dominant']} "
        f"({result['dominant_pct']:.1f}% of the gap)"
    )
    print("  PASS — all four markers present, ordering sane.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
