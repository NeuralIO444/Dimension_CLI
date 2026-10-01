# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""dimension_db.py

Issue #16 — the per-project SQLite provenance store.

One `.dimension/dimension.db` per project directory. It REPLACES
`duplication_log.json`: nothing in this codebase may create that JSON
file. (Babysitter's legacy writers still emit it; the writer lane
converts them to `record_creation` / `record_run`. Until then,
`import_legacy_duplication_log` performs a one-time import of a legacy
log into a fresh database.)

Tables:
  meta      — single row; `schema_version` INTEGER. Migrations are
              versioned SQL in MIGRATIONS, applied in order.
  creations — the provenance-not-names store (issue #10): every comp
              Babysitter creates gets a row (name, source, session,
              timestamp, operation). The duplicate predicate queries
              this table — `known_duplicate_names`.
  runs      — conform run history (session, started_at, finished_at,
              status, detail JSON). The future TUI (issue #15) reads
              this; writers record it.

Stdlib `sqlite3` only — no new dependency. Target catalog stays YAML,
manifests and reports stay JSON; this DB is the only SQLite store.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DB_DIRNAME = ".dimension"
DB_FILENAME = "dimension.db"
LEGACY_LOG_FILENAME = "duplication_log.json"

# Versioned migrations: key = schema_version the migration PRODUCES.
# Applied in ascending order for any version newer than the current
# one recorded in meta.schema_version. Each script must be idempotent
# enough to run exactly once per database.
MIGRATIONS = {
    1: """
CREATE TABLE IF NOT EXISTS meta (
    schema_version INTEGER NOT NULL
);
INSERT INTO meta (schema_version)
    SELECT 0 WHERE NOT EXISTS (SELECT 1 FROM meta);
CREATE TABLE IF NOT EXISTS creations (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    source    TEXT NOT NULL DEFAULT '',
    session   TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL,
    operation TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_creations_name ON creations (name);
CREATE INDEX IF NOT EXISTS idx_creations_session ON creations (session);
CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY,
    session    TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status     TEXT NOT NULL DEFAULT 'unknown',
    detail     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs (session);
UPDATE meta SET schema_version = 1;
""",
}


def db_path_for(project_dir: str) -> str:
    """Path of the project database: `<project_dir>/.dimension/dimension.db`."""
    return os.path.join(project_dir, DB_DIRNAME, DB_FILENAME)


def legacy_log_path_for(project_dir: str) -> str:
    """Path of the legacy Babysitter log this DB replaces."""
    return os.path.join(project_dir, DB_DIRNAME, LEGACY_LOG_FILENAME)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_version(conn: sqlite3.Connection) -> int:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if not has_meta:
        return 0
    row = conn.execute("SELECT schema_version FROM meta").fetchone()
    return int(row[0]) if row else 0


def open_project_db(project_dir: str) -> sqlite3.Connection:
    """Open (creating if needed) the project database and apply any
    pending versioned migrations. The caller owns the connection and
    must close it. This is the WRITER entry point — read-only callers
    should use `load_known_duplicate_names`-style helpers that never
    create files."""
    dim_dir = os.path.join(project_dir, DB_DIRNAME)
    os.makedirs(dim_dir, exist_ok=True)
    conn = sqlite3.connect(db_path_for(project_dir))
    try:
        current = _current_version(conn)
        for version in sorted(MIGRATIONS):
            if version > current:
                conn.executescript(MIGRATIONS[version])
        conn.commit()
    except Exception:
        conn.close()
        raise
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    """Current schema version recorded in meta."""
    return _current_version(conn)


def record_creation(
    conn: sqlite3.Connection,
    *,
    name: str,
    source: str = "",
    session: str = "",
    operation: str = "",
    timestamp: Optional[str] = None,
) -> int:
    """Log one comp Babysitter created. Returns the new row id."""
    cur = conn.execute(
        "INSERT INTO creations (name, source, session, timestamp, operation)"
        " VALUES (?, ?, ?, ?, ?)",
        (name, source, session, timestamp or _utcnow(), operation),
    )
    conn.commit()
    return int(cur.lastrowid)


def known_duplicate_names(conn: sqlite3.Connection) -> set:
    """The duplicate predicate's query (issue #10): the DISTINCT names
    Babysitter provably created. Empty set when nothing was recorded —
    absence of provenance must never invent candidates."""
    rows = conn.execute(
        "SELECT DISTINCT name FROM creations"
        " WHERE name IS NOT NULL AND name != ''"
    ).fetchall()
    return {str(r[0]) for r in rows}


def record_run(
    conn: sqlite3.Connection,
    *,
    session: str,
    status: str = "unknown",
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> int:
    """Append one conform run to the run history the TUI (issue #15)
    will read. `detail` is stored as a JSON string — manifests and
    reports stay JSON; the DB just carries the blob."""
    cur = conn.execute(
        "INSERT INTO runs (session, started_at, finished_at, status, detail)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            session,
            started_at or _utcnow(),
            finished_at,
            status,
            json.dumps(detail or {}),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_runs(
    conn: sqlite3.Connection,
    *,
    session: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Conform run history, newest first. `detail` is parsed back from JSON."""
    if session is not None:
        rows = conn.execute(
            "SELECT id, session, started_at, finished_at, status, detail"
            " FROM runs WHERE session = ? ORDER BY id DESC LIMIT ?",
            (session, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, session, started_at, finished_at, status, detail"
            " FROM runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    runs = []
    for row in rows:
        try:
            detail = json.loads(row[5]) if row[5] else {}
        except ValueError:
            detail = {}
        runs.append({
            "id": row[0],
            "session": row[1],
            "started_at": row[2],
            "finished_at": row[3],
            "status": row[4],
            "detail": detail,
        })
    return runs


def import_legacy_duplication_log(
    conn: sqlite3.Connection,
    json_path: str,
    *,
    default_session: str = "",
) -> int:
    """One-time import of a legacy `.dimension/duplication_log.json`
    into `creations`. Returns the number of rows imported. Missing or
    unreadable files import nothing. Never writes JSON — this only
    READS the legacy file the DB replaces."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    entries = payload.get("duplicates_made") or []
    if not isinstance(entries, list):
        return 0
    imported = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("duplicate_name") or "").strip()
        if not name:
            continue
        record_creation(
            conn,
            name=name,
            source=str(entry.get("original_name") or ""),
            session=str(payload.get("session_id") or default_session),
            operation="legacy_import",
        )
        imported += 1
    return imported
