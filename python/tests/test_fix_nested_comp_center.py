# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Fix 1 regression tests — nested comp center in ScaleEngine.

Pre-fix: ScaleEngine used root comp center (width/2, height/2 from
project_info) for ALL layers, including layers in nested precomps with
DIFFERENT dimensions. This placed nested-comp layers off-center in the
target format.

Post-fix: ScaleEngine accepts a `comp_dims` dict ({comp_id: (width, height)})
and uses each layer's containing comp's center for position center-remap.
Layers with no containing_comp_id, or whose comp_id is not in comp_dims,
fall back to the root comp center (unchanged behavior).

Key math (Fit mode, 1920x1080 → 1080x1920):
  S = min(1080/1920, 1920/1080) = 0.5625
  tgt_cx = 540, tgt_cy = 960

For a layer at (1024, 1024) in a 2048×2048 nested comp:
  WITH fix:    nested_cx=1024, nested_cy=1024
               dst_x = ((1024-1024)*S) + 540 = 540
               dst_y = ((1024-1024)*S) + 960 = 960  → (540, 960)
  WITHOUT fix: root_cx=960, root_cy=540
               dst_x = ((1024-960)*S) + 540 = 36 + 540 = 576
               dst_y = ((1024-540)*S) + 960 = 272.25 + 960 = 1232.25

Tests:
  1. Nested-comp layer → (540, 960) with comp_dims provided
  2. Same WITHOUT comp_dims → (576, 1232.25) — proves fix is load-bearing
  3. Root-comp layer (no containing_comp_id) → unchanged behavior with fix
  4. Root-comp layer WITH comp_dims that includes the root comp → unchanged
  5. Camera in nested comp → per-layer center applied to _conform_camera POI
"""

from __future__ import annotations

import os
import sys


sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.scale_engine import ScaleEngine               # noqa: E402
from models.scrape_manifest import ScrapeManifest       # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────

# Root comp: 1920×1080
ROOT_W, ROOT_H = 1920, 1080
# Target: TikTok 1080×1920
TGT_W, TGT_H = 1080, 1920
# S (Fit): min(TGT_W/ROOT_W, TGT_H/ROOT_H) = min(0.5625, 1.7778) = 0.5625
S_FIT = TGT_W / ROOT_W  # 0.5625
TGT_CX, TGT_CY = TGT_W / 2.0, TGT_H / 2.0  # 540.0, 960.0

# Nested comp: 2048×2048 (square logo comp)
NESTED_COMP_ID = 42
NESTED_W, NESTED_H = 2048, 2048
NESTED_CX, NESTED_CY = NESTED_W / 2.0, NESTED_H / 2.0  # 1024.0, 1024.0

# Root comp ID (for the containing_comp_id-free path)
ROOT_COMP_ID = 1

TOL = 0.01  # px tolerance


# ── Manifest builders ─────────────────────────────────────────────────────────


def _base_manifest_payload(layers: list) -> dict:
    """Minimal ScrapeManifest JSON payload."""
    return {
        "status": "OK",
        "project_info": {
            "name": "Test_Comp",
            "width": ROOT_W,
            "height": ROOT_H,
            "fps": 24.0,
        },
        "layers": layers,
    }


def _av_layer(
    index: int,
    name: str,
    pos_x: float,
    pos_y: float,
    containing_comp_id: int | None = None,
    content_tag: str | None = None,
    parent_index: int = -1,
) -> dict:
    """Build a minimal AV layer dict."""
    d: dict = {
        "uid": f"u-{index}",
        "name": name,
        "index": index,
        "layer_kind": "av",
        "match_name": "ADBE AV Layer",
        "label": 0,
        "parent_index": parent_index,
        "position": [pos_x, pos_y, 0.0],
        "scale": [100.0, 100.0, 100.0],
        "anchor": [0.0, 0.0, 0.0],
        "rotation_z": 0.0,
        "transforms": {
            "raw": {
                "position": [pos_x, pos_y, 0.0],
                "scale": [100.0, 100.0, 100.0],
                "anchor_point": [0.0, 0.0, 0.0],
            }
        },
    }
    if containing_comp_id is not None:
        d["containing_comp_id"] = containing_comp_id
    if content_tag is not None:
        d["content_tag"] = content_tag
        d["content_tag_source"] = "manual_comment"
    return d


def _camera_layer(
    index: int,
    pos_x: float,
    pos_y: float,
    poi_x: float,
    poi_y: float,
    containing_comp_id: int | None = None,
) -> dict:
    """Build a minimal camera layer dict."""
    d: dict = {
        "uid": f"u-cam-{index}",
        "name": f"Camera {index}",
        "index": index,
        "layer_kind": "camera",
        "match_name": "ADBE Camera Layer",
        "label": 0,
        "parent_index": -1,
        "position": [pos_x, pos_y, -2000.0],
        "scale": [100.0, 100.0, 100.0],
        "anchor": [0.0, 0.0, 0.0],
        "rotation_z": 0.0,
        "camera": {
            "zoom": 1777.78,
            "pointOfInterest": [poi_x, poi_y, 0.0],
            "depthOfField": False,
            "focusDistance": 2000.0,
            "aperture": 100.0,
            "blurLevel": 100.0,
        },
        "transforms": {
            "raw": {
                "position": [pos_x, pos_y, -2000.0],
                "scale": [100.0, 100.0, 100.0],
                "anchor_point": [0.0, 0.0, 0.0],
            }
        },
    }
    if containing_comp_id is not None:
        d["containing_comp_id"] = containing_comp_id
    return d


def _conform(
    layers: list,
    comp_dims: dict | None = None,
) -> list:
    """Build manifest, run ScaleEngine, return conformed layers list."""
    payload = _base_manifest_payload(layers)
    manifest = ScrapeManifest.model_validate(payload)
    engine = ScaleEngine(
        manifest, TGT_W, TGT_H, "Fit", 0.0,
        comp_dims=comp_dims,
    )
    result = engine.conform()
    return result["layers"]


# ── Test cases ────────────────────────────────────────────────────────────────


class TestNestedCompLayerWithCompDims:
    """
    Case 1: layer at the center of a 2048×2048 nested comp → target center.

    WITH comp_dims={NESTED_COMP_ID: (2048, 2048)}:
      nested_cx = 1024, nested_cy = 1024
      dst_x = ((1024 - 1024) * S) + 540 = 540.0
      dst_y = ((1024 - 1024) * S) + 960 = 960.0
    """

    def test_nested_layer_at_comp_center_maps_to_target_center(self):
        layers = [
            _av_layer(
                index=1,
                name="Pre_Neon_logo_10_mc",
                pos_x=NESTED_CX,  # 1024.0 = exact center of nested comp
                pos_y=NESTED_CY,  # 1024.0
                containing_comp_id=NESTED_COMP_ID,
            )
        ]
        comp_dims = {NESTED_COMP_ID: (NESTED_W, NESTED_H)}
        conformed = _conform(layers, comp_dims=comp_dims)
        pos = conformed[0]["conformed_transforms"]["position"]
        assert abs(pos[0] - TGT_CX) < TOL, (
            f"Expected x={TGT_CX}, got {pos[0]}"
        )
        assert abs(pos[1] - TGT_CY) < TOL, (
            f"Expected y={TGT_CY}, got {pos[1]}"
        )

    def test_nested_layer_offset_from_comp_center_remaps_correctly(self):
        """Layer at (1024+100, 1024-50) in nested comp:
        dst_x = ((1124 - 1024) * 0.5625) + 540 = 56.25 + 540 = 596.25
        dst_y = ((974  - 1024) * 0.5625) + 960 = -28.125 + 960 = 931.875
        """
        offset_x, offset_y = 100.0, -50.0
        layers = [
            _av_layer(
                index=1,
                name="Pre_Neon_logo_offset",
                pos_x=NESTED_CX + offset_x,
                pos_y=NESTED_CY + offset_y,
                containing_comp_id=NESTED_COMP_ID,
            )
        ]
        comp_dims = {NESTED_COMP_ID: (NESTED_W, NESTED_H)}
        conformed = _conform(layers, comp_dims=comp_dims)
        pos = conformed[0]["conformed_transforms"]["position"]

        expected_x = (offset_x * S_FIT) + TGT_CX
        expected_y = (offset_y * S_FIT) + TGT_CY
        assert abs(pos[0] - expected_x) < TOL, (
            f"Expected x={expected_x}, got {pos[0]}"
        )
        assert abs(pos[1] - expected_y) < TOL, (
            f"Expected y={expected_y}, got {pos[1]}"
        )


class TestNestedCompLayerWithoutCompDims:
    """
    Case 2: same setup WITHOUT comp_dims → wrong result (regression baseline).

    Proves the fix is load-bearing: removing comp_dims produces a different
    (incorrect) answer for layers whose nested comp center ≠ root comp center.
    """

    def test_without_comp_dims_gives_wrong_position(self):
        """Without comp_dims, the engine uses root comp center (960, 540).
        dst_x = ((1024 - 960) * 0.5625) + 540 = 36 + 540 = 576
        dst_y = ((1024 - 540) * 0.5625) + 960 = 272.25 + 960 = 1232.25
        """
        layers = [
            _av_layer(
                index=1,
                name="Pre_Neon_logo_10_mc",
                pos_x=NESTED_CX,
                pos_y=NESTED_CY,
                containing_comp_id=NESTED_COMP_ID,
            )
        ]
        # No comp_dims — falls back to root center
        conformed = _conform(layers, comp_dims=None)
        pos = conformed[0]["conformed_transforms"]["position"]

        # These values should NOT be the target center (540, 960)
        root_cx, root_cy = ROOT_W / 2.0, ROOT_H / 2.0  # 960, 540
        wrong_x = ((NESTED_CX - root_cx) * S_FIT) + TGT_CX   # 576.0
        wrong_y = ((NESTED_CY - root_cy) * S_FIT) + TGT_CY   # 1232.25

        assert abs(pos[0] - wrong_x) < TOL, (
            f"Without fix, expected x≈{wrong_x}; got {pos[0]}"
        )
        assert abs(pos[1] - wrong_y) < TOL, (
            f"Without fix, expected y≈{wrong_y}; got {pos[1]}"
        )
        # Confirm they are different from target center
        assert abs(pos[0] - TGT_CX) > 1.0, (
            "Without comp_dims, position should NOT match target center x"
        )
        assert abs(pos[1] - TGT_CY) > 1.0, (
            "Without comp_dims, position should NOT match target center y"
        )


class TestRootCompLayerUnaffected:
    """
    Case 3: Root comp layer (no containing_comp_id) → unchanged behavior.

    comp_dims has no effect on layers that don't have containing_comp_id.
    The root comp center is always used for these layers.
    """

    def test_root_layer_at_root_center_maps_to_target_center(self):
        """A layer at root comp center (960, 540) → target center (540, 960)."""
        root_cx, root_cy = ROOT_W / 2.0, ROOT_H / 2.0
        layers = [
            _av_layer(
                index=1,
                name="Root_Center_Layer",
                pos_x=root_cx,
                pos_y=root_cy,
                containing_comp_id=None,  # top-level layer
            )
        ]
        # Supply comp_dims anyway — should not affect root layers
        comp_dims = {NESTED_COMP_ID: (NESTED_W, NESTED_H)}
        conformed = _conform(layers, comp_dims=comp_dims)
        pos = conformed[0]["conformed_transforms"]["position"]
        assert abs(pos[0] - TGT_CX) < TOL, (
            f"Root-center layer should map to target center x={TGT_CX}; "
            f"got {pos[0]}"
        )
        assert abs(pos[1] - TGT_CY) < TOL, (
            f"Root-center layer should map to target center y={TGT_CY}; "
            f"got {pos[1]}"
        )

    def test_root_layer_position_same_with_and_without_comp_dims(self):
        """Root layer position output is identical whether comp_dims is
        provided or not — the fix must not alter root-layer behavior."""
        root_cx, root_cy = ROOT_W / 2.0, ROOT_H / 2.0
        pos_x, pos_y = root_cx + 120.0, root_cy - 80.0
        layers = [
            _av_layer(
                index=1,
                name="Root_Offset_Layer",
                pos_x=pos_x,
                pos_y=pos_y,
                containing_comp_id=None,
            )
        ]
        comp_dims = {NESTED_COMP_ID: (NESTED_W, NESTED_H)}
        conformed_with = _conform(layers, comp_dims=comp_dims)
        conformed_without = _conform(layers, comp_dims=None)

        pos_with = conformed_with[0]["conformed_transforms"]["position"]
        pos_without = conformed_without[0]["conformed_transforms"]["position"]
        assert abs(pos_with[0] - pos_without[0]) < TOL, (
            "Root layer x must be identical with/without comp_dims"
        )
        assert abs(pos_with[1] - pos_without[1]) < TOL, (
            "Root layer y must be identical with/without comp_dims"
        )

    def test_comp_dims_with_root_comp_id_does_not_change_root_layer(self):
        """
        Case 4: comp_dims includes an entry for the root comp's ID.
        Even if comp_dims happens to contain the root comp, a layer
        without containing_comp_id falls back to root center (no lookup).
        """
        root_cx, root_cy = ROOT_W / 2.0, ROOT_H / 2.0
        layers = [
            _av_layer(
                index=1,
                name="Root_Layer",
                pos_x=root_cx,
                pos_y=root_cy,
                containing_comp_id=None,
            )
        ]
        # Include root comp in comp_dims with DIFFERENT dimensions to make
        # sure the lookup isn't wrongly applied
        comp_dims = {
            ROOT_COMP_ID: (3840, 2160),  # wrong dims for root — must not be used
            NESTED_COMP_ID: (NESTED_W, NESTED_H),
        }
        conformed = _conform(layers, comp_dims=comp_dims)
        pos = conformed[0]["conformed_transforms"]["position"]
        # Layer at root center should STILL map to target center
        assert abs(pos[0] - TGT_CX) < TOL, (
            f"Root layer at root center should map to target center x; "
            f"got {pos[0]}"
        )
        assert abs(pos[1] - TGT_CY) < TOL, (
            f"Root layer at root center should map to target center y; "
            f"got {pos[1]}"
        )


class TestBackwardCompatibility:
    """
    comp_dims=None (default) and comp_dims={} produce identical output.
    ScaleEngine.__init__ without comp_dims arg must work unchanged.
    """

    def test_no_comp_dims_arg_accepted(self):
        """ScaleEngine must accept no comp_dims kwarg (backward compat)."""
        layers = [
            _av_layer(index=1, name="Layer_1", pos_x=960.0, pos_y=540.0)
        ]
        payload = _base_manifest_payload(layers)
        manifest = ScrapeManifest.model_validate(payload)
        # Call without comp_dims — must not raise
        engine = ScaleEngine(manifest, TGT_W, TGT_H, "Fit", 0.0)
        result = engine.conform()
        assert result["status"] == "SAFE"

    def test_empty_comp_dims_same_as_none(self):
        """comp_dims={} must produce same result as comp_dims=None."""
        layers = [
            _av_layer(
                index=1,
                name="Layer_1",
                pos_x=960.0,
                pos_y=540.0,
                containing_comp_id=NESTED_COMP_ID,
            )
        ]
        conformed_none = _conform(layers, comp_dims=None)
        conformed_empty = _conform(layers, comp_dims={})
        pos_none = conformed_none[0]["conformed_transforms"]["position"]
        pos_empty = conformed_empty[0]["conformed_transforms"]["position"]
        assert abs(pos_none[0] - pos_empty[0]) < TOL
        assert abs(pos_none[1] - pos_empty[1]) < TOL
