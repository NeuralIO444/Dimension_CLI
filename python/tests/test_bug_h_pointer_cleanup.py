# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_bug_h_pointer_cleanup.py
Bug H contract test — `cleanup_session` must invalidate the
JSX-owned manifest pointer (`current_manifest.txt`) so that a
subsequent `resolve_manifest_path` returns the idle-state None
cleanly, without emitting the "manifest pointer dangles" WARN.

This is the real archive → resolve flow, not a mock:
  - Stage the post-conform world inside `tmp_path` (manifest +
    sidecar + conformed/chunk manifests + report + Chunks dir +
    pointer file pointing at the staged manifest).
  - Run the real `PayloadSlicer.cleanup_session()`.
  - Call the real `resolve_manifest_path()`.
  - Assert: returns None AND no dangling-pointer WARN was emitted
    (no entry added to `manifest_source._dangling_logged`).

Pre-fix: cleanup_session moves the manifest into logs/archive but
leaves the pointer file untouched. The next resolve_manifest_path
reads the pointer, finds the target gone, fires the WARN, and
returns None — failing this test on the dangling-WARN assertion.

Post-fix: cleanup_session removes the pointer alongside the
archive. _read_pointer takes the early `if not _POINTER_PATH.exists()`
return and no WARN is issued.
"""

from __future__ import annotations

import logging
import os
import sys


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


class TestCleanupSessionInvalidatesPointer:
    def test_post_archive_resolver_returns_none_no_warn(
        self, tmp_path, monkeypatch, caplog
    ):
        from logic import manifest_source as m
        from logic.exporter import PayloadSlicer

        # `cleanup_session` resolves all archive targets via
        # os.path.abspath against cwd. Stage the entire world inside
        # tmp_path so the move targets land in tmp_path/logs/archive.
        monkeypatch.chdir(tmp_path)

        # Post-conform repo-root state, exactly what
        # `cleanup_session`'s `files_to_move` list expects.
        manifest = tmp_path / "scrape_manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        (tmp_path / "scrape_manifest.json.sha256").write_text(
            "deadbeef", encoding="utf-8")
        (tmp_path / "conformed_manifest.json").write_text(
            "{}", encoding="utf-8")
        (tmp_path / "chunk_manifest.json").write_text(
            "{}", encoding="utf-8")
        (tmp_path / "conform_report.html").write_text(
            "<html></html>", encoding="utf-8")
        chunks = tmp_path / "Chunks"
        chunks.mkdir()
        (chunks / "chunk_000.json").write_text("{}", encoding="utf-8")

        # The JSX-owned pointer file, pointing at the staged manifest.
        pointer = tmp_path / "current_manifest.txt"
        pointer.write_text(str(manifest), encoding="utf-8")
        monkeypatch.setattr(m, "_POINTER_PATH", pointer)

        # Per-session dedup set survives across tests in a single
        # pytest run. Clear it so a WARN-if-it-fires is observable.
        m._dangling_logged.clear()

        # Sanity: resolver returns the manifest BEFORE archive.
        assert m.resolve_manifest_path(tmp_path) == manifest

        # Real cleanup, no mocks. Explicitly perform final session cleanup.
        PayloadSlicer.cleanup_session(keep_manifest=False)

        # Manifest moved into the archive (sanity).
        assert not manifest.exists(), (
            "cleanup_session did not move scrape_manifest.json — "
            "test environment is misconfigured"
        )

        # The crux: post-archive resolve must be a clean idle-state.
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="dimension"):
            resolved = m.resolve_manifest_path(tmp_path)

        assert resolved is None, (
            f"resolve_manifest_path returned {resolved!r} after "
            f"cleanup_session; expected None (idle state)"
        )

        # No new entries in the dedup set means no dangling WARN
        # fired inside `_read_pointer`. The set is the cause-and-
        # effect partner of the warning call site, so it's the
        # exact behavioural assertion.
        assert m._dangling_logged == set(), (
            "cleanup_session left the pointer file pointing at the "
            "moved-away manifest; resolve_manifest_path emitted the "
            "dangling-pointer WARN that Bug H tracks. "
            f"_dangling_logged: {m._dangling_logged!r}"
        )

        # Belt-and-suspenders: also assert against the caplog stream
        # in case the non-propagating `dimension` logger ever changes.
        dangling = [
            r for r in caplog.records
            if "pointer dangles" in r.getMessage()
        ]
        assert dangling == [], (
            "dangling-pointer WARN appeared in caplog after "
            f"cleanup_session: {[r.getMessage() for r in dangling]}"
        )

    def test_cleanup_session_preserves_manifest_and_pointer(
        self, tmp_path, monkeypatch, caplog
    ):
        from logic import manifest_source as m
        from logic.exporter import PayloadSlicer

        # `cleanup_session` resolves all archive targets via
        # os.path.abspath against cwd. Stage the entire world inside
        # tmp_path so the move targets land in tmp_path/logs/archive.
        monkeypatch.chdir(tmp_path)

        # Post-conform repo-root state.
        manifest = tmp_path / "scrape_manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        (tmp_path / "scrape_manifest.json.sha256").write_text(
            "deadbeef", encoding="utf-8")
        (tmp_path / "conformed_manifest.json").write_text(
            "{}", encoding="utf-8")
        (tmp_path / "chunk_manifest.json").write_text(
            "{}", encoding="utf-8")
        (tmp_path / "conform_report.html").write_text(
            "<html></html>", encoding="utf-8")
        chunks = tmp_path / "Chunks"
        chunks.mkdir()
        (chunks / "chunk_000.json").write_text("{}", encoding="utf-8")

        # The JSX-owned pointer file, pointing at the staged manifest.
        pointer = tmp_path / "current_manifest.txt"
        pointer.write_text(str(manifest), encoding="utf-8")
        monkeypatch.setattr(m, "_POINTER_PATH", pointer)

        m._dangling_logged.clear()

        # Sanity: resolver returns the manifest BEFORE archive.
        assert m.resolve_manifest_path(tmp_path) == manifest

        # Default sequential cleanup (keep_manifest=True).
        PayloadSlicer.cleanup_session(keep_manifest=True)

        # Assert: Root manifest files and pointer still exist.
        assert manifest.exists(), "scrape_manifest.json should be preserved at root"
        assert (tmp_path / "scrape_manifest.json.sha256").exists(), "SHA-256 sidecar should be preserved"
        assert pointer.exists(), "current_manifest.txt pointer should not be deleted"

        # Assert: Target-specific files and Chunks dir are archived (moved).
        assert not (tmp_path / "conformed_manifest.json").exists(), "conformed_manifest.json should be moved"
        assert not (tmp_path / "chunk_manifest.json").exists(), "chunk_manifest.json should be moved"
        assert not (tmp_path / "conform_report.html").exists(), "conform_report.html should be moved"
        assert not chunks.exists(), "Chunks directory should be moved"

        # Assert: The archive folder contains copies/moves of all expected files.
        archive_dirs = list((tmp_path / "logs" / "archive").iterdir())
        assert len(archive_dirs) == 1, f"Expected 1 session archive folder, got {archive_dirs}"
        session_folder = archive_dirs[0]
        assert (session_folder / "scrape_manifest.json").exists(), "Archive should have scrape_manifest.json"
        assert (session_folder / "scrape_manifest.json.sha256").exists(), "Archive should have SHA-256 sidecar"
        assert (session_folder / "conformed_manifest.json").exists(), "Archive should have conformed_manifest.json"
        assert (session_folder / "conform_report.html").exists(), "Archive should have conform_report.html"
        assert (session_folder / "Chunks" / "chunk_000.json").exists(), "Archive should have Chunks dir contents"

        # Assert: The resolver still successfully resolves the root manifest path without warnings.
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="dimension"):
            resolved = m.resolve_manifest_path(tmp_path)
        assert resolved == manifest, "Resolver should still resolve to root manifest"
        assert m._dangling_logged == set(), "No dangling warnings should be emitted"
