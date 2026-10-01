# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_layer_count_drop_detector.py
Slot 4 — Cross-session layer-count drop detector.

Tests the `_prior_layer_count_for_comp` helper and the drop-detection
hook inside `SovereignBridge.parse_manifest`.

Fixture strategy: real archived `scrape_manifest.json` files from
`logs/archive/`, copied into `python/tests/fixtures/slot_4/`. No
hand-rolled JSON. Per CLAUDE.md "Trusting synthetic Python test
fixtures..." anti-pattern, the detector is exercised against
manifests that AE / JSX actually wrote.

Fixtures:
  - final_comp_2layers_apr29.json — real, Session_20260429_134222
  - final_comp_2layers_may13.json — real, Session_20260513_100101
  - final_comp_1layer_doctored.json — derived from may13, one layer
    dropped from layers[]. Used for the drop-case test.
"""

import os
import shutil
import sys
from pathlib import Path


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "slot_4"
APR29_FIXTURE = FIXTURES_DIR / "final_comp_2layers_apr29.json"
MAY13_FIXTURE = FIXTURES_DIR / "final_comp_2layers_may13.json"
DROPPED_FIXTURE = FIXTURES_DIR / "final_comp_1layer_doctored.json"


def _stage_archive_session(
    repo_root: Path, session_name: str, manifest_src: Path
) -> Path:
    """Mirror exporter.cleanup_session's layout — drop a real
    archived scrape_manifest.json under logs/archive/<session>/."""
    session_dir = repo_root / "logs" / "archive" / session_name
    session_dir.mkdir(parents=True, exist_ok=True)
    dst = session_dir / "scrape_manifest.json"
    shutil.copy2(manifest_src, dst)
    return dst


# ── Helper direct tests ───────────────────────────────────────────


class TestPriorLayerCountHelper:
    """Direct unit tests on _prior_layer_count_for_comp."""

    def test_silent_skip_when_no_prior_archive(self, tmp_path):
        """No logs/archive dir → None. First-conform behaviour."""
        from bridge.sovereign_bridge import _prior_layer_count_for_comp
        assert _prior_layer_count_for_comp("Final Comp", tmp_path) is None

    def test_silent_skip_when_no_prior_for_this_comp(self, tmp_path):
        """Archive present, but no session matches the requested
        comp name → None. Avoids false matches across projects."""
        from bridge.sovereign_bridge import _prior_layer_count_for_comp
        _stage_archive_session(
            tmp_path, "Session_20260429_134222", APR29_FIXTURE
        )
        # Real archive holds "Final Comp"; ask for something else.
        result = _prior_layer_count_for_comp(
            "Some Other Comp", tmp_path
        )
        assert result is None

    def test_returns_prior_count_on_match(self, tmp_path):
        """Single matching session → returns its layer count."""
        from bridge.sovereign_bridge import _prior_layer_count_for_comp
        _stage_archive_session(
            tmp_path, "Session_20260429_134222", APR29_FIXTURE
        )
        result = _prior_layer_count_for_comp("Final Comp", tmp_path)
        assert result == 2

    def test_picks_most_recent_when_multiple(self, tmp_path):
        """Multiple sessions for the same comp → returns the most
        recent (lex-sort by Session_YYYYMMDD_HHMMSS folder name)."""
        from bridge.sovereign_bridge import _prior_layer_count_for_comp
        # Older session with 2 layers
        _stage_archive_session(
            tmp_path, "Session_20260429_134222", APR29_FIXTURE
        )
        # Most recent session, but DOCTORED to 1 layer — the helper
        # should return the doctored count, proving it picked the
        # newest by folder name rather than scanning randomly.
        _stage_archive_session(
            tmp_path, "Session_20260513_100101", DROPPED_FIXTURE
        )
        result = _prior_layer_count_for_comp("Final Comp", tmp_path)
        assert result == 1, (
            "expected 1 (newest session's doctored count); got "
            f"{result}. Helper may not be sorting by folder name."
        )


# ── parse_manifest hook tests (via caplog) ────────────────────────


def _write_current_manifest(repo_root: Path, fixture: Path) -> Path:
    """Stage a 'current' scrape_manifest.json at the bridge's
    expected location. parse_manifest reads from disk by path,
    not from the project_root directly, so we just point it at
    a file inside tmp_path."""
    dst = repo_root / "scrape_manifest.json"
    shutil.copy2(fixture, dst)
    return dst


class TestParseManifestDropHook:
    """parse_manifest must log a WARN with comp + prior + current
    counts when len(layers) decreases vs the most recent archived
    session for the same comp.

    Note on capture method: the project's custom logger
    (python/core/logger.py) sets `propagate = False` so test output
    stays clean — defeating pytest's caplog. We monkeypatch
    `log.warning` directly on `bridge.sovereign_bridge` (where the
    name is LOOKED UP, per CLAUDE.md `from module import name`
    sharp edge) and capture the (msg, extra) tuples at the call
    site. Same pattern used by test_comment_gardener_integration.
    """

    def _patch_warning_capture(self, monkeypatch):
        from bridge import sovereign_bridge
        calls = []

        def _capture(msg, *args, **kwargs):
            calls.append((msg, kwargs.get("extra") or {}))

        monkeypatch.setattr(sovereign_bridge.log, "warning", _capture)
        return calls

    def test_logs_warn_on_drop(self, tmp_path, monkeypatch):
        from bridge.sovereign_bridge import SovereignBridge
        # Prior archive: 2-layer Final Comp.
        _stage_archive_session(
            tmp_path, "Session_20260429_134222", APR29_FIXTURE
        )
        # Current parse: 1-layer doctored Final Comp.
        current_path = _write_current_manifest(tmp_path, DROPPED_FIXTURE)

        warning_calls = self._patch_warning_capture(monkeypatch)
        bridge = SovereignBridge(project_root=str(tmp_path))
        bridge.parse_manifest(str(current_path))

        drop_calls = [
            (msg, extra) for (msg, extra) in warning_calls
            if msg == "Layer count drop detected"
        ]
        assert len(drop_calls) == 1, (
            f"expected 1 drop-detector WARN, got {len(drop_calls)}; "
            f"all warning calls: {warning_calls}"
        )
        _, extra = drop_calls[0]
        assert extra.get("comp") == "Final Comp"
        assert extra.get("prior_count") == 2
        assert extra.get("current_count") == 1

    def test_no_log_on_equal(self, tmp_path, monkeypatch):
        """prior == current → no WARN. Clean re-conform of unchanged
        comp must stay silent."""
        from bridge.sovereign_bridge import SovereignBridge
        _stage_archive_session(
            tmp_path, "Session_20260429_134222", APR29_FIXTURE
        )
        current_path = _write_current_manifest(tmp_path, MAY13_FIXTURE)

        warning_calls = self._patch_warning_capture(monkeypatch)
        bridge = SovereignBridge(project_root=str(tmp_path))
        bridge.parse_manifest(str(current_path))

        drop_calls = [
            msg for (msg, _) in warning_calls
            if msg == "Layer count drop detected"
        ]
        assert drop_calls == [], (
            f"expected no drop-detector WARN on equal counts; got "
            f"{drop_calls}"
        )

    def test_no_log_on_increase(self, tmp_path, monkeypatch):
        """prior < current → no WARN. Artist added a layer; this
        is normal, not a drop."""
        from bridge.sovereign_bridge import SovereignBridge
        # Prior: 1-layer doctored. Current: 2-layer real.
        _stage_archive_session(
            tmp_path, "Session_20260429_134222", DROPPED_FIXTURE
        )
        current_path = _write_current_manifest(tmp_path, MAY13_FIXTURE)

        warning_calls = self._patch_warning_capture(monkeypatch)
        bridge = SovereignBridge(project_root=str(tmp_path))
        bridge.parse_manifest(str(current_path))

        drop_calls = [
            msg for (msg, _) in warning_calls
            if msg == "Layer count drop detected"
        ]
        assert drop_calls == [], (
            f"expected no drop-detector WARN on increase; got "
            f"{drop_calls}"
        )
