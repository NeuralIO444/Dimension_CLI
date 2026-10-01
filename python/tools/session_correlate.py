# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Correlate a conform session_id across reports, dimension.log, and transfer_status.

Exit codes:
  0 — SUCCESS or WARNINGS (session found, inject completed)
  1 — FAILED or INCOMPLETE (session found but inject failed / missing)
  2 — NOT_FOUND
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "python"))

from pytest_dimension_ae.probes import probe_ae  # noqa: E402
from tools.qa_common import (  # noqa: E402
    ConformReportMatch,
    default_correlation_window,
    find_conform_reports_by_session,
    find_latest_session_id,
    pick_best_inject_run,
    scan_log_by_session,
    scan_log_window,
    scan_transfer_complete_runs,
    summarize_log_events,
)


VERDICT_SUCCESS = "SUCCESS"
VERDICT_WARNINGS = "WARNINGS"
VERDICT_FAILED = "FAILED"
VERDICT_INCOMPLETE = "INCOMPLETE"
VERDICT_NOT_FOUND = "NOT_FOUND"


def _find_duplication_log(repo: Path, session_id: str) -> Optional[Path]:
    candidates = [
        repo / ".dimension" / "duplication_log.json",
        repo / "duplication_log.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("session_id", "")).upper() == session_id.upper():
            return path
    return None


def _compute_verdict(
    log_summary: dict[str, Any],
    inject_run: Optional[dict[str, Any]],
    report: ConformReportMatch,
) -> str:
    if log_summary.get("inject_error"):
        return VERDICT_FAILED
    if inject_run is None:
        if log_summary.get("job_dispatched"):
            return VERDICT_INCOMPLETE
        return VERDICT_INCOMPLETE
    if inject_run.get("auditPass") is False:
        return VERDICT_FAILED
    has_warnings = bool(
        log_summary.get("warnings")
        or log_summary.get("scene_preserve_warnings")
        or report.meta.get("collapse_warnings", 0)
    )
    if has_warnings:
        return VERDICT_WARNINGS
    return VERDICT_SUCCESS


def correlate_session(
    repo: Path,
    session_id: str,
    *,
    log_path: Path,
    transfer_path: Path,
    before_minutes: int = 5,
    after_minutes: int = 15,
) -> dict[str, Any]:
    reports = find_conform_reports_by_session(repo, session_id)
    if not reports:
        return {
            "session_id": session_id.upper(),
            "verdict": VERDICT_NOT_FOUND,
            "reports": [],
        }

    # Prefer the report with the latest timestamp when multiple exist.
    report = max(
        reports,
        key=lambda r: r.timestamp or __import__("datetime").datetime.min,
    )
    report_ts = report.timestamp
    log_records: list[dict] = []
    inject_runs: list[dict] = []
    window: Optional[dict[str, str]] = None
    log_match = "timestamp_window"

    if report_ts is not None:
        start, end = default_correlation_window(
            report_ts,
            before_minutes=before_minutes,
            after_minutes=after_minutes,
        )
        window = {"start": start.isoformat(), "end": end.isoformat()}
        log_records = scan_log_window(log_path, start, end)
        inject_runs = scan_transfer_complete_runs(transfer_path, start, end)

    # M8 — prefer exact session_id matching for dimension.log when any
    # line carries the id (new-style logs stamp it on "Report written"
    # and, when producers supply it, "Bridge job dispatch"). Falls back
    # to the timestamp window above for old logs / absent field.
    # transfer_status.log stays timestamp-correlated — Babysitter lines
    # never carry a session id.
    sid_records = scan_log_by_session(log_path, session_id)
    if sid_records:
        log_records = sid_records
        log_match = "session_id"

    log_summary = summarize_log_events(log_records)
    layer_count = report.meta.get("total_layers")
    inject_run = pick_best_inject_run(
        inject_runs,
        layer_count=layer_count if isinstance(layer_count, int) else None,
    )
    verdict = _compute_verdict(log_summary, inject_run, report)
    dup_log = _find_duplication_log(repo, report.session_id)
    probe = probe_ae(repo)

    return {
        "session_id": report.session_id,
        "verdict": verdict,
        "report": {
            "json_path": str(report.json_path),
            "html_path": str(report.html_path) if report.html_path else None,
            "meta": report.meta,
        },
        "correlation_window": window,
        "log_match": log_match,
        "conform_log": log_summary,
        "inject": {
            "complete_runs_in_window": len(inject_runs),
            "best_match": inject_run,
        },
        "artifacts": {
            "duplication_log": str(dup_log) if dup_log else None,
            "chunk_manifest": str(repo / "chunk_manifest.json")
            if (repo / "chunk_manifest.json").is_file()
            else None,
            "soe_corrections": str(repo / "soe_corrections.json")
            if (repo / "soe_corrections.json").is_file()
            else None,
        },
        "ae_probe": probe.to_dict(),
    }


def render_human(payload: dict[str, Any]) -> str:
    lines = [
        "══════════════════════════════════════════════════════════════",
        "  Dimension session correlate",
        "══════════════════════════════════════════════════════════════",
        "",
        f"  Session:  {payload.get('session_id', '?')}",
        f"  Verdict:  {payload.get('verdict', '?')}",
    ]

    if payload.get("verdict") == VERDICT_NOT_FOUND:
        lines.append("")
        lines.append("  No conform_report__*.json with matching meta.session_id.")
        return "\n".join(lines)

    report = payload.get("report", {})
    meta = report.get("meta", {})
    lines.extend(
        [
            "",
            "  ── Conform report ──",
            f"  Source:     {meta.get('source_name', '?')}",
            f"  Target:     {meta.get('target_w')}×{meta.get('target_h')} "
            f"({meta.get('scale_mode')}, scale={meta.get('uniform_scale')})",
            f"  Timestamp:  {meta.get('timestamp', '?')}",
            f"  Layers:     {meta.get('total_layers', '?')} "
            f"({meta.get('total_chunks', '?')} chunks)",
            f"  JSON:       {report.get('json_path', '?')}",
        ]
    )
    if report.get("html_path"):
        lines.append(f"  HTML:       {report['html_path']}")

    window = payload.get("correlation_window")
    if window:
        lines.extend(
            [
                "",
                "  ── Log window ──",
                f"  {window.get('start')} → {window.get('end')}",
                f"  Log match:  {payload.get('log_match', 'timestamp_window')}",
            ]
        )

    clog = payload.get("conform_log", {})
    lines.extend(
        [
            "",
            "  ── Conform phase (dimension.log) ──",
            f"  Records:    {clog.get('record_count', 0)}",
            f"  SOE fixes:  {clog.get('soe_correction_count', 0)}",
            f"  Scene-preserve warnings: {clog.get('scene_preserve_warnings', 0)}",
        ]
    )
    for h in clog.get("highlights", [])[:6]:
        lines.append(f"    • {h}")
    for w in clog.get("warnings", [])[:4]:
        lines.append(f"    ⚠ {w}")
    for e in clog.get("errors", [])[:4]:
        lines.append(f"    ✗ {e}")

    inj = payload.get("inject", {})
    best = inj.get("best_match")
    lines.extend(
        [
            "",
            "  ── Inject phase (transfer_status.log) ──",
            f"  COMPLETE runs in window: {inj.get('complete_runs_in_window', 0)}",
        ]
    )
    if best:
        lines.extend(
            [
                f"  Status:     COMPLETE (auditPass={best.get('auditPass')})",
                f"  Layers:     {best.get('layerCount')}",
                f"  Mirrors:    {best.get('mirror_comp_count')}",
                f"  Timestamp:  {best.get('ts')}",
            ]
        )
    else:
        lines.append("  (no matching COMPLETE line in window)")

    probe = payload.get("ae_probe", {})
    lines.extend(
        [
            "",
            "  ── AE readiness (can re-run?) ──",
            f"  Ready:      {probe.get('ready')} — {probe.get('summary')}",
            f"  Inbox:      pending={probe.get('inbox_pending')} "
            f"claimed={probe.get('inbox_claimed')}",
        ]
    )

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Correlate a conform session across reports and pipeline logs.",
    )
    parser.add_argument(
        "session_id",
        nargs="?",
        default=None,
        help="8-char session id (e.g. C4E2EC2C); omit with --since-last",
    )
    parser.add_argument(
        "--since-last",
        action="store_true",
        help="Auto-pick the newest session id from the newest "
        "conform_report__*.json's meta instead of an explicit id",
    )
    parser.add_argument("--repo", default=str(REPO_ROOT), help="Dimension repo root")
    parser.add_argument(
        "--log",
        default="logs/dimension.log",
        help="Path to dimension.log",
    )
    parser.add_argument(
        "--transfer",
        default="transfer_status.log",
        help="Path to transfer_status.log",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON")
    parser.add_argument(
        "--before-min",
        type=int,
        default=5,
        help="Minutes before report timestamp to scan logs (default: 5)",
    )
    parser.add_argument(
        "--after-min",
        type=int,
        default=15,
        help="Minutes after report timestamp to scan logs (default: 15)",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()

    if args.since_last and args.session_id:
        parser.error("give either an explicit session_id or --since-last, not both")
    if not args.since_last and not args.session_id:
        parser.error("session_id is required (or pass --since-last)")

    session_id = args.session_id
    if args.since_last:
        session_id = find_latest_session_id(repo)
        if session_id is None:
            print(
                "No conform_report__*.json with a session_id found "
                f"under {repo} — nothing to correlate.",
                file=sys.stderr,
            )
            return 2

    log_path = Path(args.log)
    if not log_path.is_absolute():
        log_path = (repo / log_path).resolve()
    transfer_path = Path(args.transfer)
    if not transfer_path.is_absolute():
        transfer_path = (repo / transfer_path).resolve()

    payload = correlate_session(
        repo,
        session_id,
        log_path=log_path,
        transfer_path=transfer_path,
        before_minutes=args.before_min,
        after_minutes=args.after_min,
    )

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render_human(payload))

    verdict = payload.get("verdict")
    if verdict == VERDICT_NOT_FOUND:
        return 2
    if verdict in (VERDICT_FAILED, VERDICT_INCOMPLETE):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())