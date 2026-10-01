# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_roadmap_issue_link_checker.py
Unit tests for the roadmap/BUGS.md issue-state drift checker.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock
import json

from tools.roadmap_issue_link_checker import (
    check_file,
    _get_issue_or_pr_state,
    _has_status_keyword_near,
    ISSUE_REF_PATTERN,
    STATUS_PATTERN,
)


def test_issue_ref_pattern():
    """Verify the regex correctly identifies issue/PR references."""
    text = "This is #123 a reference to #456, and not a reference to #abc."
    matches = list(ISSUE_REF_PATTERN.finditer(text))
    assert len(matches) == 2
    assert int(matches[0].group(1)) == 123
    assert int(matches[1].group(1)) == 456


def test_status_pattern_shipped():
    """Verify status pattern catches 'shipped'."""
    assert STATUS_PATTERN.search("This was shipped on Friday")
    assert STATUS_PATTERN.search("Status: SHIPPED")


def test_status_pattern_done():
    """Verify status pattern catches 'done'."""
    assert STATUS_PATTERN.search("This is done now")
    assert STATUS_PATTERN.search("DONE: implementation complete")


def test_status_pattern_checkmarks():
    """Verify status pattern catches checkmark variants."""
    assert STATUS_PATTERN.search("✅ complete")
    assert STATUS_PATTERN.search("✓ shipped")


def test_status_pattern_markdown_checkbox():
    """Verify status pattern catches markdown checkbox."""
    assert STATUS_PATTERN.search("- [x] Task complete")


def test_has_status_keyword_near_true():
    """Verify _has_status_keyword_near detects status nearby."""
    text = "This issue #123 has been shipped."
    pos = text.find("#123")
    assert _has_status_keyword_near(text, pos) is True


def test_has_status_keyword_near_false():
    """Verify _has_status_keyword_near returns False when no status near."""
    text = "This is #123. Completely unrelated text in the next sentence."
    pos = text.find("#123")
    assert _has_status_keyword_near(text, pos) is False


def test_has_status_keyword_near_forward_context():
    """Verify _has_status_keyword_near finds keywords in forward context."""
    text = "See #123 which was shipped last week."
    pos = text.find("#123")
    assert _has_status_keyword_near(text, pos) is True

    # Keyword far after the reference (beyond 200 char window)
    text = "#123 " + "x" * 250 + " was shipped"
    pos = text.find("#123")
    assert _has_status_keyword_near(text, pos) is False


@patch("tools.roadmap_issue_link_checker.subprocess.run")
def test_get_issue_or_pr_state_issue(mock_run):
    """Verify _get_issue_or_pr_state handles issues correctly."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=json.dumps({"state": "CLOSED", "title": "Test issue"}),
    )
    state, title = _get_issue_or_pr_state(123)
    assert state == "CLOSED"
    assert title == "Test issue"


@patch("tools.roadmap_issue_link_checker.subprocess.run")
def test_get_issue_or_pr_state_pr(mock_run):
    """Verify _get_issue_or_pr_state handles PRs correctly."""
    # First call (issue check) fails, second call (PR check) succeeds
    issue_result = MagicMock(returncode=1, stdout="", stderr="")
    pr_result = MagicMock(
        returncode=0,
        stdout=json.dumps({"state": "MERGED", "title": "Test PR"}),
    )
    mock_run.side_effect = [issue_result, pr_result]

    state, title = _get_issue_or_pr_state(456)
    assert state == "MERGED"
    assert title == "Test PR"


@patch("tools.roadmap_issue_link_checker.subprocess.run")
def test_get_issue_or_pr_state_not_found(mock_run):
    """Verify _get_issue_or_pr_state returns (None, None) when not found."""
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
    state, title = _get_issue_or_pr_state(999)
    assert state is None
    assert title is None


@patch("tools.roadmap_issue_link_checker._get_issue_or_pr_state")
def test_check_file_no_drifts(mock_get_state, tmp_path):
    """Verify check_file returns no drifts when state matches status claim."""
    mock_get_state.return_value = ("CLOSED", "Test closed issue")

    test_file = tmp_path / "test.md"
    test_file.write_text("This feature was shipped. See #123 for details.")

    results = check_file(test_file)
    assert len(results) == 1
    check = results[0]
    assert check.ref == 123
    assert check.state == "CLOSED"
    assert check.has_status_keyword is True
    assert check.is_drift is False


@patch("tools.roadmap_issue_link_checker._get_issue_or_pr_state")
def test_check_file_detects_drift(mock_get_state, tmp_path):
    """Verify check_file detects drift: status keyword but OPEN state."""
    mock_get_state.return_value = ("OPEN", "Test open issue")

    test_file = tmp_path / "test.md"
    test_file.write_text("This feature was shipped. See #123 for details.")

    results = check_file(test_file)
    assert len(results) == 1
    check = results[0]
    assert check.ref == 123
    assert check.state == "OPEN"
    assert check.has_status_keyword is True
    assert check.is_drift is True


@patch("tools.roadmap_issue_link_checker._get_issue_or_pr_state")
def test_check_file_multiple_refs(mock_get_state, tmp_path):
    """Verify check_file handles multiple references in one file."""
    mock_get_state.side_effect = [
        ("CLOSED", "Closed issue"),
        ("OPEN", "Open issue"),
        ("MERGED", "Merged PR"),
    ]

    test_file = tmp_path / "test.md"
    test_file.write_text(
        "Shipped: #100 done, #200 done, #300 shipped"
    )

    results = check_file(test_file)
    assert len(results) == 3
    assert results[0].is_drift is False  # CLOSED is not open
    assert results[1].is_drift is True   # OPEN with status keyword
    assert results[2].is_drift is False  # MERGED is not open


@patch("tools.roadmap_issue_link_checker._get_issue_or_pr_state")
def test_check_file_no_status_keyword(mock_get_state, tmp_path):
    """Verify check_file ignores references without status keywords."""
    mock_get_state.return_value = ("OPEN", "Test open issue")

    test_file = tmp_path / "test.md"
    test_file.write_text("See #123 for more information.")

    results = check_file(test_file)
    assert len(results) == 1
    check = results[0]
    assert check.state == "OPEN"
    assert check.has_status_keyword is False
    assert check.is_drift is False


def test_check_file_nonexistent():
    """Verify check_file returns empty list for nonexistent files."""
    results = check_file(Path("/nonexistent/path.md"))
    assert results == []


@patch("tools.roadmap_issue_link_checker._get_issue_or_pr_state")
def test_check_file_line_numbers(mock_get_state, tmp_path):
    """Verify check_file correctly reports line numbers."""
    mock_get_state.return_value = ("OPEN", "Test issue")

    test_file = tmp_path / "test.md"
    test_file.write_text(
        "Line 1: no reference\n"
        "Line 2: shipped #123\n"
        "Line 3: no reference\n"
        "Line 4: done #456\n"
    )

    results = check_file(test_file)
    assert len(results) == 2
    assert results[0].line_num == 2
    assert results[0].ref == 123
    assert results[1].line_num == 4
    assert results[1].ref == 456


@patch("tools.roadmap_issue_link_checker._get_issue_or_pr_state")
def test_check_file_file_path_in_result(mock_get_state, tmp_path):
    """Verify check_file stores the file path in results."""
    mock_get_state.return_value = ("CLOSED", "Test issue")

    test_file = tmp_path / "subdir" / "test.md"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("shipped #123")

    results = check_file(test_file)
    assert len(results) == 1
    assert str(test_file) in results[0].file
