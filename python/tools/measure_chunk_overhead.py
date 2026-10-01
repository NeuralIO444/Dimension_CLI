# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Chunk-overhead measurement driver (RECOMMENDATIONS M3).

The Babysitter chunk size (default 30, DIMENSION_CHUNK_SIZE env,
read by python/logic/exporter.py::_resolve_chunk_size) was tuned
empirically years ago; re-tuning requires real measurements on a
200+ layer comp in live AE. This tool ships the driver + parser now
so the next field session only has to click EXECUTE a few times.

Two modes:

  --parse-only <transfer_status.log>
      Pure parser, no AE needed. Segments the log into inject runs
      (pump.setup.start → status COMPLETE/FAILED) and reports per run:
      setup ms, sum chunk ms, audit ms, wall total. The audit_ms
      derivation (complete.t_ms − pump.audit.start.t_ms) matches
      python/scripts/slot_17_analyse_ae_log.py — Babysitter emits no
      pump.audit.end to preserve the status:COMPLETE last-line
      invariant.

  driver mode (default; requires AE)
      Given --sizes 10,20,30,50 (default) it walks the operator
      through ONE conform+inject per size with
      DIMENSION_CHUNK_SIZE=<n> exported, snapshotting
      transfer_status.log between runs and parsing only each run's
      newly appended telemetry. THIS TOOL DOES NOT AUTOMATE AE — the
      operator clicks EXECUTE in the CEP panel per size and presses
      Enter here when the inject COMPLETEs. The env must reach the
      engine process: either run the conform CLI from a shell with the
      env exported, or relaunch AE itself from such a shell (CEP-
      spawned engine processes inherit AE's environment).

This tool never changes the chunk default and never touches
Babysitter. It only reads telemetry Babysitter already writes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "python"))

from tools.qa_common import parse_dimension_log_line  # noqa: E402


DEFAULT_SIZES = [10, 20, 30, 50]


def parse_transfer_runs(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Segment raw transfer_status.log lines into per-inject runs.

    A run opens at `pump.setup.start` and closes at the next
    `status: COMPLETE|FAILED` line. Telemetry outside a run (partial
    tail, aborted pumps without a status line) is dropped.
    """
    runs: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None

    for line in lines:
        rec = parse_dimension_log_line(line)
        if not rec:
            continue

        if rec.get("event") == "pump.setup.start":
            # A fresh setup.start while a run is open means the prior
            # run never completed (crash/abort) — discard it.
            current = {"events": [rec], "setup_start_t_ms": rec.get("t_ms")}
            continue

        if current is None:
            continue

        current["events"].append(rec)
        status = rec.get("status")
        if status in ("COMPLETE", "FAILED"):
            current["status_line"] = rec
            runs.append(current)
            current = None

    return runs


def summarize_run(run: dict[str, Any]) -> dict[str, Any]:
    """Reduce one run's telemetry to the M3 headline numbers."""
    setup_ms: Optional[float] = None
    chunk_tick_ms_sum = 0.0
    setvalue_ms_sum = 0.0
    purge_gc_ms_sum = 0.0
    chunks = 0
    audit_start_t_ms: Optional[float] = None

    for ev in run["events"]:
        event = ev.get("event")
        if event == "pump.setup.end" and isinstance(ev.get("setup_ms"), (int, float)):
            setup_ms = float(ev["setup_ms"])
        elif event == "pump.chunk.end":
            chunks += 1
            for key, acc in (
                ("tick_total_ms", "tick"),
                ("setvalue_ms", "setvalue"),
                ("purge_gc_ms", "purge"),
            ):
                v = ev.get(key)
                if isinstance(v, (int, float)):
                    if acc == "tick":
                        chunk_tick_ms_sum += v
                    elif acc == "setvalue":
                        setvalue_ms_sum += v
                    else:
                        purge_gc_ms_sum += v
        elif event == "pump.audit.start" and isinstance(ev.get("t_ms"), (int, float)):
            audit_start_t_ms = float(ev["t_ms"])

    status_line = run.get("status_line") or {}
    complete_t_ms = status_line.get("t_ms")
    setup_start_t_ms = run.get("setup_start_t_ms")

    audit_ms: Optional[float] = None
    if audit_start_t_ms is not None and isinstance(complete_t_ms, (int, float)):
        audit_ms = float(complete_t_ms) - audit_start_t_ms

    wall_ms: Optional[float] = None
    if isinstance(setup_start_t_ms, (int, float)) and isinstance(
        complete_t_ms, (int, float)
    ):
        wall_ms = float(complete_t_ms) - float(setup_start_t_ms)

    return {
        "status": status_line.get("status"),
        "audit_pass": status_line.get("auditPass"),
        "layer_count": status_line.get("layerCount"),
        "chunks": chunks,
        "setup_ms": setup_ms,
        "chunk_ms_sum": chunk_tick_ms_sum,
        "setvalue_ms_sum": setvalue_ms_sum,
        "purge_gc_ms_sum": purge_gc_ms_sum,
        "audit_ms": audit_ms,
        "wall_ms": wall_ms,
        "ts": status_line.get("ts"),
    }


def _fmt_ms(v: Optional[float]) -> str:
    return f"{v:,.0f}" if isinstance(v, (int, float)) else "—"


def render_table(rows: list[dict[str, Any]]) -> str:
    """Rows = summaries, each optionally carrying "chunk_size"."""
    header = (
        f"{'size':>5} | {'layers':>6} | {'chunks':>6} | {'setup ms':>9} | "
        f"{'sum chunk ms':>12} | {'audit ms':>8} | {'wall ms':>9} | status"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{str(row.get('chunk_size', '?')):>5} | "
            f"{str(row.get('layer_count', '—')):>6} | "
            f"{row['chunks']:>6} | "
            f"{_fmt_ms(row.get('setup_ms')):>9} | "
            f"{_fmt_ms(row.get('chunk_ms_sum')):>12} | "
            f"{_fmt_ms(row.get('audit_ms')):>8} | "
            f"{_fmt_ms(row.get('wall_ms')):>9} | "
            f"{row.get('status') or '—'}"
        )
    return "\n".join(lines)


def run_parse_only(log_path: Path, *, as_json: bool) -> int:
    if not log_path.is_file():
        print(f"ERROR: log not found at {log_path}", file=sys.stderr)
        return 2
    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        runs = parse_transfer_runs(fh)
    summaries = [summarize_run(r) for r in runs]
    if as_json:
        print(json.dumps({"log": str(log_path), "runs": summaries}, indent=2))
        return 0
    if not summaries:
        print("No complete inject runs found in log.")
        return 0
    print(f"# {len(summaries)} inject run(s) in {log_path}")
    print(render_table(summaries))
    return 0


def run_driver(repo: Path, sizes: list[int], *, as_json: bool) -> int:
    """Interactive per-size measurement loop. Requires live AE.

    Does NOT automate AE: the operator runs one conform+inject per
    size (EXECUTE in the CEP panel) with DIMENSION_CHUNK_SIZE
    exported so it reaches the engine process.
    """
    transfer = repo / "transfer_status.log"
    print("Chunk-overhead measurement driver (M3)")
    print("──────────────────────────────────────")
    print(f"Sizes to measure: {sizes}")
    print()
    print("For EACH size below you will run ONE conform+inject of the")
    print("SAME comp and target. The env var must reach the engine")
    print("process — either run the conform CLI from a shell with the")
    print("env exported, or relaunch AE from such a shell (CEP-spawned")
    print("engine processes inherit AE's environment).")
    print()

    results: list[dict[str, Any]] = []
    for size in sizes:
        offset = transfer.stat().st_size if transfer.is_file() else 0
        print(f"── size {size} " + "─" * 40)
        print(f"  1. export DIMENSION_CHUNK_SIZE={size}")
        print("  2. Run the conform (EXECUTE in the CEP panel, or CLI)")
        print("  3. Wait for the inject to COMPLETE in AE")
        try:
            input("  Press Enter here once COMPLETE (Ctrl-C to abort)… ")
        except (KeyboardInterrupt, EOFError):
            print("\nAborted by operator.")
            break

        if not transfer.is_file():
            print("  ✗ transfer_status.log not found — was the inject run?")
            continue
        with transfer.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            runs = parse_transfer_runs(fh)
        if not runs:
            print("  ✗ no completed inject run appended since last snapshot")
            continue
        summary = summarize_run(runs[-1])
        summary["chunk_size"] = size
        results.append(summary)
        print(f"  ✓ captured: wall {_fmt_ms(summary['wall_ms'])} ms, "
              f"{summary['chunks']} chunks")
        print()

    if not results:
        print("No measurements captured.")
        return 1
    print()
    print("── Results " + "─" * 44)
    if as_json:
        print(json.dumps({"results": results}, indent=2))
    else:
        print(render_table(results))
    print()
    print("NOTE: this tool never changes the chunk default (30). Take")
    print("these numbers to a review before touching DIMENSION_CHUNK_SIZE.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure Babysitter inject overhead per chunk size "
        "(RECOMMENDATIONS M3).",
        epilog="Driver mode does NOT automate AE — the operator clicks "
        "EXECUTE in the CEP panel once per size and confirms here after "
        "each COMPLETE. --parse-only needs no AE at all.",
    )
    parser.add_argument("--repo", default=str(REPO_ROOT), help="Dimension repo root")
    parser.add_argument(
        "--sizes",
        default=",".join(str(s) for s in DEFAULT_SIZES),
        help="Comma-separated chunk sizes to measure (default: 10,20,30,50)",
    )
    parser.add_argument(
        "--parse-only",
        metavar="LOG",
        help="Parse an existing transfer_status.log and report per-run "
        "numbers; no AE required",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON")
    args = parser.parse_args(argv)

    if args.parse_only:
        return run_parse_only(Path(args.parse_only), as_json=args.json)

    try:
        sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    except ValueError:
        parser.error(f"--sizes must be comma-separated integers (got {args.sizes!r})")
    if not sizes or any(s < 1 or s > 200 for s in sizes):
        parser.error("--sizes values must be in 1..200 (exporter clamp range)")

    return run_driver(Path(args.repo).resolve(), sizes, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
