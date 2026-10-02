# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_loop_path_guard.py
Regression coverage for python/tools/loop_path_guard.py -- the Phase 8/9
autonomous-loop safety boundary. Found live (2026-09-03) that its
_PIPELINE_GLOBS list had drifted stale: it protected "python/core/
exporter.py", but the real Export-stage file is "python/logic/
exporter.py" -- meaning the loop could have modified the real pipeline
file without tripping its own guard. This file exists specifically so
that class of drift gets caught automatically, not discovered by hand
during unrelated work months later.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "tools")))

from loop_path_guard import _PIPELINE_GLOBS, touched_pipeline_files  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]


class TestPipelineGlobsStayLive:
    """Every literal (non-wildcard) entry in _PIPELINE_GLOBS must resolve
    to a real file. A wildcard entry (contains '*') is checked separately
    -- it must match at least one real file, so an entire glob doesn't
    silently start matching nothing after a rename/restructure."""

    def test_literal_paths_exist_on_disk(self):
        missing = []
        for pattern in _PIPELINE_GLOBS:
            if "*" in pattern:
                continue
            if not (_REPO_ROOT / pattern).is_file():
                missing.append(pattern)
        assert missing == [], (
            f"_PIPELINE_GLOBS has stale entries pointing at files that "
            f"don't exist: {missing}. The loop's Phase 8/9 safety boundary "
            f"silently stops protecting these paths -- fix the glob to "
            f"match the file's real current location."
        )

    def test_wildcard_patterns_match_at_least_one_real_file(self):
        no_matches = []
        for pattern in _PIPELINE_GLOBS:
            if "*" not in pattern:
                continue
            matched = list(_REPO_ROOT.glob(pattern))
            if not matched:
                no_matches.append(pattern)
        assert no_matches == [], (
            f"_PIPELINE_GLOBS wildcard entries matching zero real files: "
            f"{no_matches}. Likely a directory rename or restructure left "
            f"this pattern pointing at nothing."
        )


class TestTouchedPipelineFiles:
    def test_detects_a_literal_pipeline_file(self):
        hits = touched_pipeline_files(["python/core/gravity.py", "README.md"])
        assert hits == ["python/core/gravity.py"]

    def test_detects_a_wildcard_pipeline_file(self):
        hits = touched_pipeline_files(["python/core/scale_engine_narrow.py"])
        assert hits == ["python/core/scale_engine_narrow.py"]

    def test_non_pipeline_files_produce_no_hits(self):
        hits = touched_pipeline_files([
            "python/core/typographic_micro_guard.py",
            "docs/some_doc.md",
            "python/tools/loop_pick_issue.py",
        ])
        assert hits == []

    def test_the_exact_bug_this_file_exists_to_catch(self):
        """python/logic/exporter.py -- the REAL export-stage file -- must
        be flagged. Before the fix, only the nonexistent python/core/
        exporter.py was in the glob list, so this returned no hits."""
        hits = touched_pipeline_files(["python/logic/exporter.py"])
        assert hits == ["python/logic/exporter.py"]

    def test_empty_file_list_produces_no_hits(self):
        assert touched_pipeline_files([]) == []
