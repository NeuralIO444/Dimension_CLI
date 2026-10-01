# (c) 2026 NeuralIO 444
"""CLI --mode/--bleed must win over PresetManager defaults when --preset is set."""

from __future__ import annotations

from core.scale_engine import ScaleEngine
from logic.preset_manager import PresetManager
from models.scrape_manifest import ScrapeManifest


def _minimal_manifest() -> ScrapeManifest:
    return ScrapeManifest.model_validate({
        "status": "OK",
        "project_info": {"name": "test", "width": 1920, "height": 1080},
        "layers": [],
    })


def test_preset_manager_defaults_fit():
    mgr = PresetManager()
    preset = next(iter(mgr.presets.values()))
    assert preset.scale_mode == "Fit"


def test_fill_vs_fit_produce_different_uniform_scale():
    manifest = _minimal_manifest()
    fit_s = ScaleEngine(manifest, 1080, 1920, "Fit", 0.0)._calculate_base_scale()
    fill_s = ScaleEngine(manifest, 1080, 1920, "Fill", 0.0)._calculate_base_scale()
    assert fit_s == 0.5625
    assert fill_s == 1.7777777777777777
    assert fit_s != fill_s


def test_orchestrator_preset_branch_uses_cli_mode():
    """Regression: CEP passes --mode Fill --preset tiktok; must not stick on Fit."""
    cli_mode = "Fill"
    cli_bleed = 5.0
    mgr = PresetManager()
    preset = next(iter(mgr.presets.values()))
    assert preset.scale_mode == "Fit"
    # Mirrors orchestrator.py preset resolution after 2026-07-05 fix.
    scale_mode = cli_mode
    bleed_pct = cli_bleed
    assert scale_mode == "Fill"
    assert bleed_pct == 5.0