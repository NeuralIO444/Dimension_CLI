# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial.
# See LICENSE for full terms.

"""Provenance ops: query the per-project SQLite store (issue #16).

`.dimension/dimension.db` is the provenance-not-names store: every
comp Babysitter creates gets a row, and the duplicate predicate
queries it. These ops are read-only — the DB is never created here;
a missing DB simply contributes nothing. The one writer,
`import_legacy`, is an explicit one-time migration command.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any, Optional

from dimension.common import DimensionError
from core import dimension_db
from core.comp_cleaner import load_known_duplicate_names


def _resolve_db_path(
    db: Optional[str], project_dir: Optional[str]
) -> Optional[str]:
    if db:
        return db
    base = project_dir or os.getcwd()
    return dimension_db.db_path_for(os.path.abspath(base))


def duplicates_op(
    *,
    db: Optional[str] = None,
    project_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Names Babysitter provably created (the duplicate predicate)."""
    db_path = _resolve_db_path(db, project_dir)
    names = (
        load_known_duplicate_names(db_path=db_path)
        if db_path and os.path.exists(db_path)
        else set()
    )
    return {
        "status": "OK",
        "headless": True,
        "db": db_path,
        "db_exists": bool(db_path and os.path.exists(db_path)),
        "duplicate_count": len(names),
        "duplicates": sorted(names),
    }


def runs_op(
    *,
    db: Optional[str] = None,
    project_dir: Optional[str] = None,
    session: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Conform run history, newest first. Read-only (`mode=ro`)."""
    db_path = _resolve_db_path(db, project_dir)
    if not db_path or not os.path.exists(db_path):
        return {
            "status": "OK",
            "headless": True,
            "db": db_path,
            "db_exists": False,
            "run_count": 0,
            "runs": [],
        }
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise DimensionError(
            f"could not open project database: {e}", code="DB_UNREADABLE"
        ) from e
    try:
        runs = dimension_db.list_runs(conn, session=session, limit=limit)
    finally:
        conn.close()
    return {
        "status": "OK",
        "headless": True,
        "db": db_path,
        "db_exists": True,
        "run_count": len(runs),
        "runs": runs,
    }


def import_legacy_op(*, project_dir: str) -> dict[str, Any]:
    """One-time import of a legacy `.dimension/duplication_log.json`
    into a fresh project database. The only writer in this module —
    explicit, never implicit."""
    project_dir = os.path.abspath(project_dir)
    legacy_path = dimension_db.legacy_log_path_for(project_dir)
    if not os.path.isfile(legacy_path):
        raise DimensionError(
            f"no legacy duplication log at {legacy_path} — nothing to import",
            code="SOURCE_NOT_FOUND",
        )
    conn = dimension_db.open_project_db(project_dir)
    try:
        imported = dimension_db.import_legacy_duplication_log(
            conn, legacy_path
        )
    finally:
        conn.close()
    return {
        "status": "OK",
        "headless": True,
        "db": dimension_db.db_path_for(project_dir),
        "imported_rows": imported,
    }
