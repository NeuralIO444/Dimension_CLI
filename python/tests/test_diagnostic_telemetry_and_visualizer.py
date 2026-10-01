# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_diagnostic_telemetry_and_visualizer.py
Unit tests for Dimension Telemetry, Diagnostic Visualizer, and Diff CLI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.telemetry import TelemetryCollector, TELEMETRY
from tools.diagnostic_visualizer import generate_diagnostic_html
from tools.verify_math_and_pillars import run_manifest_diff


@pytest.mark.parametrize(
    "env_value,expected_enabled",
    [
        ("1", True),
        ("0", False),
        ("", False),
        (None, True),  # unset -- default is on
    ],
)
def test_telemetry_default_construction_honors_env_var(monkeypatch, env_value, expected_enabled):
    """TelemetryCollector() with no args -- exactly how the module-level
    TELEMETRY singleton every conform pass writes to is constructed --
    must actually respect DIMENSION_TELEMETRY. Previously `enabled`
    defaulted to True and `True or bool(env == "1")` is always True, so
    the env var could never disable it."""
    if env_value is None:
        monkeypatch.delenv("DIMENSION_TELEMETRY", raising=False)
    else:
        monkeypatch.setenv("DIMENSION_TELEMETRY", env_value)

    collector = TelemetryCollector()
    assert collector.enabled is expected_enabled


def test_telemetry_explicit_enabled_overrides_env_var(monkeypatch):
    monkeypatch.setenv("DIMENSION_TELEMETRY", "0")
    assert TelemetryCollector(enabled=True).enabled is True

    monkeypatch.setenv("DIMENSION_TELEMETRY", "1")
    assert TelemetryCollector(enabled=False).enabled is False


def test_telemetry_collector_span_and_metrics(tmp_path: Path):
    collector = TelemetryCollector()
    collector.reset()
    collector.start_profiling()

    with collector.span("test_stage", phase="scrape", layer_count=5):
        # allocate small object to check memory
        _data = [i * 2 for i in range(1000)]

    collector.record_layer(
        uid="u_101",
        name="Header Layer",
        tag="TOP",
        layer_kind="text",
        src_pos=[960, 200, 0],
        src_scale=[100, 100, 100],
        dst_pos=[540, 200, 0],
        dst_scale=[56.25, 56.25, 100],
    )

    collector.stop_profiling()
    summary = collector.get_summary()

    assert summary["total_layers"] == 1
    assert "scrape" in summary["phase_breakdown"]
    assert summary["phase_breakdown"]["scrape"]["count"] == 1
    assert summary["layer_count_by_tag"].get("TOP") == 1

    # Verify global TELEMETRY instance functions as well
    TELEMETRY.reset()
    with TELEMETRY.span("global_test"):
        pass
    assert len(TELEMETRY.spans) == 1

    export_file = tmp_path / "telemetry_out.json"
    exported_path = collector.export_json(export_file)
    assert Path(exported_path).exists()

    with open(export_file, "r") as f:
        data = json.load(f)
    assert "summary" in data
    assert len(data["spans"]) == 1
    assert len(data["layer_metrics"]) == 1


def test_generate_diagnostic_html(tmp_path: Path):
    src_man = {
        "project_info": {"name": "Test Comp", "width": 1920, "height": 1080},
        "layers": [
            {
                "index": 1,
                "name": "Title",
                "uid": "t_1",
                "content_tag": "TOP",
                "position": [960, 200, 0],
                "scale": [100, 100, 100],
                "source_rect": [0, 0, 500, 100],
            }
        ]
    }

    conf_dict = {
        "target_width": 1080,
        "target_height": 1920,
        "scale_mode": "Auto",
        "aspect_strategy": "narrow",
        "layers": [
            {
                "index": 1,
                "name": "Title",
                "uid": "t_1",
                "content_tag": "TOP",
                "conformed_transforms": {
                    "position": [540, 200, 0],
                    "scale": [56.25, 56.25, 100],
                },
                "soe_correction": {"nudge_px": [0, -15.0]},
            }
        ]
    }

    html_out = tmp_path / "diagnostic.html"
    generated_path = generate_diagnostic_html(
        manifest_source=src_man,
        manifest_conformed=conf_dict,
        telemetry_data={"summary": {"total_duration_ms": 5.2, "throughput_layers_per_sec": 10000}},
        harness_results=[{"category": "MATH", "test": "Ribbon Scale Invariant", "passed": True}],
        target_path=html_out,
    )

    assert Path(generated_path).exists()
    content = html_out.read_text(encoding="utf-8")
    assert "DIMENSION" in content
    assert "DIAGNOSTIC TELEMETRY" in content
    assert "Title" in content
    assert "TOP" in content
    assert "Ribbon Scale Invariant" in content
    assert "PASS" in content


def test_manifest_diff_tool(tmp_path: Path):
    m1 = tmp_path / "m1.json"
    m2 = tmp_path / "m2.json"
    m1.write_text(json.dumps({"project_info": {"width": 1920}, "layers": [{"name": "A", "index": 1}]}), encoding="utf-8")
    m2.write_text(json.dumps({"project_info": {"width": 1920}, "layers": [{"name": "A", "index": 1}]}), encoding="utf-8")

    assert run_manifest_diff(str(m1), str(m2)) is True

    m3 = tmp_path / "m3.json"
    m3.write_text(json.dumps({"project_info": {"width": 1080}, "layers": [{"name": "B", "index": 1}]}), encoding="utf-8")
    assert run_manifest_diff(str(m1), str(m3)) is False
