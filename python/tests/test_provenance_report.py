# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial.

"""Issue #17: the report reads creations from the SQLite store."""

from core.dimension_db import open_project_db, record_creation
from logic.report_generator import _load_duplication_log


def test_report_reads_sqlite_when_json_log_is_absent(tmp_path):
    conn = open_project_db(str(tmp_path))
    record_creation(conn, name="Hero_tiktok_v1", source="hero.json",
                    session="sess-17", operation="conform")
    conn.close()
    payload = _load_duplication_log(tmp_path, expected_session_id="sess-17")
    assert payload is not None
    assert payload["source"] == "dimension.db"
    assert payload["creations"][0]["name"] == "Hero_tiktok_v1"
    assert not (tmp_path / ".dimension" / "duplication_log.json").exists()
