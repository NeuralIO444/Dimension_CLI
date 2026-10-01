# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_slot_11_results_sweep.py
Slot 11 Stage D (2026-05-15) — session-end sweep of
`.dimension_inbox/results/` inside `PayloadSlicer.cleanup_session()`.

Result files are session-bound by definition. Stage A quarantined
them to `results/`; Stage B made `_wait_for_result` clean up on
every exit path. Stage D is the belt-and-suspenders close-out: at
session end, any result file that somehow outlived its requestor
is unconditionally swept.

Scope (verified by these tests):
  - Only `*.result.json` files inside `.dimension_inbox/results/`
    are removed.
  - The inbox root (heartbeat file, job descriptors, etc.) and
    other subdirs (`processing/`, `quarantine_*/`) are NOT touched.
  - Cleanup is best-effort: a permission error on one file does
    not break the rest of cleanup_session.

Pre-existing inbox cruft (`.DS_Store`, `tick_trace.txt`,
`quarantine_*/`) is deliberately out of scope for this sweep —
filed as a follow-up.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


class TestResultsSweep:
    """Session-end sweep of `.dimension_inbox/results/`."""

    def _stage_inbox(self, tmp_path: Path) -> Path:
        """Build a representative `.dimension_inbox/` tree under
        tmp_path. Returns the inbox directory."""
        inbox = tmp_path / ".dimension_inbox"
        inbox.mkdir()
        # Inbox root entries that MUST NOT be touched.
        (inbox / "poller_heartbeat.txt").write_text(
            "1747337800.0\n", encoding="utf-8")
        (inbox / "processing").mkdir()
        (inbox / "quarantine_20260512").mkdir()
        (inbox / "quarantine_20260512" / "old_job.json").write_text(
            "{}", encoding="utf-8")
        # Optionally a stray job descriptor at the inbox root.
        (inbox / "job_unrelated.json").write_text(
            '{"type": "scrape"}', encoding="utf-8")
        # The target of the sweep — results subdir with result files.
        results = inbox / "results"
        results.mkdir()
        return inbox

    def test_sweep_clears_result_files(self, tmp_path, monkeypatch):
        """cleanup_session removes every `*.result.json` in
        `.dimension_inbox/results/`."""
        from logic.exporter import PayloadSlicer

        monkeypatch.chdir(tmp_path)
        inbox = self._stage_inbox(tmp_path)
        results = inbox / "results"

        # Drop a few synthetic result files in the sweep target.
        for jid in ("aaa111", "bbb222", "ccc333"):
            (results / f"job_{jid}.result.json").write_text(
                '{"status": "OK"}', encoding="utf-8")

        # Sanity pre-condition.
        before = sorted(p.name for p in results.iterdir())
        assert before == [
            "job_aaa111.result.json",
            "job_bbb222.result.json",
            "job_ccc333.result.json",
        ], f"fixture setup: {before}"

        PayloadSlicer.cleanup_session()

        # After: results dir is empty (the dir itself may stay).
        assert results.exists(), (
            "Sweep removed the results/ directory itself — scope is "
            "files only"
        )
        leftover = sorted(p.name for p in results.iterdir())
        assert leftover == [], (
            f"Sweep left result files behind: {leftover}"
        )

    def test_sweep_preserves_inbox_root_and_siblings(
        self, tmp_path, monkeypatch,
    ):
        """Heartbeat file, job descriptors, and sibling subdirs
        (`processing/`, `quarantine_*/`) must survive the sweep."""
        from logic.exporter import PayloadSlicer

        monkeypatch.chdir(tmp_path)
        inbox = self._stage_inbox(tmp_path)

        # Also drop a result file so the sweep has work to do.
        (inbox / "results" / "job_xyz.result.json").write_text(
            '{"status": "OK"}', encoding="utf-8")

        PayloadSlicer.cleanup_session()

        # Inbox root: heartbeat untouched.
        heartbeat = inbox / "poller_heartbeat.txt"
        assert heartbeat.exists(), (
            "Sweep wrongly removed the poller heartbeat"
        )
        assert heartbeat.read_text(encoding="utf-8") == "1747337800.0\n"

        # Inbox root: stray job descriptor untouched.
        job = inbox / "job_unrelated.json"
        assert job.exists(), (
            "Sweep wrongly removed an inbox-root job descriptor"
        )

        # Sibling subdirs untouched.
        assert (inbox / "processing").is_dir(), (
            "Sweep wrongly removed processing/"
        )
        assert (inbox / "quarantine_20260512").is_dir(), (
            "Sweep wrongly removed quarantine_20260512/"
        )
        assert (inbox / "quarantine_20260512" / "old_job.json").exists(), (
            "Sweep wrongly reached into quarantine_*/"
        )

    def test_sweep_only_targets_result_json_suffix(
        self, tmp_path, monkeypatch,
    ):
        """A non-`*.result.json` file dropped into results/ (e.g.
        a `.tmp` sibling or stray text file) is NOT touched. The
        sweep's predicate is strict on the `.result.json` suffix."""
        from logic.exporter import PayloadSlicer

        monkeypatch.chdir(tmp_path)
        inbox = self._stage_inbox(tmp_path)
        results = inbox / "results"

        # A legitimate result file (should be swept).
        (results / "job_aaa.result.json").write_text(
            '{"status": "OK"}', encoding="utf-8")
        # A stray non-result file (should stay).
        (results / "stray.txt").write_text("noise", encoding="utf-8")

        PayloadSlicer.cleanup_session()

        assert not (results / "job_aaa.result.json").exists(), (
            "Result file was not swept"
        )
        assert (results / "stray.txt").exists(), (
            "Sweep wrongly removed a non-result file from results/"
        )

    def test_sweep_no_results_dir_is_no_op(self, tmp_path, monkeypatch):
        """If `.dimension_inbox/results/` does not exist (e.g. a
        fresh repo that has never dispatched a job), cleanup_session
        must not raise."""
        from logic.exporter import PayloadSlicer

        monkeypatch.chdir(tmp_path)
        # No .dimension_inbox at all — cleanup_session should still
        # archive whatever else is present (nothing here) without
        # blowing up on the missing inbox.
        PayloadSlicer.cleanup_session()  # must not raise

    def test_sweep_does_not_raise_on_unreadable_file(
        self, tmp_path, monkeypatch,
    ):
        """If unlink fails on a single file, sweep logs a warning
        and continues — cleanup_session is best-effort by contract."""
        from logic.exporter import PayloadSlicer
        import logic.exporter as exporter_mod

        monkeypatch.chdir(tmp_path)
        inbox = self._stage_inbox(tmp_path)
        results = inbox / "results"

        (results / "job_good.result.json").write_text(
            '{"status": "OK"}', encoding="utf-8")
        (results / "job_bad.result.json").write_text(
            '{"status": "OK"}', encoding="utf-8")

        # Force os.remove to raise on the "bad" entry only.
        real_remove = os.remove

        def flaky_remove(path):
            if path.endswith("job_bad.result.json"):
                raise OSError("simulated permission denied")
            real_remove(path)

        monkeypatch.setattr(exporter_mod.os, "remove", flaky_remove)

        # Must not raise — best-effort cleanup.
        PayloadSlicer.cleanup_session()

        # The "good" file was swept, the "bad" one was logged-and-skipped.
        assert not (results / "job_good.result.json").exists(), (
            "Good file should have been swept despite bad file failing"
        )
