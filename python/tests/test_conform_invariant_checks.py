# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_conform_invariant_checks.py
Phase 4 of the autonomous-engineering initiative. Locks in the two things
verified by hand while building conform_invariant_checks.py, so they don't
silently regress:

1. No false positive on a real fixture that legitimately has multiple
   PlacementUnit kinds in the same comp (camera_scene AND a separate FILL
   singleton) -- an earlier version of check_sealed_unit_atomicity grouped
   by containing_comp_id alone and flagged EFX (a correctly-different-ratio
   FILL layer) as an atomicity violation against the camera scene it merely
   shares a comp with, not a unit with.
2. The check DOES fire on the real historical numbers from #365 (the
   Z-depth Scene Preservation Guard bug) -- proving it has actual detection
   power, not just "never fails."
"""

from __future__ import annotations

import json
import os

from core.conform_invariant_checks import check_sealed_unit_atomicity
from core.placement_units import PlacementUnit, PlacementUnitsReport, UnitMember
from core.scale_engine import ScaleEngine
from models.scrape_manifest import ScrapeManifest

_87N_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "bug_l", "87n_source_manifest.json"
)


def test_no_false_positive_on_87n_camera_scene_plus_fill_singleton():
    """The 87N fixture's camera_scene unit (7 members) and its EFX FILL
    singleton live in the same comp but are different units with correctly
    different scale ratios -- must not be flagged."""
    with open(_87N_FIXTURE, "r", encoding="utf-8") as f:
        manifest = ScrapeManifest.model_validate(json.load(f))

    engine = ScaleEngine(manifest, 1080, 1920, scale_mode="Fill", bleed_pct=0.05, layout="auto")
    result = engine.conform()

    report = check_sealed_unit_atomicity(engine, result, "87n-no-false-positive")
    assert report.ok, f"unexpected violations: {report.violations}"


def test_catches_historical_z_depth_divergence_bug_365():
    """Replays the real pre-fix numbers from #365: camera Z scaled by
    0.590625x (correctly guarded) while a content layer in the SAME
    camera_scene unit scaled Z by 1.866667x (unguarded -- the actual bug).
    Scale ratios were already uniform pre-fix; only Z-position diverged,
    which is exactly why this check tracks Z-position ratio separately
    from scale ratio."""
    unit = PlacementUnit(
        unit_id="scene:1600004739", kind="camera_scene", label="test",
        members=[
            UnitMember(comp_id=1600004739, index=1, name="Camera 1"),
            UnitMember(comp_id=1600004739, index=4, name="EVERYTHING  Outlines"),
        ],
    )

    class _FakeEngine:
        placement_units_report = PlacementUnitsReport(units=[unit])

    result = {
        "layers": [
            {
                "containing_comp_id": 1600004739, "index": 1, "name": "Camera 1",
                "position": [960.0, 540.0, -366.667], "scale": [100.0, 100.0, 100.0],
                "conformed_transforms": {"position": [540.0, 960.0, -216.562], "scale": [59.0625, 59.0625, 59.0625]},
            },
            {
                "containing_comp_id": 1600004739, "index": 4, "name": "EVERYTHING  Outlines",
                "position": [946.968, 533.942, -774.0], "scale": [100.0, 100.0, 100.0],
                "conformed_transforms": {"position": [532.303, 956.422, -1444.8], "scale": [59.0625, 59.0625, 59.0625]},
            },
        ]
    }

    report = check_sealed_unit_atomicity(_FakeEngine(), result, "365-replay")
    assert not report.ok, "check should have caught the real historical Z-depth divergence"
    assert any("Z-position" in str(v) for v in report.violations)
