# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_artwork_boundaries.py
Comprehensive test suite for Layer Archetypes & Artwork Boundary Engine (PR 4).
"""

import pytest

from core.alpha_hull import (
    classify_layer_archetype,
    compute_alpha_bounds,
    compute_composite_precomp_hull,
    compute_edge_aware_offset,
    compute_vector_bounds,
)
from models.scrape_manifest import (
    ArtworkBounds,
    EffectRecord,
    LayerArchetype,
    LayerModel,
    MaskRecord,
    SourceItem,
    TrackMatte,
    TypographicInfo,
)


class TestLayerArchetypeClassification:
    """1. Tests automatic classification into the 5 core layer archetypes."""

    def test_classify_text_layer_as_type(self):
        layer = LayerModel(index=1, name="Headline_Text", content_tag="TYPE")
        assert classify_layer_archetype(layer) == LayerArchetype.TYPE

    def test_classify_typography_info_as_type(self):
        layer = LayerModel(
            index=1,
            name="Title_01",
            typographic_info=TypographicInfo(font_size_pt=72.0, line_count=2, char_count=18),
        )
        assert classify_layer_archetype(layer) == LayerArchetype.TYPE

    def test_classify_3d_layer_as_three_d(self):
        layer = LayerModel(index=1, name="Card_3D", threeD=True)
        assert classify_layer_archetype(layer) == LayerArchetype.THREE_D

    def test_classify_camera_and_light_as_three_d(self):
        cam = LayerModel(index=1, name="Camera 1", layer_kind="camera")
        light = LayerModel(index=2, name="Key_Light", layer_kind="light")
        assert classify_layer_archetype(cam) == LayerArchetype.THREE_D
        assert classify_layer_archetype(light) == LayerArchetype.THREE_D

    def test_classify_comp_source_item_as_live_precomp(self):
        source = SourceItem(name="PRECOMP_LOGO", id=10, kind="comp", width=1920, height=1080)
        layer = LayerModel(index=1, name="LOGO_WRAPPER", source_item=source)
        assert classify_layer_archetype(layer) == LayerArchetype.LIVE_PRECOMP

    def test_classify_masked_layer_as_keyed_alpha(self):
        layer = LayerModel(
            index=1,
            name="Actor_Masked",
            masks=[MaskRecord(index=1, name="Mask 1", mode="add")],
        )
        assert classify_layer_archetype(layer) == LayerArchetype.KEYED_ALPHA

    def test_classify_track_matte_layer_as_keyed_alpha(self):
        layer = LayerModel(
            index=1,
            name="Cutout_Matte",
            track_matte=TrackMatte(mode="alpha", layer_index=2, layer_name="Matte_Layer"),
        )
        assert classify_layer_archetype(layer) == LayerArchetype.KEYED_ALPHA

    def test_classify_keylight_effect_as_keyed_alpha(self):
        layer = LayerModel(
            index=1,
            name="GreenScreen_Plate",
            effects=[EffectRecord(index=1, match_name="ADBE Keylight", display_name="Keylight", enabled=True)],
        )
        assert classify_layer_archetype(layer) == LayerArchetype.KEYED_ALPHA

    def test_classify_shape_layer_as_vector_2d(self):
        layer = LayerModel(index=1, name="Shape_Layer_1")
        assert classify_layer_archetype(layer) == LayerArchetype.VECTOR_2D

    def test_classify_illustrator_asset_as_vector_2d(self):
        source = SourceItem(name="logo.ai", id=11, kind="footage", file_path="/assets/logo.ai", is_vector=True)
        layer = LayerModel(index=1, name="Agency_Logo.ai", source_item=source, collapseTransformations=True)
        assert classify_layer_archetype(layer) == LayerArchetype.VECTOR_2D


class TestAlphaHullComputation:
    """2. Tests pixel-accurate visual bounding box and center of mass extraction."""

    def test_alpha_hull_empty_array_returns_zero_bounds(self):
        data = [[0.0 for _ in range(100)] for _ in range(100)]
        bounds = compute_alpha_bounds(data)
        assert bounds.width == 0.0
        assert bounds.height == 0.0
        assert bounds.centroid is None

    def test_alpha_hull_fully_opaque_square(self):
        # 100x100 canvas, 20x20 opaque square at x=[40..59], y=[40..59]
        data = [[0.0 for _ in range(100)] for _ in range(100)]
        for y in range(40, 60):
            for x in range(40, 60):
                data[y][x] = 1.0

        bounds = compute_alpha_bounds(data)
        assert bounds.left == 40.0
        assert bounds.top == 40.0
        assert bounds.width == 20.0
        assert bounds.height == 20.0
        assert bounds.centroid[0] == pytest.approx(49.5, abs=0.5)
        assert bounds.centroid[1] == pytest.approx(49.5, abs=0.5)

    def test_alpha_hull_centered_circle_centroid(self):
        # 200x200 canvas with circular disc centered at (100, 100) radius 30
        data = [[0.0 for _ in range(200)] for _ in range(200)]
        for y in range(200):
            for x in range(200):
                if (x - 100) ** 2 + (y - 100) ** 2 <= 30 ** 2:
                    data[y][x] = 255.0

        bounds = compute_alpha_bounds(data)
        assert bounds.left == pytest.approx(70.0, abs=1.0)
        assert bounds.top == pytest.approx(70.0, abs=1.0)
        assert bounds.width == pytest.approx(61.0, abs=1.0)
        assert bounds.height == pytest.approx(61.0, abs=1.0)
        assert bounds.centroid[0] == pytest.approx(100.0, abs=0.5)
        assert bounds.centroid[1] == pytest.approx(100.0, abs=0.5)

    def test_alpha_hull_offset_corner_logo(self):
        # Small logo placed at bottom-left on an oversized 1920x1080 canvas (represented as 192x108)
        data = [[0.0 for _ in range(192)] for _ in range(108)]
        # Logo at x=[10..30], y=[80..100]
        for y in range(80, 101):
            for x in range(10, 31):
                data[y][x] = 1.0

        bounds = compute_alpha_bounds(data, upscale_factor=10.0)
        assert bounds.left == 100.0
        assert bounds.top == 800.0
        assert bounds.width == 210.0
        assert bounds.height == 210.0
        assert bounds.centroid[0] == pytest.approx(200.0, abs=5.0)
        assert bounds.centroid[1] == pytest.approx(900.0, abs=5.0)

    def test_alpha_hull_noise_threshold_rejection(self):
        # Low noise alpha values (e.g. 2/255) rejected by default threshold
        data = [[0.0 for _ in range(50)] for _ in range(50)]
        # Background dust/noise
        data[5][5] = 2.0 / 255.0
        data[45][45] = 3.0 / 255.0
        # Real artwork at x=[20..30], y=[20..30] with alpha=1.0
        for y in range(20, 30):
            for x in range(20, 30):
                data[y][x] = 1.0

        bounds = compute_alpha_bounds(data, threshold=5.0 / 255.0)
        assert bounds.left == 20.0
        assert bounds.top == 20.0
        assert bounds.width == 10.0
        assert bounds.height == 10.0


class TestVectorAndMaskBounds:
    """3. Tests Axis-Aligned Bounding Box (AABB) extraction from vector/mask vertices."""

    def test_vector_bounds_from_polygon_vertices(self):
        # Polygon with 4 vertices
        pts = [(100.0, 50.0), (300.0, 50.0), (250.0, 200.0), (80.0, 180.0)]
        bounds = compute_vector_bounds(pts)
        assert bounds.left == 80.0
        assert bounds.top == 50.0
        assert bounds.width == 220.0  # 300 - 80
        assert bounds.height == 150.0  # 200 - 50
        assert bounds.centroid[0] == (100 + 300 + 250 + 80) / 4.0
        assert bounds.centroid[1] == (50 + 50 + 200 + 180) / 4.0

    def test_vector_bounds_ignores_nan_and_inf_vertices(self):
        pts = [(10.0, 10.0), (float("nan"), 20.0), (float("inf"), float("-inf")), (50.0, 60.0)]
        bounds = compute_vector_bounds(pts)
        assert bounds.left == 10.0
        assert bounds.top == 10.0
        assert bounds.width == 40.0
        assert bounds.height == 50.0
        assert bounds.centroid == [30.0, 35.0]

    def test_vector_bounds_empty_list_returns_zero_bounds(self):
        bounds = compute_vector_bounds([])
        assert bounds.width == 0.0
        assert bounds.height == 0.0
        assert bounds.centroid is None


class TestEdgeAwareGravityOffset:
    """4. Tests edge-aware offset calculations for aligning artwork contours."""

    def test_edge_aware_offset_pin_top_aligns_art_edge(self):
        # 1920x1080 canvas, logo art at y=400 (height 100), safe area top=108
        bounds = ArtworkBounds(left=860.0, top=400.0, width=200.0, height=100.0, centroid=[960.0, 450.0])
        safe_rect = (96.0, 108.0, 1728.0, 864.0)
        dx, dy = compute_edge_aware_offset(bounds, canvas_w=1920, canvas_h=1080, safe_rect=safe_rect, gravity="top")
        # To align top edge (y=400) to top of canvas, offset dy = -400
        assert dy == -400.0
        # Optical centering dx = 1920/2 - 960 = 0.0
        assert dx == 0.0

    def test_edge_aware_offset_pin_bottom_aligns_art_edge(self):
        # 1920x1080 canvas, legal disclaimer at y=200..300, bottom=300
        bounds = ArtworkBounds(left=460.0, top=200.0, width=1000.0, height=100.0, centroid=[960.0, 250.0])
        safe_rect = (96.0, 108.0, 1728.0, 864.0)
        dx, dy = compute_edge_aware_offset(bounds, canvas_w=1920, canvas_h=1080, safe_rect=safe_rect, gravity="bottom")
        # To align bottom edge (y=300) to canvas bottom (1080), dy = 1080 - 300 = +780
        assert dy == 780.0

    def test_edge_aware_offset_pin_left_and_right(self):
        # Logo at left=300 (width 150, right=450)
        bounds = ArtworkBounds(left=300.0, top=490.0, width=150.0, height=100.0, centroid=[375.0, 540.0])
        safe_rect = (96.0, 108.0, 1728.0, 864.0)

        dx_l, _ = compute_edge_aware_offset(bounds, canvas_w=1920, canvas_h=1080, safe_rect=safe_rect, gravity="leftMid")
        assert dx_l == -300.0

        dx_r, _ = compute_edge_aware_offset(bounds, canvas_w=1920, canvas_h=1080, safe_rect=safe_rect, gravity="rightMid")
        assert dx_r == 1920 - 450.0

    def test_edge_aware_offset_optical_center_aligns_centroid(self):
        # Asymmetrical artwork with optical centroid at (700, 400) on 1920x1080 canvas
        bounds = ArtworkBounds(left=500.0, top=300.0, width=500.0, height=300.0, centroid=[700.0, 400.0])
        safe_rect = (96.0, 108.0, 1728.0, 864.0)
        dx, dy = compute_edge_aware_offset(bounds, canvas_w=1920, canvas_h=1080, safe_rect=safe_rect, gravity="center")
        assert dx == 1920 / 2.0 - 700.0  # +260
        assert dy == 1080 / 2.0 - 400.0  # +140

    def test_edge_aware_offset_zero_bounds_returns_zero_offset(self):
        bounds = ArtworkBounds()
        safe_rect = (96.0, 108.0, 1728.0, 864.0)
        dx, dy = compute_edge_aware_offset(bounds, canvas_w=1920, canvas_h=1080, safe_rect=safe_rect, gravity="top")
        assert dx == 0.0
        assert dy == 0.0


class TestCompositePrecompHull:
    """5. Tests unified convex visual hull computation for nested precomps."""

    def test_composite_precomp_hull_single_child(self):
        child = ArtworkBounds(left=100.0, top=200.0, width=300.0, height=400.0)
        comp_hull = compute_composite_precomp_hull([child])
        assert comp_hull.left == 100.0
        assert comp_hull.top == 200.0
        assert comp_hull.width == 300.0
        assert comp_hull.height == 400.0

    def test_composite_precomp_hull_multiple_children_union(self):
        # Child 1: upper-left (50, 50, w=100, h=100) -> right=150, bottom=150
        c1 = ArtworkBounds(left=50.0, top=50.0, width=100.0, height=100.0)
        # Child 2: lower-right (400, 300, w=200, h=150) -> right=600, bottom=450
        c2 = ArtworkBounds(left=400.0, top=300.0, width=200.0, height=150.0)

        comp_hull = compute_composite_precomp_hull([c1, c2])
        assert comp_hull.left == 50.0
        assert comp_hull.top == 50.0
        assert comp_hull.width == 550.0  # 600 - 50
        assert comp_hull.height == 400.0  # 450 - 50

    def test_composite_precomp_hull_with_child_transforms(self):
        # Child at origin (0, 0, w=100, h=100) translated to pos=[500, 500] and scale=[200, 200]
        c = ArtworkBounds(left=0.0, top=0.0, width=100.0, height=100.0)
        tf = [{"position": [500.0, 500.0], "scale": [200.0, 200.0]}]

        comp_hull = compute_composite_precomp_hull([c], tf)
        assert comp_hull.left == 500.0
        assert comp_hull.top == 500.0
        assert comp_hull.width == 200.0
        assert comp_hull.height == 200.0

    def test_composite_precomp_hull_empty_children_returns_zero(self):
        comp_hull = compute_composite_precomp_hull([])
        assert comp_hull.width == 0.0
        assert comp_hull.height == 0.0
        assert comp_hull.centroid is None
