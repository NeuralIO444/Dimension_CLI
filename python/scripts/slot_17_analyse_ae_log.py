#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/scripts/slot_17_analyse_ae_log.py

Slot 17 addendum #2 — parses logs/dimension.log for the new bridge
handshake + JSX pump-tick markers and emits a per-run wall-clock
breakdown to stdout.

Reads markers shipped in Phase 1 addenda:

  Bridge handshake (per round-trip — scrape, tag-write, etc.):
    bridge.<type>.poll_dead       (dispatch → JSX claim)
    bridge.<type>.jsx_dispatch    (claim → handler entry)
    bridge.<type>.jsx_work        (handler entry → result write)
    bridge.<type>.python_read_lag (result write → Python read)
    bridge.<type>.wall_total      (dispatch → Python read)

  Inject pump (Babysitter, transfer_status.log → forwarded to dimension.log):
    pipeline.phase: INJECT | REPORT  (with t_ms when JSX panel >= Slot 17)
    jsx.pump: pump.setup.start/end + setup_ms
    jsx.pump: pump.chunk.start/end + setvalue_ms, purge_gc_ms, tick_total_ms
    jsx.pump: pump.audit.start/end + audit_ms, finalize_ms

  Inject dispatch (launcher):
    "Job dispatched" + dispatch_ms

Usage (from repo root):
    python3 python/scripts/slot_17_analyse_ae_log.py [path-to-dimension.log]

Defaults to ./logs/dimension.log. Prints the latest detected conform
round-trip and (if found) the latest scrape round-trip. Pass --json
for a machine-readable summary.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def parse_lines(log_path: Path) -> list[dict]:
    """Read dimension.log; return list of parsed JSON records (skip
    unparseable lines)."""
    out: list[dict] = []
    if not log_path.is_file():
        return out
    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def latest_bridge_round_trips(records: list[dict]) -> list[dict]:
    """Group bridge.<type>.* phase.end records by their dispatch_ms
    where present. Returns most-recent-first list of dicts:
        {job_type, dispatch_ms, claim_ms, started_ms, finished_ms,
         local_read_ms, poll_dead_ms, jsx_dispatch_ms, jsx_work_ms,
         python_read_lag_ms, wall_total_ms}
    Skips incomplete groups.
    """
    by_dispatch: dict[int, dict] = {}
    for r in records:
        phase = r.get("phase")
        if not isinstance(phase, str) or not phase.startswith("bridge."):
            continue
        parts = phase.split(".", 2)
        if len(parts) < 3:
            continue
        _, job_type, leaf = parts[0], parts[1], parts[2]
        d_ms = r.get("dispatch_ms")
        if not isinstance(d_ms, (int, float)):
            # Some phase.end records don't carry dispatch_ms (only the
            # ones that anchor on it do). Fall back to the latest
            # dispatch we've seen for the same job_type.
            d_ms = None
            for prior in reversed(records):
                if (
                    prior.get("phase", "").startswith(f"bridge.{job_type}.")
                    and isinstance(prior.get("dispatch_ms"), (int, float))
                ):
                    d_ms = int(prior["dispatch_ms"])
                    break
            if d_ms is None:
                continue
        d_ms = int(d_ms)
        bucket = by_dispatch.setdefault(d_ms, {
            "job_type": job_type,
            "dispatch_ms": d_ms,
        })
        bucket[f"{leaf}_ms"] = r.get("elapsed_ms")
        for k in ("claim_ms", "started_ms", "finished_ms", "local_read_ms"):
            if k in r:
                bucket[k] = r[k]
    return sorted(by_dispatch.values(), key=lambda b: -b["dispatch_ms"])


def latest_inject_run(records: list[dict]) -> Optional[dict]:
    """Find the most recent inject by walking backward from a
    "Conform complete" line. Collects:
      - dispatch_ms (from latest 'Job dispatched' before complete)
      - INJECT phase first-seen t_ms, REPORT phase t_ms, complete t_ms
      - all pump.* events with their ms breakdowns
      - audit_ms derived from t_ms deltas (pump.audit.start → complete)

    Slot 17 — pump.audit.end was dropped from Babysitter to preserve
    the status:COMPLETE last-line invariant; audit_ms now comes from
    `complete_t_ms - pump.audit.start.t_ms` instead of a dedicated
    marker.
    """
    # Anchor on the MOST RECENT `Job dispatched`, then look for a
    # matching `Conform complete` (or `pipeline.inject.error`) at a
    # higher index. If no complete fired after the latest dispatch
    # (mid-flight, or the COMPLETE-line-shadowing bug pre-Slot-17 fix),
    # walk to end-of-log so the pump events emitted AFTER dispatch
    # are still captured.
    #
    # Earlier versions anchored on the latest complete unconditionally,
    # which made the analyser stick to old runs when a fresh inject
    # hadn't yet emitted its complete line.
    jd_idx = -1
    for i in range(len(records) - 1, -1, -1):
        if records[i].get("msg") == "Job dispatched":
            jd_idx = i
            break
    if jd_idx < 0:
        return None

    last_complete_idx = jd_idx
    end_idx = len(records)
    for i in range(jd_idx + 1, len(records)):
        msg = records[i].get("msg", "")
        if msg.startswith("Conform complete") or msg == "pipeline.inject.error":
            last_complete_idx = i
            end_idx = i + 1
            break

    dispatch_ms: Optional[int] = None
    inject_t_ms: Optional[int] = None
    report_t_ms: Optional[int] = None
    pump_events: list[dict] = []
    complete_t_ms: Optional[int] = None
    pump_audit_start_t_ms: Optional[int] = None

    # Look backward for the matching "Job dispatched" (latest one
    # at or before the anchor).
    for i in range(last_complete_idx, -1, -1):
        r = records[i]
        if r.get("msg") == "Job dispatched":
            v = r.get("dispatch_ms")
            if isinstance(v, (int, float)):
                dispatch_ms = int(v)
            break
        if r.get("msg") == "Conform launch initiated":
            # Hit the launch but no dispatch_ms field (legacy log). Give up.
            break

    # Walk forward from dispatch up to the anchor (or end-of-log when
    # falling back). Bounded by ±500 records so unrelated activity
    # doesn't pollute the per-run table.
    walk_start = max(0, last_complete_idx - 500)
    for i in range(walk_start, min(end_idx, last_complete_idx + 500)):
        r = records[i]
        msg = r.get("msg")
        if msg == "pipeline.phase":
            phase = r.get("phase")
            t = r.get("t_ms")
            if phase == "INJECT" and inject_t_ms is None and isinstance(t, (int, float)):
                inject_t_ms = int(t)
            elif phase == "REPORT" and isinstance(t, (int, float)):
                report_t_ms = int(t)
        elif msg == "jsx.pump":
            event = r.get("event")
            pump_events.append({
                "event": event,
                "t_ms": r.get("t_ms"),
                "chunk_index": r.get("chunk_index"),
                "layer_count": r.get("layer_count"),
                "setup_ms": r.get("setup_ms"),
                "setvalue_ms": r.get("setvalue_ms"),
                "purge_gc_ms": r.get("purge_gc_ms"),
                "tick_total_ms": r.get("tick_total_ms"),
            })
            if event == "pump.audit.start" and isinstance(r.get("t_ms"), (int, float)):
                pump_audit_start_t_ms = int(r["t_ms"])
        elif msg and msg.startswith("Conform complete"):
            t = r.get("t_ms")
            if isinstance(t, (int, float)):
                complete_t_ms = int(t)

    # Slot 17 — derive audit_ms from t_ms deltas. pump.audit.start is
    # emitted right before phase:REPORT; status:COMPLETE is the last
    # line of the audit tick. The delta covers performAudit + the
    # writeLog overhead (sub-ms) but not finalizeWorkspace.
    audit_ms: Optional[int] = None
    if pump_audit_start_t_ms is not None and complete_t_ms is not None:
        audit_ms = complete_t_ms - pump_audit_start_t_ms

    return {
        "dispatch_ms": dispatch_ms,
        "inject_t_ms": inject_t_ms,
        "report_t_ms": report_t_ms,
        "complete_t_ms": complete_t_ms,
        "pump_audit_start_t_ms": pump_audit_start_t_ms,
        "derived_audit_ms": audit_ms,
        "pump_events": pump_events,
    }


def render_bridge_table(rt: dict) -> str:
    lines = [
        f"=== Bridge round-trip — {rt.get('job_type','?')} ===",
        f"  dispatch_ms      = {rt.get('dispatch_ms')}",
    ]
    for label, key in [
        ("poll_dead_ms", "poll_dead_ms"),
        ("jsx_dispatch_ms", "jsx_dispatch_ms"),
        ("jsx_work_ms", "jsx_work_ms"),
        ("python_read_lag_ms", "python_read_lag_ms"),
        ("wall_total_ms", "wall_total_ms"),
    ]:
        v = rt.get(key)
        if v is not None:
            lines.append(f"  {label:<20}= {v:>10.1f} ms")
    return "\n".join(lines)


def render_inject_table(run: dict) -> str:
    lines = ["=== Inject round-trip ==="]
    d = run.get("dispatch_ms")
    inj = run.get("inject_t_ms")
    rep = run.get("report_t_ms")
    comp = run.get("complete_t_ms")
    if d is not None and inj is not None:
        lines.append(f"  dispatch → INJECT     = {inj - d:>8} ms  (poll claim + setup ticks)")
    if inj is not None and rep is not None:
        lines.append(f"  INJECT → REPORT       = {rep - inj:>8} ms  (setupWorkspace + chunk pump)")
    if rep is not None and comp is not None:
        lines.append(f"  REPORT → COMPLETE     = {comp - rep:>8} ms  (audit + finalize)")
    if d is not None and comp is not None:
        lines.append(f"  dispatch → COMPLETE   = {comp - d:>8} ms  (WALL)")

    setup_total = 0.0
    chunk_total_setvalue = 0.0
    chunk_total_purge = 0.0
    chunk_total_tick = 0.0
    chunks_seen = 0
    gap_total = 0.0
    for ev in run.get("pump_events", []):
        if ev["event"] == "pump.setup.end" and ev.get("setup_ms"):
            setup_total += ev["setup_ms"]
        if ev["event"] == "pump.chunk.end":
            if ev.get("setvalue_ms"):
                chunk_total_setvalue += ev["setvalue_ms"]
            if ev.get("purge_gc_ms"):
                chunk_total_purge += ev["purge_gc_ms"]
            if ev.get("tick_total_ms"):
                chunk_total_tick += ev["tick_total_ms"]
            chunks_seen += 1
    audit_derived = run.get("derived_audit_ms")

    # Per-chunk gap between end[i] and start[i+1] (scheduleTask dead time).
    chunk_ends = [
        e for e in run["pump_events"]
        if e["event"] == "pump.chunk.end" and isinstance(e.get("t_ms"), (int, float))
    ]
    chunk_starts = [
        e for e in run["pump_events"]
        if e["event"] == "pump.chunk.start" and isinstance(e.get("t_ms"), (int, float))
    ]
    for i in range(min(len(chunk_ends), len(chunk_starts) - 1)):
        gap_total += int(chunk_starts[i + 1]["t_ms"] - chunk_ends[i]["t_ms"])

    if chunks_seen or run.get("pump_events"):
        lines.append("  ---- JSX-side detail ----")
        if setup_total:
            lines.append(f"  setupWorkspace        = {setup_total:>8.0f} ms  (one tick)")
        if chunks_seen:
            lines.append(f"  chunk setValue (sum)  = {chunk_total_setvalue:>8.0f} ms  ({chunks_seen} chunks)")
            lines.append(f"  chunk purge+gc (sum)  = {chunk_total_purge:>8.0f} ms")
            lines.append(f"  scheduleTask gaps     = {gap_total:>8.0f} ms  (between chunks)")
        if audit_derived is not None:
            lines.append(f"  audit (derived)       = {audit_derived:>8} ms  (performAudit + writeLog overhead)")
        jsx_known = setup_total + chunk_total_setvalue + chunk_total_purge + (audit_derived or 0)
        lines.append(f"  jsx work (known sum)  = {jsx_known:>8.0f} ms")
        if d is not None and comp is not None:
            wall = comp - d
            lines.append(f"  unaccounted vs wall   = {wall - jsx_known:>8.0f} ms  (poll claim + scheduleTask gaps + idle)")
    else:
        lines.append("  (no pump-tick data — JSX panel pre-Slot 17 instrumentation)")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Slot 17 — analyse AE log for bridge + JSX timings.")
    ap.add_argument("path", nargs="?", default="logs/dimension.log",
                    help="Path to dimension.log (default: ./logs/dimension.log)")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = ap.parse_args()

    log_path = Path(args.path)
    if not log_path.is_file():
        print(f"ERROR: log not found at {log_path}", file=sys.stderr)
        sys.exit(2)

    records = parse_lines(log_path)
    if not records:
        print("ERROR: log was empty or unparseable", file=sys.stderr)
        sys.exit(3)

    bridge_rts = latest_bridge_round_trips(records)
    inject_run = latest_inject_run(records)

    if args.json:
        out = {
            "log": str(log_path),
            "record_count": len(records),
            "bridge_round_trips": bridge_rts[:5],  # most recent 5
            "latest_inject": inject_run,
        }
        print(json.dumps(out, indent=2, default=str))
        return

    print("# Slot 17 — AE log analysis")
    print(f"  source: {log_path}")
    print(f"  records: {len(records)}")
    print()
    if not bridge_rts:
        print("(no bridge handshake markers found — JSX panel pre-Slot-17 or no recent round-trip)")
    else:
        for rt in bridge_rts[:3]:
            print(render_bridge_table(rt))
            print()
    if inject_run:
        print(render_inject_table(inject_run))
    else:
        print("(no recent inject — no `Conform complete` line found)")


if __name__ == "__main__":
    main()
