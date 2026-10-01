# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_comp_cleaner_provenance.py

Issue #10 / Dimension #551 regression tests — provenance, not names.

#551: DIMENSION_DUP_PATTERN matched none of the names
`output_naming.resolve_output_name` actually produces, so the CLEAN COMPS
report was inert on real projects. The regex is deleted; the predicate is
now membership in the provenance set (`.dimension/duplication_log.json`
∪ manifest `duplication_plan`). These tests prove the names the old
regex missed are recognized by the new predicate.
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
    def test_duplication_log_names_load(self, tmp_path):
        log_path = tmp_path / "duplication_log.json"
        log_path.write_text(json.dumps({
            "session_id": "sess-1",
            "duplicates_made": [
                {"original_uid": "u1", "original_name": "Hero",
                 "duplicate_name": "Hero_1080x1920"},
                {"original_uid": "u2", "original_name": "Title",
                 "duplicate_name": "Title_1080x1920"},
            ],
        }), encoding="utf-8")

        names = load_known_duplicate_names(duplication_log_path=str(log_path))
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
        log_path = tmp_path / "duplication_log.json"
        log_path.write_text(json.dumps({
            "duplicates_made": [
                {"duplicate_name": "Hero_1080x1920"},
            ],
        }), encoding="utf-8")

        names = load_known_duplicate_names(
            duplication_log_path=str(log_path),
            manifest_path=str(manifest_path))
        assert names == {"Hero_1080x1920", "Bg_1080x1920"}

    def test_missing_files_contribute_nothing(self, tmp_path):
        names = load_known_duplicate_names(
            duplication_log_path=str(tmp_path / "nope.json"),
            manifest_path=str(tmp_path / "nope2.json"))
        assert names == set()

    def test_report_identifies_provenance_logged_duplicates_on_fixture(
            self, tmp_path):
        """End-to-end on a fixture: loader → analyze_project → report."""
        log_path = tmp_path / "duplication_log.json"
        log_path.write_text(json.dumps({
            "session_id": "sess-9",
            "duplicates_made": [
                {"original_uid": "u1", "original_name": "Hero",
                 "duplicate_name": "Hero_1080x1920"},
                # Logged but referenced → protected, not orphaned.
                {"original_uid": "u2", "original_name": "Title",
                 "duplicate_name": "Title_1080x1920"},
            ],
        }), encoding="utf-8")

        known = load_known_duplicate_names(duplication_log_path=str(log_path))
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
