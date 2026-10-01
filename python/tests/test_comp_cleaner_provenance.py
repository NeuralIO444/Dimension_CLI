# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_comp_cleaner_provenance.py

Issue #10 / Dimension #551 regression tests — provenance, not names.

#551: DIMENSION_DUP_PATTERN matched none of the names
`output_naming.resolve_output_name` actually produces, so the CLEAN COMPS
report was inert on real projects. The regex is deleted; the predicate is
now membership in the provenance set (`.dimension/dimension.db`
creations table — issue #16, REPLACING duplication_log.json — unioned
with the manifest `duplication_plan`). These tests prove the names the
old regex missed are recognized by the new predicate.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.comp_cleaner import (
    CompCleaner,
    load_known_duplicate_names,
)
from core.dimension_db import (
    db_path_for,
    import_legacy_duplication_log,
    list_runs,
    open_project_db,
    record_creation,
    record_run,
    schema_version,
)
from core.output_naming import resolve_output_name_for_preset
from data.target_catalog import BUILTIN_TARGETS

# Fragments of the deleted DIMENSION_DUP_PATTERN — kept here only to
# document the #551 failure mode: none of the names below contain any of
# these, which is exactly why the regex report was inert.
_DEAD_MARKERS = ("__dim_dup_", "_dim_dup", "__dim_", "[CONFORM]", "[DIM_DUP]")


def _resolved_names_for_all_builtin_targets():
    """Every name resolve_output_name produces for the builtin catalog,
    plus the default `_vN` collision form."""
    names = []
    for target in BUILTIN_TARGETS:
        base = resolve_output_name_for_preset("Source_Comp", target, set())
        names.append(base.name)
        bumped = resolve_output_name_for_preset(
            "Source_Comp", target, {base.name})
        assert bumped.name != base.name
        assert bumped.name.endswith("_v2"), bumped.name
        names.append(bumped.name)
    return names


class TestIssue551Regression:
    def test_builtin_target_names_carry_no_dead_markers(self):
        """Documents the #551 bug: the old regex's markers appear in none
        of the names resolve_output_name actually produces."""
        for name in _resolved_names_for_all_builtin_targets():
            assert not any(m in name for m in _DEAD_MARKERS), name

    def test_provenance_predicate_recognizes_every_builtin_target_name(self):
        """The new predicate: every resolve_output_name product (plus the
        _vN collision form) is recognized when it is in the provenance
        set — the exact set the old regex missed."""
        names = _resolved_names_for_all_builtin_targets()
        assert len(names) == 2 * len(BUILTIN_TARGETS)

        project_struct = {
            "items": [
                {"id": i, "name": name, "type": "composition",
                 "width": 1920, "height": 1080, "duration": 10.0,
                 "layers": []}
                for i, name in enumerate(names, start=1)
            ]
        }
        plan = CompCleaner.analyze_project(
            project_struct, known_duplicates=set(names))
        found = {c.name for c in plan.orphaned_candidates}
        assert found == set(names), (
            f"{len(set(names)) - len(found)} provenance-logged names not recognized")

    def test_names_without_provenance_are_not_recognized(self):
        """Converse: a resolve_output_name product with no provenance
        record is not a candidate — names alone prove nothing."""
        name = resolve_output_name_for_preset(
            "Source_Comp", BUILTIN_TARGETS[0], set()).name
        project_struct = {
            "items": [
                {"id": 1, "name": name, "type": "composition",
                 "width": 1920, "height": 1080, "duration": 10.0,
                 "layers": []},
            ]
        }
        plan = CompCleaner.analyze_project(project_struct, known_duplicates=set())
        assert plan.total_orphans_count == 0


class TestProvenanceLoader:
    def _seed_db(self, project_dir):
        """One project DB with two recorded creations."""
        conn = open_project_db(str(project_dir))
        try:
            record_creation(conn, name="Hero_1080x1920", source="Hero",
                            session="sess-1", operation="duplicate")
            record_creation(conn, name="Title_1080x1920", source="Title",
                            session="sess-1", operation="duplicate")
        finally:
            conn.close()
        return db_path_for(str(project_dir))

    def test_db_creation_names_load(self, tmp_path):
        db = self._seed_db(tmp_path)
        names = load_known_duplicate_names(db_path=db)
        assert names == {"Hero_1080x1920", "Title_1080x1920"}

    def test_manifest_plan_names_union(self, tmp_path):
        manifest_path = tmp_path / "chunk_manifest.json"
        manifest_path.write_text(json.dumps({
            "duplication_plan": {
                "duplicates": [
                    {"original_name": "Bg", "duplicate_name": "Bg_1080x1920"},
                ],
            },
        }), encoding="utf-8")
        db = self._seed_db(tmp_path)

        names = load_known_duplicate_names(
            db_path=db, manifest_path=str(manifest_path))
        assert names == {"Hero_1080x1920", "Title_1080x1920", "Bg_1080x1920"}

    def test_missing_db_contributes_nothing(self, tmp_path):
        names = load_known_duplicate_names(
            db_path=str(tmp_path / ".dimension" / "dimension.db"),
            manifest_path=str(tmp_path / "nope.json"))
        assert names == set()

    def test_loader_never_creates_the_database(self, tmp_path):
        """The read path of a read-only report must not create files."""
        db = str(tmp_path / ".dimension" / "dimension.db")
        load_known_duplicate_names(db_path=db)
        assert not os.path.exists(db)

    def test_report_identifies_provenance_logged_duplicates_on_fixture(
            self, tmp_path):
        """End-to-end on a fixture: DB → loader → analyze_project → report."""
        db = self._seed_db(tmp_path)

        known = load_known_duplicate_names(db_path=db)
        project_struct = {
            "items": [
                {"id": 1, "name": "Master", "type": "composition",
                 "width": 1920, "height": 1080, "duration": 10.0,
                 "layers": [{"source_comp_id": 3}]},
                {"id": 2, "name": "Hero_1080x1920", "type": "composition",
                 "width": 1080, "height": 1920, "duration": 5.0,
                 "layers": []},
                {"id": 3, "name": "Title_1080x1920", "type": "composition",
                 "width": 1080, "height": 1920, "duration": 5.0,
                 "layers": []},
                {"id": 4, "name": "User_Comp", "type": "composition",
                 "width": 1920, "height": 1080, "duration": 10.0,
                 "layers": []},
            ]
        }
        plan = CompCleaner.analyze_project(project_struct, known_duplicates=known)

        assert plan.total_orphans_count == 1
        assert plan.orphaned_candidates[0].name == "Hero_1080x1920"
        protected = {p["name"]: p["reason"] for p in plan.protected_comps}
        assert protected["Title_1080x1920"] == "REFERENCED_BY_LAYERS"
        assert "User_Comp" not in protected


class TestDimensionDb:
    """Issue #16 — the per-project SQLite store itself."""

    def test_fresh_db_gets_schema_version_1(self, tmp_path):
        conn = open_project_db(str(tmp_path))
        try:
            assert schema_version(conn) == 1
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"meta", "creations", "runs"} <= tables
        finally:
            conn.close()

    def test_reopen_is_idempotent(self, tmp_path):
        conn = open_project_db(str(tmp_path))
        conn.close()
        conn = open_project_db(str(tmp_path))
        try:
            assert schema_version(conn) == 1
        finally:
            conn.close()

    def test_db_lives_at_dot_dimension_dimension_db(self, tmp_path):
        conn = open_project_db(str(tmp_path))
        conn.close()
        assert os.path.isfile(db_path_for(str(tmp_path)))
        assert db_path_for(str(tmp_path)).endswith(
            os.path.join(".dimension", "dimension.db"))

    def test_creations_round_trip(self, tmp_path):
        conn = open_project_db(str(tmp_path))
        try:
            row_id = record_creation(
                conn, name="Hero_1080x1920", source="Hero",
                session="sess-7", operation="duplicate")
            assert row_id >= 1
            row = conn.execute(
                "SELECT name, source, session, timestamp, operation"
                " FROM creations WHERE id = ?", (row_id,)).fetchone()
            assert row[0] == "Hero_1080x1920"
            assert row[1] == "Hero"
            assert row[2] == "sess-7"
            assert row[3]  # ISO-8601 timestamp filled in
            assert row[4] == "duplicate"
        finally:
            conn.close()

    def test_runs_round_trip_and_list(self, tmp_path):
        conn = open_project_db(str(tmp_path))
        try:
            record_run(conn, session="sess-7", status="ok",
                       detail={"preset": "tiktok"})
            record_run(conn, session="sess-8", status="failed")
            runs = list_runs(conn)
            assert [r["session"] for r in runs] == ["sess-8", "sess-7"]
            assert runs[1]["status"] == "ok"
            assert runs[1]["detail"] == {"preset": "tiktok"}
            assert list_runs(conn, session="sess-7")[0]["id"] == runs[1]["id"]
        finally:
            conn.close()

    def test_legacy_duplication_log_imports_once(self, tmp_path):
        log_path = tmp_path / ".dimension" / "duplication_log.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps({
            "session_id": "sess-legacy",
            "duplicates_made": [
                {"original_uid": "u1", "original_name": "Hero",
                 "duplicate_name": "Hero_1080x1920"},
                {"original_uid": "u2", "original_name": "",
                 "duplicate_name": "  "},  # blank → skipped
            ],
        }), encoding="utf-8")

        conn = open_project_db(str(tmp_path))
        try:
            imported = import_legacy_duplication_log(conn, str(log_path))
            assert imported == 1
            names = load_known_duplicate_names(
                db_path=db_path_for(str(tmp_path)))
            assert names == {"Hero_1080x1920"}
        finally:
            conn.close()

    def test_legacy_import_missing_file_imports_nothing(self, tmp_path):
        conn = open_project_db(str(tmp_path))
        try:
            assert import_legacy_duplication_log(
                conn, str(tmp_path / "nope.json")) == 0
        finally:
            conn.close()

    def test_nothing_creates_duplication_log_json(self, tmp_path):
        """Issue #16: the JSON file must never be created by this store."""
        conn = open_project_db(str(tmp_path))
        try:
            record_creation(conn, name="A", session="s")
            record_run(conn, session="s")
            import_legacy_duplication_log(conn, str(tmp_path / "nope.json"))
        finally:
            conn.close()
        assert not os.path.exists(
            os.path.join(str(tmp_path), ".dimension", "duplication_log.json"))

    def test_known_names_empty_on_fresh_db(self, tmp_path):
        conn = open_project_db(str(tmp_path))
        conn.close()
        assert load_known_duplicate_names(
            db_path=db_path_for(str(tmp_path))) == set()
