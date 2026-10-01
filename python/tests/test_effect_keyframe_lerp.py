# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_effect_keyframe_lerp.py
Tests keyframe stream scaling on effect and layer-style properties and composite dependency guard (TASK-SUB-FEAT / #274).
"""

from core.lerp_engine import LerpEngine, is_composite_dependent_transfer_mode


class TestEffectKeyframeLerp:
    def test_blur_radius_keyframe_scaling(self):
        """Blur radius (scalar distance) scales by uniform scale S."""
        keys = {
            "times": [0.0, 1.0, 2.0],
            "values": [10.0, 20.0, 30.0],
        }
        res = LerpEngine.package_conformed_effect_keys(
            keys_dict=keys,
            match_name="ADBE Fast Blur-0001",
            display_name="Blurriness",
            value_kind="float",
            uniform_scale=2.0,
            src_center=(960.0, 540.0),
            tgt_center=(1920.0, 1080.0),
        )
        assert res is not None
        assert res.times == [0.0, 1.0, 2.0]
        # 10*2=20, 20*2=40, 30*2=60
        assert res.values == [20.0, 40.0, 60.0]

    def test_point_2d_center_remap_keyframe_scaling(self):
        """2D point centers remap from source canvas to target canvas."""
        # Emitter at source center (960, 540) moving to (1060, 540)
        keys = {
            "times": [0.0, 1.0],
            "values": [[960.0, 540.0], [1060.0, 540.0]],
        }
        res = LerpEngine.package_conformed_effect_keys(
            keys_dict=keys,
            match_name="ADBE Flare Center",
            display_name="Flare Center",
            value_kind="vec2",
            uniform_scale=2.0,
            src_center=(960.0, 540.0),
            tgt_center=(1920.0, 1080.0),
        )
        assert res is not None
        assert res.times == [0.0, 1.0]
        # Center (960, 540) -> (1920, 1080)
        # (1060, 540) is +100px X from center -> +200px X in 2.0x target -> (2120, 1080)
        assert res.values[0] == [1920.0, 1080.0]
        assert res.values[1] == [2120.0, 1080.0]

    def test_percentage_opacity_keyframe_pass_through(self):
        """Percentage opacity must NOT scale by S (avoids blowing out to 200%)."""
        keys = {
            "times": [0.0, 1.0],
            "values": [50.0, 100.0],
        }
        res = LerpEngine.package_conformed_effect_keys(
            keys_dict=keys,
            match_name="ADBE Drop Shadow-0001",
            display_name="Opacity",
            value_kind="float",
            uniform_scale=2.0,
            src_center=(960.0, 540.0),
            tgt_center=(1920.0, 1080.0),
        )
        assert res is not None
        assert res.times == [0.0, 1.0]
        # 50.0 and 100.0 must remain 50.0 and 100.0
        assert res.values == [50.0, 100.0]

    def test_empty_or_none_keys_returns_none(self):
        assert LerpEngine.package_conformed_effect_keys(
            keys_dict={},
            match_name="foo",
            display_name="bar",
            value_kind=None,
            uniform_scale=2.0,
            src_center=(0, 0),
            tgt_center=(0, 0),
        ) is None

    def test_mismatched_times_and_values_returns_none(self):
        assert LerpEngine.package_conformed_effect_keys(
            keys_dict={"times": [0.0, 1.0, 2.0], "values": [10.0, 20.0]},
            match_name="foo",
            display_name="bar",
            value_kind=None,
            uniform_scale=2.0,
            src_center=(0, 0),
            tgt_center=(0, 0),
        ) is None

class TestCompositeDependencyGuard:
    def test_normal_modes_are_not_composite_dependent(self):
        assert not is_composite_dependent_transfer_mode("Normal")
        assert not is_composite_dependent_transfer_mode("Dissolve")
        assert not is_composite_dependent_transfer_mode(None)
        assert not is_composite_dependent_transfer_mode("")

    def test_non_normal_modes_are_flagged(self):
        assert is_composite_dependent_transfer_mode("Screen")
        assert is_composite_dependent_transfer_mode("Multiply")
        assert is_composite_dependent_transfer_mode("Color Dodge")
        assert is_composite_dependent_transfer_mode("classic-color-burn")
        assert is_composite_dependent_transfer_mode("Overlay")
        assert is_composite_dependent_transfer_mode("Difference")
