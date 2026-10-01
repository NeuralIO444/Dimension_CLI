# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_scale_engine_collision.py — Unit tests verifying tuple-scoped layer lookup,
parent chain walks, and 3D camera leakage isolation under recursive manifests.
"""

from __future__ import annotations

import sys
import os

# Ensure repo root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from models.scrape_manifest import ScrapeManifest, LayerModel, ProjectInfo
from core.scale_engine import ScaleEngine
from core.classify import is_layer_root, detect_3d_camera_scenes_by_comp, should_scale_z


def test_is_layer_root_tuple_scoped():
    # Scoped: contains tuples of (comp_id, index)
    scoped_indices = {(1, 10), (1, 20), (2, 10)}
    
    # 1. Parent is in the same comp and exists in set -> not a root
    assert not is_layer_root(parent_index=10, layer_indices=scoped_indices, containing_comp_id=1)
    
    # 2. Parent index is -1 -> is root
    assert is_layer_root(parent_index=-1, layer_indices=scoped_indices, containing_comp_id=1)
    
    # 3. Parent index exists in another comp but not this comp -> is root
    assert is_layer_root(parent_index=20, layer_indices=scoped_indices, containing_comp_id=2)
    
    # 4. Fallback for non-scoped flat set
    flat_indices = {10, 20}
    assert not is_layer_root(parent_index=10, layer_indices=flat_indices)
    assert is_layer_root(parent_index=-1, layer_indices=flat_indices)
    assert is_layer_root(parent_index=30, layer_indices=flat_indices)


def test_detect_3d_camera_scenes_by_comp():
    layers = [
        # Comp 1: 3D Camera Scene (has camera and 3D layer)
        LayerModel(index=1, name="Camera 1", layer_kind="camera", threeD=False, containing_comp_id=1, camera={}),
        LayerModel(index=2, name="Layer 3D Comp 1", layer_kind="av", threeD=True, containing_comp_id=1),
        
        # Comp 2: Flat Comp (has camera but no 3D layers)
        LayerModel(index=1, name="Camera 2", layer_kind="camera", threeD=False, containing_comp_id=2, camera={}),
        LayerModel(index=2, name="Layer 2D Comp 2", layer_kind="av", threeD=False, containing_comp_id=2),
        
        # Comp 3: Flat Comp (has 3D layer but no camera)
        LayerModel(index=1, name="Layer 3D Comp 3", layer_kind="av", threeD=True, containing_comp_id=3),
    ]
    
    scenes = detect_3d_camera_scenes_by_comp(layers)
    assert 1 in scenes
    assert 2 not in scenes
    assert 3 not in scenes


def test_should_scale_z_isolation():
    layers = [
        # Comp 1: 3D Camera Scene
        LayerModel(index=1, name="Camera 1", layer_kind="camera", threeD=False, containing_comp_id=1, camera={}),
        LayerModel(index=2, name="Layer 3D Comp 1", layer_kind="av", threeD=True, containing_comp_id=1),
        
        # Comp 2: Flat Comp (no camera)
        LayerModel(index=2, name="Layer 3D Comp 2", layer_kind="av", threeD=True, containing_comp_id=2),
    ]
    
    scenes = detect_3d_camera_scenes_by_comp(layers)
    
    # Layer 3D Comp 1 should scale Z since Comp 1 is a 3D camera scene
    assert should_scale_z(layers[1], scenes)
    
    # Layer 3D Comp 2 should NOT scale Z since Comp 2 is not a 3D camera scene (no camera)
    assert not should_scale_z(layers[2], scenes)


def test_scale_engine_index_collision_world_space():
    # Setup a manifest containing colliding layer indices across two comps.
    # In Comp 1, index 99 exists.
    # In Comp 2, index 2 exists and has parent_index 99. But Comp 2 does NOT have a layer with index 99.
    # Without scoping, index 2 in Comp 2 would see index 99 in Comp 1 and think it's a child.
    # With scoping, it should correctly see that index 99 does not exist in Comp 2, so it's a root.
    layers = [
        # Comp 1
        LayerModel(
            index=99,
            name="Parent Comp 1",
            layer_kind="av",
            position=[100.0, 100.0, 0.0],
            scale=[100.0, 100.0, 100.0],
            containing_comp_id=1,
        ),
        # Comp 2
        LayerModel(
            index=2,
            name="Root Comp 2 (parent 99 does not exist in Comp 2)",
            layer_kind="av",
            parent_index=99,
            position=[300.0, 300.0, 0.0],
            containing_comp_id=2,
        ),
    ]
    
    manifest = ScrapeManifest(
        status="OK",
        project_info=ProjectInfo(width=1920, height=1080, name="Collision Test"),
        layers=layers,
    )
    
    engine = ScaleEngine(
        manifest=manifest,
        target_width=1080,
        target_height=1080,
        scale_mode="fit",
        bleed_pct=0.0,
    )
    
    result = engine.conform()
    
    conformed_layers = result["layers"]
    assert len(conformed_layers) == 2
    
    comp2_root_out = conformed_layers[1]
    
    # It must be conformed as a root layer (is_root = True) because parent index 99
    # is scoped to containing_comp_id=2, which does not contain index 99.
    assert comp2_root_out["name"] == "Root Comp 2 (parent 99 does not exist in Comp 2)"
    assert comp2_root_out["conformed_transforms"]["is_root"] is True

