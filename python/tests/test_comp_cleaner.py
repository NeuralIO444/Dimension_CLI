# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_comp_cleaner.py — TASK-UX-01 / Issue #297
Unit tests for unreferenced duplicate comp cleanup planner.
"""

from core.comp_cleaner import CompCleaner, CompCleanupPlan


class TestCompCleaner:
    def test_finds_unreferenced_dimension_duplicate_comps(self):
        project_struct = {
            "items": [
                {"id": 100, "name": "Main_Hero_16x9", "type": "composition", "width": 1920, "height": 1080, "duration": 10.0, "layers": [
                    {"name": "Nested 1", "source_comp_id": 101},
                ]},
                {"id": 101, "name": "Main_Hero_16x9__dim_dup_1", "type": "composition", "width": 1920, "height": 1080, "duration": 10.0, "layers": []},
                # Unreferenced orphaned duplicate
                {"id": 102, "name": "Old_Conform__dim_dup_99", "type": "composition", "width": 1080, "height": 1920, "duration": 5.0, "layers": []},
                # Normal user comp (not matching pattern)
                {"id": 103, "name": "Asset_Precomp_A", "type": "composition", "width": 500, "height": 500, "duration": 2.0, "layers": []},
            ]
        }

        plan = CompCleaner.analyze_project(project_struct, active_comp_id=100)
        assert isinstance(plan, CompCleanupPlan)
        assert plan.total_comps_scanned == 4
        assert plan.total_orphans_count == 1
        assert plan.orphaned_candidates[0].comp_id == 102
        assert plan.orphaned_candidates[0].name == "Old_Conform__dim_dup_99"
        # Comp 101 is protected because Comp 100 references it
        assert any(p["comp_id"] == 101 for p in plan.protected_comps)

    def test_active_comp_never_orphaned(self):
        project_struct = {
            "items": [
                {"id": 200, "name": "Active_Conform__dim_dup_2", "type": "composition", "width": 1080, "height": 1920, "duration": 10.0, "layers": []},
            ]
        }
        plan = CompCleaner.analyze_project(project_struct, active_comp_id=200)
        assert plan.total_orphans_count == 0
        assert len(plan.protected_comps) == 1
        assert plan.protected_comps[0]["reason"] == "ACTIVE_COMP"
