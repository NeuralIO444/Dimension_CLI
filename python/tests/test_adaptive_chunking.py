# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_adaptive_chunking.py — Unit tests for adaptive dynamic chunk batching.

Verifies:
1. Static lightweight layers are batched up to target weight and max 30 layers.
2. Keyframe-dense and effect-heavy layers receive dedicated higher weight and smaller chunk slices.
3. Strict Pydantic max_length=30 enforcement on all generated ChunkManifests.
4. DIMENSION_CHUNK_SIZE environment variable override behavior.
5. Preserved index ordering in chunk_paths.
"""

import shutil
import tempfile
from pathlib import Path
from typing import List

import pytest
from logic.exporter import (
    PayloadSlicer,
    _calculate_layer_weight,
    _clamp_chunk_size,
    _partition_layers_adaptively,
)
from models.conformed_manifest import ConformedLayer, ConformedTransforms


def _make_static_layer(index: int, name: str = "") -> ConformedLayer:
    return ConformedLayer(
        index=index,
        name=name or f"Layer_{index}",
        layer_index=index,
        uid=f"uid_{index}",
        conformed_transforms=ConformedTransforms(
            is_root=True,
            position=[960.0, 540.0, 0.0],
            scale=[100.0, 100.0, 100.0],
            rotation=0.0,
            anchor=[960.0, 540.0, 0.0],
        ),
    )


def _make_heavy_keyframed_layer(index: int, key_count: int = 40) -> ConformedLayer:
    times = [float(i) * 0.1 for i in range(key_count)]
    values = [[960.0 + i, 540.0, 0.0] for i in range(key_count)]
    return ConformedLayer(
        index=index,
        name=f"Heavy_Keyframe_Layer_{index}",
        layer_index=index,
        uid=f"uid_heavy_{index}",
        conformed_transforms=ConformedTransforms(
            is_root=True,
            position=[960.0, 540.0, 0.0],
            scale=[100.0, 100.0, 100.0],
            rotation=0.0,
            anchor=[960.0, 540.0, 0.0],
        ),
        conformed_keys={
            "position": {
                "times": times,
                "values": values,
            }
        },
    )


def _make_effect_heavy_layer(index: int, effect_count: int = 5) -> ConformedLayer:
    effects = [
        {
            "index": e + 1,
            "name": f"Effect_{e}",
            "match_name": f"ADBE_Effect_{e}",
            "display_name": f"Effect {e}",
            "params": [],
        }
        for e in range(effect_count)
    ]
    return ConformedLayer(
        index=index,
        name=f"Effect_Layer_{index}",
        layer_index=index,
        uid=f"uid_fx_{index}",
        conformed_transforms=ConformedTransforms(
            is_root=True,
            position=[960.0, 540.0, 0.0],
            scale=[100.0, 100.0, 100.0],
            rotation=0.0,
            anchor=[960.0, 540.0, 0.0],
        ),
        conformed_effects=effects,
    )


class TestLayerWeightCalculation:
    def test_static_layer_weight_is_one(self):
        layer = _make_static_layer(1)
        w = _calculate_layer_weight(layer)
        assert w == 1.0

    def test_keyframed_layer_weight_scales_with_keys(self):
        layer_10 = _make_heavy_keyframed_layer(1, key_count=10)
        layer_40 = _make_heavy_keyframed_layer(2, key_count=40)
        w_10 = _calculate_layer_weight(layer_10)
        w_40 = _calculate_layer_weight(layer_40)
        assert w_10 == 1.0 + 5.0  # 1.0 base + 10 * 0.5
        assert w_40 == 1.0 + 15.0  # capped at 15.0 max keyframe bonus -> 16.0

    def test_effects_layer_weight_scales_with_effects(self):
        layer_3fx = _make_effect_heavy_layer(1, effect_count=3)
        w_3fx = _calculate_layer_weight(layer_3fx)
        assert w_3fx == 1.0 + (3 * 2.0)  # 7.0


class TestAdaptivePartitioning:
    def test_empty_layers_returns_empty_chunks(self):
        assert _partition_layers_adaptively([]) == []

    def test_batches_60_static_layers_into_exact_two_chunks(self):
        # 60 static layers (weight 1.0 each) with max_chunk_size=30, target_weight=30.0
        # -> exactly 2 chunks of 30 layers each.
        layers = [_make_static_layer(i + 1) for i in range(60)]
        chunks = _partition_layers_adaptively(layers, max_chunk_size=30, target_weight=30.0)
        assert len(chunks) == 2
        assert len(chunks[0]) == 30
        assert len(chunks[1]) == 30

    def test_heavy_layers_partition_into_smaller_chunks(self):
        # 10 heavy keyframed layers (weight 16.0 each) -> target_weight=30.0 -> max 1 per chunk
        layers = [_make_heavy_keyframed_layer(i + 1, key_count=40) for i in range(10)]
        chunks = _partition_layers_adaptively(layers, max_chunk_size=30, target_weight=30.0)
        # Each chunk should hold 1 heavy layer
        assert len(chunks) == 10
        for chunk in chunks:
            assert len(chunk) == 1

    def test_mixed_layers_partition_safely_and_respect_30_cap(self):
        layers: List[ConformedLayer] = []
        for i in range(20):
            layers.append(_make_static_layer(i + 1))
        layers.append(_make_heavy_keyframed_layer(21, key_count=30))
        for i in range(22, 40):
            layers.append(_make_static_layer(i))

        chunks = _partition_layers_adaptively(layers, max_chunk_size=30, target_weight=30.0)
        total_partitioned = sum(len(c) for c in chunks)
        assert total_partitioned == len(layers)
        for chunk in chunks:
            assert len(chunk) <= 30
            assert len(chunk) >= 1


class TestPayloadSlicerEndToEnd:
    @pytest.fixture
    def temp_dir(self):
        d = tempfile.mkdtemp(prefix="dim_test_slicer_")
        yield d
        shutil.rmtree(d, ignore_errors=True)

    def test_slice_and_export_adaptive_execution(self, temp_dir):
        import json
        raw_layers = [
            _make_static_layer(i + 1).model_dump(exclude_unset=True)
            for i in range(50)
        ]
        slicer = PayloadSlicer(output_dir=temp_dir)
        manifest_path = slicer.slice_and_export(
            conformed_layers_data=raw_layers,
            target_width=1080,
            target_height=1920,
            preset_label="TEST_ADAPTIVE",
            expected_comp_name="Test Comp",
        )
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

        assert manifest["total_layers"] == 50
        assert manifest["total_chunks"] == 2  # 30 + 20
        assert len(manifest["chunk_paths"]) == 2
        # Check that chunk paths are ordered
        assert manifest["chunk_paths"][0].endswith("chunk_000.json")
        assert manifest["chunk_paths"][1].endswith("chunk_001.json")

    def test_slice_and_export_env_override(self, temp_dir, monkeypatch):
        import json
        monkeypatch.setenv("DIMENSION_CHUNK_SIZE", "5")
        raw_layers = [
            _make_static_layer(i + 1).model_dump(exclude_unset=True)
            for i in range(15)
        ]
        slicer = PayloadSlicer(output_dir=temp_dir)
        manifest_path = slicer.slice_and_export(
            conformed_layers_data=raw_layers,
            target_width=1080,
            target_height=1920,
            preset_label="TEST_OVERRIDE",
            expected_comp_name="Test Comp",
        )
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

        assert manifest["total_layers"] == 15
        assert manifest["total_chunks"] == 3  # 15 / 5 = 3 chunks
        assert len(manifest["chunk_paths"]) == 3

    def test_slice_and_export_invalid_env_falls_back_instead_of_crashing(
        self, temp_dir, monkeypatch,
    ):
        """Regression test — DIMENSION_CHUNK_SIZE=<garbage> used to raise
        an unhandled ValueError out of slice_and_export instead of
        falling back like _resolve_chunk_size() already does."""
        import json
        monkeypatch.setenv("DIMENSION_CHUNK_SIZE", "not-a-number")
        raw_layers = [
            _make_static_layer(i + 1).model_dump(exclude_unset=True)
            for i in range(15)
        ]
        slicer = PayloadSlicer(output_dir=temp_dir)
        manifest_path = slicer.slice_and_export(
            conformed_layers_data=raw_layers,
            target_width=1080,
            target_height=1920,
            preset_label="TEST_BAD_ENV",
            expected_comp_name="Test Comp",
        )
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        assert manifest["total_layers"] == 15
        # Falls back to MAX_CHUNK_SIZE (30 by default) — all 15 layers
        # fit in one chunk, same as if DIMENSION_CHUNK_SIZE weren't set.
        assert manifest["total_chunks"] == 1

    def test_slice_and_export_env_above_30_is_clamped_to_30(self, temp_dir, monkeypatch):
        """DIMENSION_CHUNK_SIZE=50 must clamp down to 30, not be honored
        as-is — ChunkManifest.layers has a hard Pydantic max_length=30
        (models/conformed_manifest.py) that's unconditional and not
        itself governed by this env var. A resolved chunk_size above 30
        would crash at ChunkManifest construction with a
        ValidationError instead of a clean, predictable chunk split."""
        import json
        monkeypatch.setenv("DIMENSION_CHUNK_SIZE", "50")
        raw_layers = [
            _make_static_layer(i + 1).model_dump(exclude_unset=True)
            for i in range(40)
        ]
        slicer = PayloadSlicer(output_dir=temp_dir)
        manifest_path = slicer.slice_and_export(
            conformed_layers_data=raw_layers,
            target_width=1080,
            target_height=1920,
            preset_label="TEST_ABOVE_30",
            expected_comp_name="Test Comp",
        )
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        assert manifest["total_layers"] == 40
        # 40 layers at chunk_size=30 (clamped) → 2 chunks: 30 + 10.
        assert manifest["total_chunks"] == 2


class TestClampChunkSize:
    def test_valid_value_within_ceiling(self):
        assert _clamp_chunk_size("15") == 15

    def test_value_above_30_clamped(self):
        """Ceiling matches ChunkManifest.layers's hard
        Pydantic max_length=30 — not itself configurable via this env
        var, so a value above it must clamp, not pass through."""
        assert _clamp_chunk_size("500") == 30
        assert _clamp_chunk_size("50") == 30

    def test_value_below_1_clamped(self):
        assert _clamp_chunk_size("0") == 1
        assert _clamp_chunk_size("-5") == 1

    def test_none_returns_default(self):
        assert _clamp_chunk_size(None, default=30) == 30
        assert _clamp_chunk_size(None, default=99) == 99

    def test_non_numeric_falls_back_to_default_instead_of_raising(self):
        assert _clamp_chunk_size("not-a-number", default=30) == 30
        assert _clamp_chunk_size("", default=30) == 30
