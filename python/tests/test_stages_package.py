# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Contract tests for python/stages/ pipeline split."""

from __future__ import annotations

import inspect
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def test_orchestrator_is_thin_dispatcher():
    import orchestrator

    orchestrator_path = os.path.join(
        os.path.dirname(__file__), "..", "orchestrator.py"
    )
    with open(orchestrator_path, encoding="utf-8") as f:
        line_count = sum(1 for _ in f)
    assert line_count <= 130, f"orchestrator.py grew to {line_count} lines"

    src = inspect.getsource(orchestrator.main)
    assert "run_conform" in src
    assert "ScaleEngine" not in src


def test_conform_stage_exports_run_conform():
    from stages.conform import ConformConfig, run_conform

    assert callable(run_conform)
    cfg = ConformConfig(source="/nonexistent/manifest.json")
    with pytest.raises(Exception):
        run_conform(cfg)


def test_conform_io_load_and_resolve(tmp_path):
    from stages.conform import ConformConfig, SourceNotFoundError
    from stages.conform_io import load_scrape_manifest, resolve_conform_target

    missing = tmp_path / "nope.json"
    with pytest.raises(SourceNotFoundError):
        load_scrape_manifest(str(missing))

    manifest_path = tmp_path / "scrape_manifest.json"
    manifest_path.write_text(
        json.dumps({
            "status": "OK",
            "schema_version": 5,
            "project_info": {"name": "T", "width": 1920, "height": 1080},
            "layers": [],
        }),
        encoding="utf-8",
    )
    manifest, raw = load_scrape_manifest(str(manifest_path))
    assert manifest.project_info.name == "T"
    assert raw["status"] == "OK"

    manual = resolve_conform_target(
        ConformConfig(source=str(manifest_path), width=1080, height=1920, duration=15.0, fps=30.0)
    )
    assert manual.width == 1080
    assert manual.height == 1920
    assert manual.duration == 15.0
    assert manual.fps == 30.0

    preset = resolve_conform_target(
        ConformConfig(source=str(manifest_path), preset="tiktok_video", duration=60.0)
    )
    assert preset.width > 0
    assert preset.height > 0
    assert preset.duration == 60.0





def test_inject_stage_loads_monolithic_manifest(tmp_path):
    from stages.inject import load_monolithic_manifest

    chunk_dir = tmp_path / "Chunks"
    chunk_dir.mkdir()
    (chunk_dir / "chunk_0.json").write_text(
        '{"layers": [{"uid": "a", "index": 0}]}', encoding="utf-8"
    )
    manifest = {
        "chunk_paths": ["Chunks/chunk_0.json"],
        "expected_comp_name": "Test",
    }
    manifest_path = tmp_path / "chunk_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    loaded = load_monolithic_manifest(str(manifest_path))
    assert len(loaded["layers"]) == 1
    assert "chunk_paths" not in loaded


def test_process_inject_logs_classifies_complete_unchanged():
    from stages.inject import process_inject_logs

    logs = [{"phase": "INJECT"}, {"status": "COMPLETE"}]
    result = process_inject_logs(logs)
    assert result.completed is True
    assert result.failed is False
    assert result.aborted is False


def test_process_inject_logs_classifies_failed_unchanged():
    from stages.inject import process_inject_logs

    logs = [{"status": "FAILED", "error": "Babysitter eval failed"}]
    result = process_inject_logs(logs)
    assert result.failed is True
    assert result.completed is False
    assert result.aborted is False


def test_process_inject_logs_classifies_aborted_distinguishably():
    from stages.inject import process_inject_logs

    logs = [{"phase": "INJECT"}, {"status": "ABORTED", "error": "cancelled by user"}]
    result = process_inject_logs(logs)
    assert result.aborted is True
    assert result.completed is False
    assert result.failed is False


def test_process_inject_logs_phase_map_fires_only_for_live_anchors():
    from stages.inject import process_inject_logs

    logs = [
        {"phase": "INJECT"},
        {"phase": "REWIRE"},
        {"phase": "REPORT"},
        {"phase": "SETUP"},
        {"phase": "DUPLICATIONS"},
        {"phase": "REWIRES"},
        {"phase": "CHUNKS"},
        {"phase": "AUDIT"},
        {"phase": "COMPLETE"},  # a phase value, not a status value
    ]
    calls: list[tuple[str, int]] = []
    process_inject_logs(logs, on_phase=lambda phase, pct: calls.append((phase, pct)))
    assert calls == [("INJECT", 93), ("REWIRE", 94), ("REPORT", 98)]


def test_process_inject_logs_chunk_interpolation_missing_or_zero_total_chunks():
    from stages.inject import process_inject_logs

    logs = [
        {"chunkIndex": 2},
        {"chunkIndex": 0, "totalChunks": 0},
        {"chunkIndex": 1, "totalChunks": -3},
    ]
    calls: list[tuple[str, int]] = []
    process_inject_logs(logs, on_phase=lambda phase, pct: calls.append((phase, pct)))
    assert calls == []


def test_process_inject_logs_chunk_interpolation_within_band_and_monotonic():
    from stages.inject import process_inject_logs

    logs = [{"chunkIndex": i, "totalChunks": 4} for i in range(4)]
    pcts: list[float] = []
    process_inject_logs(logs, on_phase=lambda phase, pct: pcts.append(pct))
    assert len(pcts) == 4
    assert all(p > 95 and p <= 97 for p in pcts)
    # Non-decreasing, not strictly increasing: the consumer rounds
    # chunk_pct to an int (per plan §4.4, matching on_phase's `int`
    # signature), and Python's banker's rounding can map two adjacent
    # fractional values to the same integer for small totalChunks
    # (e.g. round(95.5) == round(96.5) == 96) — a real, harmless
    # cosmetic property of the rounding, not a monotonicity violation.
    assert pcts == sorted(pcts)
    assert pcts[-1] == 97


def test_process_inject_logs_mixed_shapes_preserve_warnings():
    from stages.inject import process_inject_logs

    logs = [
        {"chunkIndex": 0, "totalChunks": 2},
        {"phase": "INJECT"},
        {
            "event": "telemetry",
            "phase": "mirror_setup_skips",
            "not_found_count": 2,
            "create_failed_count": 1,
        },
        {"phase": "REWIRE", "detail": "target mirror not found: X"},
        {"status": "COMPLETE"},
    ]
    result = process_inject_logs(logs)
    assert result.warnings == {"skips": 3, "rewire": 1}
    assert result.completed is True


def test_inject_result_aborted_field_defaults_false():
    from stages.inject import InjectResult

    result = InjectResult(completed=True, failed=False, logs=[], warnings={})
    assert result.aborted is False


def test_inject_phase_progress_matches_cep_mapping():
    from stages.inject import INJECT_PHASE_PROGRESS

    assert INJECT_PHASE_PROGRESS == {"INJECT": 93, "REWIRE": 94, "REPORT": 98}


def test_batch_forwards_engine_events_and_target_progress(monkeypatch):
    from stages import batch as batch_mod
    from stages.conform import ConformResult

    events: list[dict] = []
    target_starts: list[tuple[int, int, str]] = []

    def _fake_run_conform(_cfg, *, on_engine_event=None):
        if on_engine_event is not None:
            on_engine_event({"type": "progress", "pct": 40, "msg": "Scale complete"})
        return ConformResult(
            chunk_manifest_path="/tmp/chunk_manifest.json",
            report_path="/tmp/report.html",
            conformed_path="/tmp/conformed_manifest.json",
            run_warnings=[],
        )

    monkeypatch.setattr(batch_mod, "run_conform", _fake_run_conform)

    success_count, _logs = batch_mod.run_batch_conform(
        "/fake/manifest.json",
        [{"name": "TikTok", "width": 1080, "height": 1920}],
        on_engine_event=events.append,
        on_target_start=lambda i, t, label: target_starts.append((i, t, label)),
    )

    assert success_count == 1
    assert target_starts == [(0, 1, "TikTok")]
    assert events == [{"type": "progress", "pct": 40, "msg": "Scale complete"}]


def test_survey_stage_wraps_surveyor():
    from stages import run_survey
    from models.scrape_manifest import ScrapeManifest

    manifest = ScrapeManifest.model_validate({
        "status": "OK",
        "schema_version": 5,
        "project_info": {"name": "T", "width": 1920, "height": 1080},
        "layers": [],
    })
    stats = run_survey(manifest)
    assert isinstance(stats, dict)