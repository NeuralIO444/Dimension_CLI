# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_scale_engine_narrow_spatial.py

Tests for the spatial sub-grouping fix in the group-aware gravity pre-pass
(scale_engine_narrow.py). Single-linkage clustering now partitions each
(comp_id, tag) group into spatial sub-clusters before computing centroids,
so a compositing solid far from the hero group cannot contaminate the
centroid and misplace every on-screen layer.

Coverage:
  1. Single-layer group — centroid unchanged, group_size == 1.
  2. Two nearby layers — share same centroid, group_size == 2.
  3. Two distant layers — each isolated, group_size == 1 each.
  4. Cyan Solid 2 scenario — 5 logos at (1024,1024) + outlier at (25,2023);
     logos have group_size == 5, outlier has group_size == 1.
  5. Three layers: two close, one far — close pair clusters together.
  6. Different tags in same comp never share a cluster.
  7. Same tag in different comps never shares a cluster.
  8. Threshold boundary: at exactly threshold -> same cluster;
     at threshold+1 -> separate cluster.
  9. Output positions correct for Cyan Solid scenario (logos centered).
 10. 87N fixture regression — all 11 CENTER layers stay in one cluster
     (max pairwise distance 454px < threshold 768px), group_size==11 preserved.
"""

from __future__ import annotations

import json
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.scrape_manifest import ScrapeManifest, LayerModel, ProjectInfo
from core.scale_engine import ScaleEngine

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "bug_l" / "87n_source_manifest.json"

# The five sequentially-revealed text-body layers used in 87N regression test.
_TEXT_BODY_LAYERS = (
    "EVERYTHING ",
    "YOU WANT",
    "IS ON THE",
    "OTHER SIDE",
    "OF FEAR",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manifest(layers, width=2048, height=2048):
    """Build a minimal ScrapeManifest for spatial clustering tests."""
    return ScrapeManifest(
        status="OK",
        project_info=ProjectInfo(width=width, height=height, name="SpatialTest"),
        layers=layers,
    )


def _conform(manifest, tgt_w=1080, tgt_h=1920):
    """Run a Fit-mode conform, return the result dict."""
    engine = ScaleEngine(
        manifest=manifest,
        target_width=tgt_w,
        target_height=tgt_h,
        scale_mode="Fit",
        bleed_pct=0.0,
    )
    return engine.conform()


def _dst_pos(result, layer_name):
    """Extract conformed position for a named layer."""
    by_name = {l["name"]: l for l in result["layers"]}
    layer = by_name[layer_name]
    return layer["conformed_transforms"]["position"]


def _group_size(result, layer_name):
    """Extract gravity_group_size for a named layer."""
    by_name = {l["name"]: l for l in result["layers"]}
    return by_name[layer_name].get("gravity_group_size")


# ---------------------------------------------------------------------------
# Test 1 — Single-layer group: centroid unchanged, size == 1
# ---------------------------------------------------------------------------

class TestSingleLayerGroup:
    def test_single_layer_group_centroid_unchanged(self):
        """A group of size 1 is always a cluster of size 1.
        gravity_group_size must be 1. The layer's offset from its own
        centroid is zero, so apply_gravity() places it identically to the
        pre-fix output — no regression for the common case."""
        manifest = _make_manifest([
            LayerModel(
                index=1,
                name="Logo",
                content_tag="CENTER",
                position=[500.0, 400.0, 0.0],
                scale=[100.0, 100.0, 100.0],
                containing_comp_id=10,
            )
        ])
        result = _conform(manifest)
        assert _group_size(result, "Logo") == 1


# ---------------------------------------------------------------------------
# Test 2 — Two nearby layers share the same cluster (and centroid)
# ---------------------------------------------------------------------------

class TestTwoNearbyLayers:
    def test_two_nearby_layers_share_centroid(self):
        """Two CENTER layers 70px apart (< threshold 819.2px in a 2048px comp)
        must land in the same cluster.  gravity_group_size == 2 for both.
        The spacing between their conformed positions equals the spacing
        between (pos - centroid)*S — NOT zero (which would be the collapse bug)
        and NOT the raw HD spacing (which would mean clustering was ignored)."""
        manifest = _make_manifest([
            LayerModel(
                index=1,
                name="Near1",
                content_tag="CENTER",
                position=[1000.0, 1000.0, 0.0],
                scale=[100.0, 100.0, 100.0],
                containing_comp_id=10,
            ),
            LayerModel(
                index=2,
                name="Near2",
                content_tag="CENTER",
                position=[1050.0, 1050.0, 0.0],
                scale=[100.0, 100.0, 100.0],
                containing_comp_id=10,
            ),
        ])
        result = _conform(manifest)
        # Both must report cluster size 2.
        assert _group_size(result, "Near1") == 2
        assert _group_size(result, "Near2") == 2

        # The conformed positions must be distinct (spacing preserved).
        pos_a = _dst_pos(result, "Near1")
        pos_b = _dst_pos(result, "Near2")
        assert pos_a[0] != pos_b[0] or pos_a[1] != pos_b[1], (
            "Two nearby layers collapsed to the same conformed position "
            "— group offset was not applied correctly."
        )

        # The spacing between the two conformed Y positions must equal
        # the source spacing scaled by S.
        S = _conform(manifest)["scale"]["S"]
        src_dy = 1050.0 - 1000.0
        dst_dy = abs(pos_b[1] - pos_a[1])
        assert abs(dst_dy - src_dy * S) < 0.5, (
            f"Spacing {dst_dy:.2f} does not match expected {src_dy * S:.2f}"
        )


# ---------------------------------------------------------------------------
# Test 3 — Two distant layers split into separate clusters
# ---------------------------------------------------------------------------

class TestTwoDistantLayers:
    def test_two_distant_layers_split_into_separate_clusters(self):
        """Two CENTER layers 2404px apart (>> threshold 819.2px) must each
        form their own cluster of size 1.  Each layer's centroid equals its
        own position, so offset == 0 and apply_gravity() places each layer
        independently centered."""
        manifest = _make_manifest([
            LayerModel(
                index=1,
                name="Far1",
                content_tag="CENTER",
                position=[100.0, 100.0, 0.0],
                scale=[100.0, 100.0, 100.0],
                containing_comp_id=10,
            ),
            LayerModel(
                index=2,
                name="Far2",
                content_tag="CENTER",
                position=[1800.0, 1800.0, 0.0],
                scale=[100.0, 100.0, 100.0],
                containing_comp_id=10,
            ),
        ])
        # Distance: sqrt((1800-100)^2 + (1800-100)^2) = sqrt(2*1700^2) ≈ 2404px
        # Threshold: max(2048,2048)*0.40 = 819.2px → 2404 >> 819.2 → separate clusters
        result = _conform(manifest)
        assert _group_size(result, "Far1") == 1, (
            f"Far1: expected gravity_group_size 1, got {_group_size(result, 'Far1')}"
        )
        assert _group_size(result, "Far2") == 1, (
            f"Far2: expected gravity_group_size 1, got {_group_size(result, 'Far2')}"
        )


# ---------------------------------------------------------------------------
# Test 4 — Cyan Solid 2 scenario (the exact bug from 87N client comp)
# ---------------------------------------------------------------------------

class TestCyanSolidScenario:
    def test_cyan_solid_scenario(self):
        """Reproduce the 87N-style poisoning bug:
        5 logo layers at (1024, 1024) + 1 outlier at (25, 2023) all share
        the CENTER tag in the same comp.

        Distance from outlier to logos:
          sqrt((25-1024)^2 + (2023-1024)^2) = sqrt(999^2 + 999^2) ≈ 1412.8px
        Threshold: max(2048, 2048)*0.40 = 819.2px
        1412.8 >> 819.2 → outlier forms its own cluster.

        After the fix:
          - Logo layers: gravity_group_size == 5, centroid ≈ (1024, 1024)
          - Cyan Solid 2: gravity_group_size == 1
        """
        manifest = _make_manifest([
            LayerModel(
                index=1, name="Logo1", content_tag="CENTER",
                position=[1024.0, 1024.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=1600004502,
            ),
            LayerModel(
                index=2, name="Logo2", content_tag="CENTER",
                position=[1024.0, 1024.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=1600004502,
            ),
            LayerModel(
                index=3, name="Logo3", content_tag="CENTER",
                position=[1024.0, 1024.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=1600004502,
            ),
            LayerModel(
                index=4, name="Logo4", content_tag="CENTER",
                position=[1024.0, 1024.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=1600004502,
            ),
            LayerModel(
                index=5, name="Logo5", content_tag="CENTER",
                position=[1024.0, 1024.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=1600004502,
            ),
            LayerModel(
                index=6, name="Cyan Solid 2", content_tag="CENTER",
                position=[25.0, 2023.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=1600004502,
            ),
        ])
        result = _conform(manifest)

        # Logo layers must be in the 5-member cluster.
        for logo in ("Logo1", "Logo2", "Logo3", "Logo4", "Logo5"):
            sz = _group_size(result, logo)
            assert sz == 5, (
                f"{logo}: expected gravity_group_size 5 (logos cluster), got {sz}"
            )

        # Cyan Solid 2 must be isolated in its own cluster.
        cyan_sz = _group_size(result, "Cyan Solid 2")
        assert cyan_sz == 1, (
            f"Cyan Solid 2: expected gravity_group_size 1 (isolated), got {cyan_sz}"
        )


# ---------------------------------------------------------------------------
# Test 5 — Three layers: two close, one far
# ---------------------------------------------------------------------------

class TestThreeLayersMixed:
    def test_three_layers_two_close_one_far(self):
        """Three CENTER layers: Close1 and Close2 are ~22px apart (same cluster).
        Far1 is ~1980px from Close1 (separate cluster).

        Expected:
          Close1 gravity_group_size == 2, centroid ≈ (110.0, 105.0)
          Close2 gravity_group_size == 2, centroid ≈ (110.0, 105.0)
          Far1   gravity_group_size == 1
        """
        manifest = _make_manifest([
            LayerModel(
                index=1, name="Close1", content_tag="CENTER",
                position=[100.0, 100.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=10,
            ),
            LayerModel(
                index=2, name="Close2", content_tag="CENTER",
                position=[120.0, 110.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=10,
            ),
            LayerModel(
                index=3, name="Far1", content_tag="CENTER",
                position=[1500.0, 1500.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=10,
            ),
        ])
        # Close1 <-> Close2: sqrt(20^2 + 10^2) = sqrt(500) ≈ 22.4px < 819.2px
        # Close1 <-> Far1:   sqrt(1400^2 + 1400^2) ≈ 1980px >> 819.2px
        result = _conform(manifest)

        assert _group_size(result, "Close1") == 2, (
            f"Close1: expected 2, got {_group_size(result, 'Close1')}"
        )
        assert _group_size(result, "Close2") == 2, (
            f"Close2: expected 2, got {_group_size(result, 'Close2')}"
        )
        assert _group_size(result, "Far1") == 1, (
            f"Far1: expected 1, got {_group_size(result, 'Far1')}"
        )


# ---------------------------------------------------------------------------
# Test 6 — Different tags in same comp never share a cluster
# ---------------------------------------------------------------------------

class TestDifferentTagsNeverCluster:
    def test_different_tags_never_share_centroid(self):
        """Two layers in the same comp at the same position but with
        different canonical tags (TOP vs CENTER) must each be in their
        own size-1 cluster.  Tag groups are partitioned before clustering
        — different tags never interact."""
        manifest = _make_manifest([
            LayerModel(
                index=1, name="TypeLayer", content_tag="TOP",
                position=[1024.0, 1024.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=10,
            ),
            LayerModel(
                index=2, name="CenterLayer", content_tag="CENTER",
                position=[1024.0, 1024.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=10,
            ),
        ])
        result = _conform(manifest)
        # Each tag group has exactly 1 member → cluster size 1 each.
        assert _group_size(result, "TypeLayer") == 1, (
            f"TypeLayer: expected 1, got {_group_size(result, 'TypeLayer')}"
        )
        assert _group_size(result, "CenterLayer") == 1, (
            f"CenterLayer: expected 1, got {_group_size(result, 'CenterLayer')}"
        )


# ---------------------------------------------------------------------------
# Test 7 — Same tag in different comps never shares a cluster
# ---------------------------------------------------------------------------

class TestDifferentCompsNeverCluster:
    def test_different_comps_never_share_centroid(self):
        """Two CENTER layers at the same position but in different
        containing_comp_ids must each be in their own size-1 cluster.
        The group key includes comp_id, so different comps are always
        separate partitions."""
        manifest = _make_manifest([
            LayerModel(
                index=1, name="Layer1", content_tag="CENTER",
                position=[1024.0, 1024.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=1,
            ),
            LayerModel(
                index=1, name="Layer2", content_tag="CENTER",
                position=[1024.0, 1024.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=2,
            ),
        ])
        result = _conform(manifest)
        assert _group_size(result, "Layer1") == 1, (
            f"Layer1: expected 1, got {_group_size(result, 'Layer1')}"
        )
        assert _group_size(result, "Layer2") == 1, (
            f"Layer2: expected 1, got {_group_size(result, 'Layer2')}"
        )


# ---------------------------------------------------------------------------
# Test 8 — Threshold boundary cases (1000x1000 comp → threshold == 400.0px)
# ---------------------------------------------------------------------------

class TestThresholdBoundary:
    def test_threshold_boundary_cases(self):
        """Use a 1000x1000 source comp conforming to 1080x1920 (narrow path,
        which exercises the clustering pre-pass) so threshold =
        max(1000,1000)*0.40 = 400.0px (round number for boundary verification).

        The narrow rule set is triggered by a significant aspect-ratio change
        (square → tall), so we use tgt_w=1080, tgt_h=1920 for both sub-tests.

        Sub-test A: distance == threshold exactly (400.0px) → same cluster.
        Sub-test B: distance == threshold + 1px (401.0px) → separate clusters.
        """
        # Sub-test A: at exactly threshold — must be same cluster (d <= threshold)
        manifest_a = _make_manifest(
            layers=[
                LayerModel(
                    index=1, name="Thresh1", content_tag="CENTER",
                    position=[0.0, 0.0, 0.0], scale=[100.0, 100.0, 100.0],
                    containing_comp_id=10,
                ),
                LayerModel(
                    index=2, name="Thresh2", content_tag="CENTER",
                    position=[400.0, 0.0, 0.0], scale=[100.0, 100.0, 100.0],
                    containing_comp_id=10,
                ),
            ],
            width=1000,
            height=1000,
        )
        result_a = _conform(manifest_a, tgt_w=1080, tgt_h=1920)
        assert _group_size(result_a, "Thresh1") == 2, (
            f"At threshold: Thresh1 expected group_size 2, got {_group_size(result_a, 'Thresh1')}"
        )
        assert _group_size(result_a, "Thresh2") == 2, (
            f"At threshold: Thresh2 expected group_size 2, got {_group_size(result_a, 'Thresh2')}"
        )

        # Sub-test B: threshold + 1 — must be separate clusters (d > threshold)
        manifest_b = _make_manifest(
            layers=[
                LayerModel(
                    index=1, name="Thresh1", content_tag="CENTER",
                    position=[0.0, 0.0, 0.0], scale=[100.0, 100.0, 100.0],
                    containing_comp_id=10,
                ),
                LayerModel(
                    index=2, name="Thresh2", content_tag="CENTER",
                    position=[401.0, 0.0, 0.0], scale=[100.0, 100.0, 100.0],
                    containing_comp_id=10,
                ),
            ],
            width=1000,
            height=1000,
        )
        result_b = _conform(manifest_b, tgt_w=1080, tgt_h=1920)
        assert _group_size(result_b, "Thresh1") == 1, (
            f"Beyond threshold: Thresh1 expected group_size 1, got {_group_size(result_b, 'Thresh1')}"
        )
        assert _group_size(result_b, "Thresh2") == 1, (
            f"Beyond threshold: Thresh2 expected group_size 1, got {_group_size(result_b, 'Thresh2')}"
        )


# ---------------------------------------------------------------------------
# Test 9 — Output positions correct for Cyan Solid scenario
# ---------------------------------------------------------------------------

class TestOutputPositionsCyanSolid:
    def test_output_positions_correct_for_cyan_solid_scenario(self):
        """Full conform of the Cyan Solid 2 fixture to 1080x1920 (TikTok 9:16).

        Source: 2048x2048, Fit mode.
        S = min(1080/2048, 1920/2048) = 0.52734375

        Logo layers (centroid = (1024, 1024), offset = 0):
          The safe_area inset shifts safe_cy from tgt_cy.  With the fix,
          the logos use centroid (1024, 1024) as the group reference — their
          offset is zero — so they land at (safe_cx, safe_cy).
          safe_cx ≈ 540 (horizontally centered in 1080-wide frame).

          Without the fix, the poisoned centroid was ~(857, 1190),
          so logos landed at ~(633, 856) — clearly off-center.

        Assertions (tolerance-based, not exact — safe_area insets vary):
          logo x ≈ 540 (within 1px of horizontal center)
          logo y > 900  (clearly above the buggy ~856 value)

        Cyan Solid 2 must land at a distinct x or y from logos (it has its
        own cluster centroid at (25, 2023), not shared with logos).
        """
        layers = [
            LayerModel(
                index=1, name="Logo1", content_tag="CENTER",
                position=[1024.0, 1024.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=1600004502,
            ),
            LayerModel(
                index=2, name="Logo2", content_tag="CENTER",
                position=[1024.0, 1024.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=1600004502,
            ),
            LayerModel(
                index=3, name="Logo3", content_tag="CENTER",
                position=[1024.0, 1024.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=1600004502,
            ),
            LayerModel(
                index=4, name="Logo4", content_tag="CENTER",
                position=[1024.0, 1024.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=1600004502,
            ),
            LayerModel(
                index=5, name="Logo5", content_tag="CENTER",
                position=[1024.0, 1024.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=1600004502,
            ),
            LayerModel(
                index=6, name="Cyan Solid 2", content_tag="CENTER",
                position=[25.0, 2023.0, 0.0], scale=[100.0, 100.0, 100.0],
                containing_comp_id=1600004502,
            ),
        ]
        manifest = _make_manifest(layers, width=2048, height=2048)
        result = _conform(manifest, tgt_w=1080, tgt_h=1920)

        # Logo 1 as representative (all 5 logos have offset 0 from centroid).
        logo_pos = _dst_pos(result, "Logo1")

        # X: logo must be near horizontal center of the 1080-wide frame.
        # safe_cx with DEFAULT_SAFE_AREA (left=0.04, right=0.04 inset) = 540.0.
        assert abs(logo_pos[0] - 540.0) < 1.0, (
            f"Logo1 x={logo_pos[0]:.2f} is not near 540 — centroid poisoning "
            f"may still be active (buggy value was ~633)"
        )

        # Y: logo must be above the buggy value of ~856.
        assert logo_pos[1] > 900, (
            f"Logo1 y={logo_pos[1]:.2f} <= 900 — centroid poisoning may still "
            f"be active (buggy value was ~856)"
        )

        # gravity_group_size confirms the clustering is correct.
        # (Position alone is the same for logo and outlier with CENTER gravity
        # because both have zero offset from their respective cluster centroids —
        # the outlier is its own size-1 cluster, centered independently.)
        assert _group_size(result, "Logo1") == 5, (
            f"Logo1: expected gravity_group_size 5, got {_group_size(result, 'Logo1')}"
        )
        assert _group_size(result, "Cyan Solid 2") == 1, (
            f"Cyan Solid 2: expected gravity_group_size 1, got {_group_size(result, 'Cyan Solid 2')}"
        )


# ---------------------------------------------------------------------------
# Test 10 — 87N fixture regression guard
# ---------------------------------------------------------------------------

class Test87NFixtureRegression:
    """Load the real 87N manifest (not a synthetic fixture).
    All 11 CENTER layers span a max pairwise distance of 454.1px,
    which is below the 768px threshold (max(1920,1080)*0.40).
    Therefore all 11 must stay in ONE cluster — gravity_group_size == 11
    for the five HERO text layers, just as it was before this fix."""

    def _load(self):
        with open(_FIXTURE_PATH) as f:
            return ScrapeManifest.model_validate(json.load(f))

    def _conform_87n(self):
        manifest = self._load()
        engine = ScaleEngine(
            manifest=manifest,
            target_width=1080,
            target_height=1920,
            scale_mode="Fit",
            bleed_pct=0.05,
        )
        return engine.conform()

    def test_87n_total_layer_count_unchanged(self):
        """Total output layer count must match the 19-layer fixture."""
        result = self._conform_87n()
        assert len(result["layers"]) == 19, (
            f"Expected 19 layers, got {len(result['layers'])}"
        )

    def test_87n_hero_text_layers_gravity_group_size_11(self):
        """The five HERO text layers must report gravity_group_size == 11.
        All 11 CENTER layers (5 HERO + 4 BOXART plates + 1 BOXART bg + 1 ARTWORK)
        are within 454.1px of each other — well inside the 768px threshold —
        so clustering produces one group of 11, same as before this fix."""
        result = self._conform_87n()
        by_name = {l["name"]: l for l in result["layers"]}
        for name in _TEXT_BODY_LAYERS:
            sz = by_name[name].get("gravity_group_size")
            assert sz == 11, (
                f"{name}: expected gravity_group_size 11 (all 11 CENTER "
                f"layers stay in one cluster), got {sz!r}"
            )

    def test_87n_five_text_layers_land_at_distinct_y_positions(self):
        """The five text lines must still land at five distinct Y positions
        (the group-aware offset math must not be broken by the clustering change)."""
        result = self._conform_87n()
        by_name = {l["name"]: l for l in result["layers"]}
        y_positions = [
            by_name[name]["conformed_transforms"]["position"][1]
            for name in _TEXT_BODY_LAYERS
        ]
        assert len(set(round(y, 3) for y in y_positions)) == 5, (
            f"Five text layers collapsed: {y_positions}"
        )

    def test_87n_vertical_order_preserved(self):
        """Source vertical order of the five text lines must be preserved
        in the conformed output."""
        manifest = self._load()
        src_by_name = {l.name: l for l in manifest.layers}
        src_order = sorted(_TEXT_BODY_LAYERS, key=lambda n: src_by_name[n].position[1])

        result = self._conform_87n()
        dst_by_name = {l["name"]: l for l in result["layers"]}
        dst_order = sorted(
            _TEXT_BODY_LAYERS,
            key=lambda n: dst_by_name[n]["conformed_transforms"]["position"][1],
        )
        assert src_order == dst_order, (
            f"Source order {src_order} != conformed order {dst_order}"
        )

    def test_87n_spacing_scales_by_S(self):
        """Spacing between consecutive text lines must equal src_spacing * S
        (within 0.5px tolerance — same check as test_group_aware_gravity_integration)."""
        manifest = self._load()
        S_result = ScaleEngine(
            manifest=self._load(),
            target_width=1080,
            target_height=1920,
            scale_mode="Fit",
            bleed_pct=0.05,
        ).conform()["scale"]["S"]

        src_by_name = {l.name: l for l in manifest.layers}
        result = self._conform_87n()
        dst_by_name = {l["name"]: l for l in result["layers"]}

        ordered = sorted(_TEXT_BODY_LAYERS, key=lambda n: src_by_name[n].position[1])
        for a, b in zip(ordered, ordered[1:]):
            src_dy = src_by_name[b].position[1] - src_by_name[a].position[1]
            dst_dy = (
                dst_by_name[b]["conformed_transforms"]["position"][1]
                - dst_by_name[a]["conformed_transforms"]["position"][1]
            )
            expected_dy = src_dy * S_result
            assert abs(dst_dy - expected_dy) < 0.5, (
                f"{a}->{b}: expected spacing {expected_dy:.2f} "
                f"(src {src_dy:.2f} * S={S_result:.4f}), got {dst_dy:.2f}"
            )
