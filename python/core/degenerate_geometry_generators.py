# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/degenerate_geometry_generators.py
Phase 4 of the autonomous-engineering initiative (2026-09-02) -- deterministic
degenerate-geometry generators, keyed by "dimension" name, for
python/tools/pr_diff_harness.py to run against a diff's touched files.

Distinct from Phase 5's planned property-based/random fuzzing (needs the
`hypothesis` dependency, not yet approved): these are bounded, deterministic
parameter sweeps over the SAME kind of extreme values the existing 4
Hall of Horrors tests use by hand (zero/microscopic/giant scale, gimbal-lock
camera angles, identical coordinates) -- just generated systematically
across many combinations instead of 4 hand-picked cases, and always paired
with the universal invariant battery in conform_invariant_checks.py, never
an invented "expected value" for the made-up geometry.

Each generator yields (label, ScrapeManifest) tuples. Labels are stable and
descriptive so a failing case can be reproduced and promoted straight into
a permanent Hall of Horrors test (see that file's growing-suite convention).
"""

from __future__ import annotations

from typing import Iterator, Tuple

from models.scrape_manifest import CameraProperties, LayerModel, ProjectInfo, ScrapeManifest

_PROJECT = ProjectInfo(width=1920, height=1080, frame_rate=24.0, duration=10.0, name="degenerate-sweep")

# Values worth sweeping, chosen to match the extremes the existing 4 hand-
# written Hall of Horrors tests already probe individually.
_SCALE_EXTREMES = (0.0, 0.0001, 1.0, 100.0, 1000.0, 1e5)
_ROTATION_ANGLES = (0.0, 45.0, 89.9, 90.0, 90.1, 179.9, 180.0, 270.0, 359.9)
_DEPTH_EXTREMES = (-1e6, -1500.0, -1.0, 0.0, 1.0, 1500.0, 1e6)


def _manifest(layers) -> ScrapeManifest:
    return ScrapeManifest(status="OK", project_info=_PROJECT, layers=list(layers))


def zero_and_extreme_scale() -> Iterator[Tuple[str, ScrapeManifest]]:
    """Single-layer scale sweep across zero/microscopic/giant values --
    generalizes test_hall_of_horrors_math.py's hand-written
    test_black_hole_zero_scale_layer and test_microscopic_and_giant_layer_dimensions
    into a full parameter sweep."""
    for s in _SCALE_EXTREMES:
        yield (
            f"scale_extreme_{s}",
            _manifest([
                LayerModel(
                    index=1, name="ScaleSweep", layer_kind="av",
                    position=[960.0, 540.0, 0.0],
                    scale=[s, s, 100.0],
                    is_root=True,
                ),
            ]),
        )


def camera_scene_depth_sweep() -> Iterator[Tuple[str, ScrapeManifest]]:
    """Camera + one 3D content layer sharing a camera_scene unit, sweeping
    camera pitch x content Z-depth. This is what would have caught #365 (the
    Z-depth Scene Preservation Guard bug) systematically instead of by hand
    -- every generated case here gets run through
    conform_invariant_checks.check_sealed_unit_atomicity, which asserts
    camera and content scale/Z-position ratios stay identical."""
    for angle in _ROTATION_ANGLES:
        for depth in _DEPTH_EXTREMES:
            yield (
                f"camera_scene_pitch{angle}_depth{depth}",
                _manifest([
                    LayerModel(
                        index=1, name="Camera", layer_kind="camera",
                        position=[960.0, 540.0, -1500.0],
                        rotation_x=angle,
                        orientation=[angle, 0.0, 0.0],
                        camera=CameraProperties(
                            zoom=1500.0, focusDistance=1500.0,
                            pointOfInterest=[960.0, 540.0, 0.0],
                        ),
                        is_root=True,
                    ),
                    LayerModel(
                        index=2, name="Content3D", layer_kind="av",
                        position=[960.0, 540.0, depth],
                        scale=[100.0, 100.0, 100.0],
                        threeD=True,
                        is_root=True,
                    ),
                ]),
            )


def multi_member_camera_scene() -> Iterator[Tuple[str, ScrapeManifest]]:
    """Camera + N content layers at varying depths, all in the same scene --
    exercises the atomicity check across more than 2 members (the real #365
    bug involved 7), sweeping member count."""
    for n in (2, 3, 7, 20):
        layers = [
            LayerModel(
                index=1, name="Camera", layer_kind="camera",
                position=[960.0, 540.0, -1500.0],
                camera=CameraProperties(
                    zoom=1500.0, focusDistance=1500.0,
                    pointOfInterest=[960.0, 540.0, 0.0],
                ),
                is_root=True,
            ),
        ]
        for i in range(n):
            depth = -1000.0 + (i * 250.0)
            layers.append(
                LayerModel(
                    index=2 + i, name=f"Content3D_{i}", layer_kind="av",
                    position=[960.0, 540.0, depth],
                    scale=[100.0, 100.0, 100.0],
                    threeD=True,
                    is_root=True,
                )
            )
        yield (f"multi_member_camera_scene_n{n}", _manifest(layers))


def bleed_extremes() -> Iterator[Tuple[str, ScrapeManifest]]:
    """A single layer conformed under bleed_pct extremes -- bleed_pct itself
    is a conform-time parameter, not a manifest property, so this generator
    yields the manifest once; python/tools/pr_diff_harness.py sweeps
    bleed_pct at the ScaleEngine construction call site for this dimension."""
    yield (
        "bleed_sweep_base_layer",
        _manifest([
            LayerModel(
                index=1, name="BleedSweep", layer_kind="av",
                position=[960.0, 540.0, 0.0],
                scale=[100.0, 100.0, 100.0],
                is_root=True,
            ),
        ]),
    )


def sealed_precomp_depth() -> Iterator[Tuple[str, ScrapeManifest]]:
    """Nested precomp wrapper layers at 1-3 levels deep, each level's inner
    layer at an extreme source scale -- exercises the sealed-precomp
    pass-through contract (internals untouched, wrapper carries the whole
    transform) at varying nesting depth."""
    for depth in (1, 2, 3):
        layers = [
            LayerModel(
                index=1, name="RootWrapper", layer_kind="av",
                position=[960.0, 540.0, 0.0],
                scale=[100.0, 100.0, 100.0],
                is_root=True,
                containing_comp_id=0,
            ),
        ]
        for level in range(depth):
            comp_id = level + 1
            layers.append(
                LayerModel(
                    index=1, name=f"NestedLayer_{level}", layer_kind="av",
                    position=[960.0, 540.0, 0.0],
                    scale=[1e-3 if level % 2 == 0 else 1e4, 100.0, 100.0],
                    is_root=False,
                    containing_comp_id=comp_id,
                )
            )
        yield (f"sealed_precomp_depth{depth}", _manifest(layers))


# Dimension registry -- the single source of truth pr_diff_harness.py reads.
# Keep names stable; they're referenced by the file->dimension map there.
GENERATORS = {
    "scale_extremes": zero_and_extreme_scale,
    "camera_scenes": camera_scene_depth_sweep,
    "multi_member_camera_scenes": multi_member_camera_scene,
    "bleed_extremes": bleed_extremes,
    "sealed_precomp_nesting": sealed_precomp_depth,
}
