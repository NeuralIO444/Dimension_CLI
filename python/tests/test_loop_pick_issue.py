# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_loop_pick_issue.py — ordering rules for the autonomous
loop's issue picker.

No tests existed for this module before 2026-09-04, which is part of why a
costly ordering flaw went unnoticed: a live tick picked the oldest eligible
issue (`difficulty:hard`), routed it to Opus per run_loop.sh's model ladder,
spent $1.65, and correctly concluded the work was blocked on a human — while
five `difficulty:easy` issues sat untouched behind it.

These tests pin the ordering contract so that regression is mechanical
rather than something noticed on a spend report.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools import loop_pick_issue as lp  # noqa: E402


def _issue(number: int, created: str, difficulty: str | None = None, extra=()):
    labels = [{"name": "ready-for-loop"}] + [{"name": n} for n in extra]
    if difficulty:
        labels.append({"name": f"difficulty:{difficulty}"})
    return {
        "number": number,
        "title": f"issue {number}",
        "body": "",
        "labels": labels,
        "createdAt": created,
        "url": f"https://example.invalid/{number}",
    }


class TestDifficultyRank:
    def test_easy_sorts_before_medium_before_hard(self):
        assert (
            lp._difficulty_rank(_issue(1, "2026-01-01", "easy"))
            < lp._difficulty_rank(_issue(2, "2026-01-01", "medium"))
            < lp._difficulty_rank(_issue(3, "2026-01-01", "hard"))
        )

    def test_unlabelled_ranks_as_medium(self):
        """Must match run_loop.sh's model ladder, which defaults an
        unlabelled issue to Sonnet — not Haiku, not Opus. If these two
        disagree, the loop silently pays the wrong rate."""
        assert lp._difficulty_rank(_issue(1, "2026-01-01", None)) == \
            lp._difficulty_rank(_issue(2, "2026-01-01", "medium"))

    def test_unknown_difficulty_value_falls_back_to_medium(self):
        assert lp._difficulty_rank(_issue(1, "2026-01-01", "spicy")) == 1

    def test_other_labels_do_not_confuse_the_rank(self):
        issue = _issue(1, "2026-01-01", "easy", extra=("soe", "documentation"))
        assert lp._difficulty_rank(issue) == 0


class TestOrdering:
    def _pick(self, monkeypatch, issues):
        import json

        class _Res:
            returncode = 0
            stdout = json.dumps(issues)
            stderr = ""

        monkeypatch.setattr(lp.subprocess, "run", lambda *a, **k: _Res())
        return lp.pick_next_issue()

    def test_easy_wins_over_an_older_hard_issue(self, monkeypatch):
        """The exact live scenario from 2026-09-04: #350 (hard) was oldest
        and got picked, burning Opus on work that was blocked on a human."""
        picked = self._pick(monkeypatch, [
            _issue(350, "2026-08-01T00:00:00Z", "hard"),
            _issue(403, "2026-09-03T00:00:00Z", "easy"),
        ])
        assert picked["number"] == 403

    def test_fifo_still_applies_within_one_difficulty_tier(self, monkeypatch):
        """Difficulty is the primary key, not a replacement for FIFO — an
        issue must not sit forever just because newer easy ones keep
        landing."""
        picked = self._pick(monkeypatch, [
            _issue(410, "2026-09-03T12:00:00Z", "easy"),
            _issue(403, "2026-09-03T09:00:00Z", "easy"),
            _issue(421, "2026-09-03T15:00:00Z", "easy"),
        ])
        assert picked["number"] == 403

    def test_hard_is_reached_only_when_nothing_easier_remains(self, monkeypatch):
        picked = self._pick(monkeypatch, [_issue(350, "2026-08-01T00:00:00Z", "hard")])
        assert picked["number"] == 350

    def test_no_eligible_issues_returns_none(self, monkeypatch):
        assert self._pick(monkeypatch, []) is None

    def test_gh_failure_returns_none_rather_than_raising(self, monkeypatch):
        """A gh outage should end the tick quietly, not crash it — the loop
        treats "nothing to do" as a valid outcome."""
        class _Res:
            returncode = 1
            stdout = ""
            stderr = "gh: network error"

        monkeypatch.setattr(lp.subprocess, "run", lambda *a, **k: _Res())
        assert lp.pick_next_issue() is None
