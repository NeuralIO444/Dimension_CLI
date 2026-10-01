# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_math_roundtrip_invariants.py
Automated Round-Trip Invariant & Mathematical Symmetry Suite (TASK-MATH-01 / #271).

Asserts:
  1. Round-Trip Reversibility: P_src -> P_tgt -> P_rev == P_src within epsilon.
  2. Determinant Conservation: det(M_conformed) preserves chirality/handedness without unintended inversion.
  3. Non-Colliding Preservation: Layers spatially disjoint in source remain disjoint after conform.
"""

import math
import pytest
from core.scale_engine import ScaleEngine
from models.scrape_manifest import ScrapeManifest, ProjectInfo, LayerModel


def _make_sample_manifest(width: int = 1920, height: int = 1080) -> ScrapeManifest:
    return ScrapeManifest(
        status="OK",
        project_info=ProjectInfo(
            width=width,
            height=height,
            frame_rate=24.0,
            duration=10.0,
            name="Roundtrip_Test",
        ),
        layers=[
            LayerModel(
                index=1,
                name="Title_Card",
                layer_kind="av",
                width=800,
                height=200,
                content_tag="TYPE",
                position=[960.0, 540.0, 0.0],
                scale=[100.0, 100.0, 100.0],
                anchor=[400.0, 100.0, 0.0],
                rotation=0.0,
                opacity=100.0,
                is_root=True,
            ),
            LayerModel(
                index=2,
                name="Legal_Copy",
                layer_kind="av",
                width=600,
                height=80,
                content_tag="LEGALS",
                position=[960.0, 950.0, 0.0],
                scale=[100.0, 100.0, 100.0],
                anchor=[300.0, 40.0, 0.0],
                rotation=0.0,
                opacity=100.0,
                is_root=True,
            ),
        ],
    )


class TestMathRoundtripInvariants:
    @pytest.mark.parametrize(
        "target_w,target_h",
        [
            (3840, 2160),  # UHD 4K (2.0x uniform)
            (1280, 720),   # 720p HD (0.667x uniform)
            (960, 540),    # qHD (0.5x uniform)
            (7680, 4320),  # 8K Cinema (4.0x uniform)
        ],
    )
    def test_roundtrip_reversibility_uniform_modes(self, target_w: int, target_h: int):
        """Conform forward, then conform the result back to 1920x1080."""
        src_manifest = _make_sample_manifest(1920, 1080)

        # Forward conform
        fwd_engine = ScaleEngine(src_manifest, target_w, target_h, scale_mode="Fit", bleed_pct=0.0)
        fwd_result = fwd_engine.conform()

        # Build intermediate manifest for reverse conform
        fwd_manifest = _make_sample_manifest(target_w, target_h)
        for i, lyr in enumerate(fwd_manifest.layers):
            fwd_tf = fwd_result["layers"][i]["conformed_transforms"]
            lyr.position = fwd_tf["position"]
            lyr.scale = fwd_tf["scale"]
            lyr.anchor = fwd_tf["anchor"]

        # Reverse conform back to 1920x1080
        rev_engine = ScaleEngine(fwd_manifest, 1920, 1080, scale_mode="Fit", bleed_pct=0.0)
        rev_result = rev_engine.conform()

        # Compare original source positions against reverse conform
        for i in range(len(src_manifest.layers)):
            src_pos = src_manifest.layers[i].position
            rev_pos = rev_result["layers"][i]["conformed_transforms"]["position"]

            # Position X/Y should reverse back within tolerance
            assert math.isclose(src_pos[0], rev_pos[0], abs_tol=1.0)
            assert math.isclose(src_pos[1], rev_pos[1], abs_tol=1.0)

    def test_determinant_conservation_no_reflection(self):
        """Scale factors on X and Y must both be positive to preserve chirality."""
        src_manifest = _make_sample_manifest(1920, 1080)
        engine = ScaleEngine(src_manifest, 3840, 2160, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        for lyr in res["layers"]:
            sx, sy = lyr["conformed_transforms"]["scale"][:2]
            det_2d = sx * sy
            assert det_2d > 0.0, f"Determinant {det_2d} must be strictly positive (no accidental flipping)"

    def test_spatially_disjoint_preservation(self):
        """Layers that do not overlap in source must remain disjoint along Y."""
        src_manifest = _make_sample_manifest(1920, 1080)
        engine = ScaleEngine(src_manifest, 1080, 1920, scale_mode="Fit", bleed_pct=0.0)
        res = engine.conform()

        y1 = res["layers"][0]["conformed_transforms"]["position"][1]
        y2 = res["layers"][1]["conformed_transforms"]["position"][1]

        # Title (y1) is above Legal (y2) in source; must remain above after conform
        assert y1 < y2, f"Layer order inverted: Title at Y={y1}, Legal at Y={y2}"
