# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Shared helpers for Dimension QA operator tools."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional


REPORT_TS_FMT = "%Y-%m-%d %H:%M:%S"
TRANSFER_TS_RE = re.compile(
    r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2}\s+GMT[+-]\d{4}$"
)


@dataclass
class ConformReportMatch:
    session_id: str
    json_path: Path
    html_path: Optional[Path]
    meta: dict[str, Any]

    @property
    def timestamp(self) -> Optional[datetime]:
        raw = self.meta.get("timestamp")
        if not isinstance(raw, str):
            return None
        try:
            return datetime.strptime(raw, REPORT_TS_FMT)
        except ValueError:
            return None


@dataclass
class InboxJobInfo:
    path: Path
    age_days: float
    mtime_iso: str
    job_type: Optional[str] = None


@dataclass
class OrphanScan:
    pending: list[InboxJobInfo] = field(default_factory=list)
    claimed: list[InboxJobInfo] = field(default_factory=list)
    stale_threshold_days: float = 7.0

    @property
    def stale_pending(self) -> list[InboxJobInfo]:
        return [j for j in self.pending if j.age_days >= self.stale_threshold_days]

    @property
    def stale_claimed(self) -> list[InboxJobInfo]:
        return [j for j in self.claimed if j.age_days >= self.stale_threshold_days]

    @property
    def has_stale(self) -> bool:
        return bool(self.stale_pending or self.stale_claimed)


def normalize_session_id(session_id: str) -> str:
    return session_id.strip().upper()


def _report_globs(repo: Path) -> list[Path]:
    roots = [repo, repo / "archive" / "old-conform-reports"]
    paths: list[Path] = []
    for root in roots:
        if root.is_dir():
            paths.extend(sorted(root.glob("conform_report__*.json")))
    return paths


def find_conform_reports_by_session(
    repo: Path,
    session_id: str,
) -> list[ConformReportMatch]:
    """Return all conform reports whose meta.session_id matches."""
    sid = normalize_session_id(session_id)
    matches: list[ConformReportMatch] = []
    for path in _report_globs(repo):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            continue
        if normalize_session_id(str(meta.get("session_id", ""))) != sid:
            continue
        html = path.with_suffix(".html")
        matches.append(
            ConformReportMatch(
                session_id=sid,
                json_path=path,
                html_path=html if html.is_file() else None,
                meta=meta,
            )
        )
    return matches


def find_latest_session_id(repo: Path) -> Optional[str]:
    """Session id from the newest (mtime) conform_report__*.json meta.

    Backs `check_session --since-last` — repo-root reports are LIVE
    artifacts rewritten by every conform run, so the newest one's
    session id is "the last session". Returns None when no report
    carries a session_id.
    """
    paths = sorted(
        _report_globs(repo),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            continue
        sid = meta.get("session_id")
        if isinstance(sid, str) and sid.strip():
            return normalize_session_id(sid)
    return None


def parse_dimension_log_line(line: str) -> Optional[dict[str, Any]]:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def parse_log_timestamp(ts: str) -> Optional[datetime]:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def parse_transfer_timestamp(ts: str) -> Optional[datetime]:
    """Parse Babysitter ts like 'Sun Jul 05 2026 17:38:31 GMT-0700'."""
    if not TRANSFER_TS_RE.match(ts):
        return None
    try:
        return datetime.strptime(ts, "%a %b %d %Y %H:%M:%S GMT%z")
    except ValueError:
        return None


def scan_log_window(
    log_path: Path,
    start: datetime,
    end: datetime,
    *,
    max_lines: int = 50_000,
) -> list[dict[str, Any]]:
    """Return dimension.log records whose ts falls in [start, end]."""
    if not log_path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i >= max_lines:
                break
            rec = parse_dimension_log_line(line)
            if not rec:
                continue
            ts_raw = rec.get("ts")
            if not isinstance(ts_raw, str):
                continue
            ts = parse_log_timestamp(ts_raw)
            if ts is None:
                continue
            if start <= ts <= end:
                out.append(rec)
    return out


def scan_log_by_session(
    log_path: Path,
    session_id: str,
    *,
    max_lines: int = 50_000,
) -> list[dict[str, Any]]:
    """Return dimension.log records whose `session_id` field matches.

    New-style log lines (M8, 2026-07-06) carry session_id in their
    extra dict; matching on it is exact where the timestamp window is
    heuristic. Old logs without the field simply return [] — callers
    fall back to `scan_log_window`.
    """
    if not log_path.is_file():
        return []
    sid = normalize_session_id(session_id)
    out: list[dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i >= max_lines:
                break
            rec = parse_dimension_log_line(line)
            if not rec:
                continue
            rec_sid = rec.get("session_id")
            if isinstance(rec_sid, str) and normalize_session_id(rec_sid) == sid:
                out.append(rec)
    return out


def scan_transfer_complete_runs(
    transfer_path: Path,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Return COMPLETE status lines from transfer_status.log in time window."""
    if not transfer_path.is_file():
        return []
    runs: list[dict[str, Any]] = []
    for line in transfer_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("status") != "COMPLETE":
            continue
        ts_raw = rec.get("ts")
        if not isinstance(ts_raw, str):
            continue
        ts = parse_transfer_timestamp(ts_raw)
        if ts is None:
            continue
        # Normalize to naive for comparison with dimension.log timestamps.
        ts_naive = ts.replace(tzinfo=None)
        if start <= ts_naive <= end:
            runs.append(rec)
    return runs


def pick_best_inject_run(
    runs: list[dict[str, Any]],
    *,
    layer_count: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Pick the inject COMPLETE run that best matches the conform report."""
    if not runs:
        return None
    if layer_count is not None:
        exact = [r for r in runs if r.get("layerCount") == layer_count]
        if exact:
            return exact[-1]
    return runs[-1]


def summarize_log_events(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract high-signal conform/inject events from a log window."""
    warnings: list[str] = []
    errors: list[str] = []
    highlights: list[str] = []
    report_paths: list[str] = []
    inject_error = False
    conform_complete = False
    job_dispatched = False
    soe_corrections = 0
    scene_preserve_warnings = 0

    for rec in records:
        level = rec.get("level", "")
        msg = rec.get("msg", "")
        if level == "WARNING":
            warnings.append(msg)
        elif level == "ERROR":
            errors.append(msg)
        if msg == "Report written":
            path = rec.get("path")
            if isinstance(path, str):
                report_paths.append(path)
            highlights.append(f"Report written @ {rec.get('ts')}")
        elif msg == "Job dispatched":
            job_dispatched = True
            highlights.append(f"Job dispatched @ {rec.get('ts')}")
        elif isinstance(msg, str) and msg.startswith("Conform complete"):
            conform_complete = True
            highlights.append(f"{msg} @ {rec.get('ts')}")
        elif msg == "pipeline.inject.error":
            inject_error = True
            errors.append(f"pipeline.inject.error @ {rec.get('ts')}")
        elif msg == "SOE pass complete" and rec.get("module") == "orchestrator":
            corr = rec.get("correction_count")
            if isinstance(corr, int):
                soe_corrections = corr
            spw = rec.get("scene_preserve_warnings")
            if isinstance(spw, int):
                scene_preserve_warnings = spw
        elif msg == "Scene-preserve: sealed nested comps":
            highlights.append(
                f"Scene-preserve sealed comps: {rec.get('sealed_comp_count')}"
            )

    return {
        "warnings": warnings,
        "errors": errors,
        "highlights": highlights,
        "report_paths": report_paths,
        "job_dispatched": job_dispatched,
        "conform_complete": conform_complete,
        "inject_error": inject_error,
        "soe_correction_count": soe_corrections,
        "scene_preserve_warnings": scene_preserve_warnings,
        "record_count": len(records),
    }


def scan_inbox_orphans(
    repo: Path,
    *,
    stale_days: float = 7.0,
) -> OrphanScan:
    """List pending/claimed inbox jobs and flag stale ones."""
    import time

    inbox = repo / ".dimension_inbox"
    now = time.time()
    scan = OrphanScan(stale_threshold_days=stale_days)

    def _collect(folder: Path, bucket: list[InboxJobInfo]) -> None:
        if not folder.is_dir():
            return
        for path in sorted(folder.glob("job_*.json")):
            age_s = now - path.stat().st_mtime
            job_type: Optional[str] = None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload.get("type"), str):
                    job_type = payload["type"]
            except (OSError, json.JSONDecodeError):
                pass
            bucket.append(
                InboxJobInfo(
                    path=path,
                    age_days=round(age_s / 86400, 1),
                    mtime_iso=datetime.fromtimestamp(path.stat().st_mtime).isoformat(
                        timespec="seconds"
                    ),
                    job_type=job_type,
                )
            )

    _collect(inbox, scan.pending)
    _collect(inbox / "processing", scan.claimed)
    return scan


def default_correlation_window(
    report_ts: datetime,
    *,
    before_minutes: int = 5,
    after_minutes: int = 15,
) -> tuple[datetime, datetime]:
    return (
        report_ts - timedelta(minutes=before_minutes),
        report_ts + timedelta(minutes=after_minutes),
    )