# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_kinematics_3d.py — TASK-ENG-07 / Issue #296
Unit tests for 4x4 homogeneous transformation matrices and 3D KinematicSolver.
"""


from core.kinematics import (
    KinematicSolver3D,
    create_3d_affine_matrix,
    decompose_3d_affine_matrix,
)
from models.scrape_manifest import LayerModel


class Test3DTransformMatrices:
    def test_identity_roundtrip(self):
        mat = create_3d_affine_matrix(
            position=(960.0, 540.0, -1000.0),
            scale=(100.0, 100.0, 100.0),
            rotation_xyz=(0.0, 0.0, 0.0),
            anchor=(0.0, 0.0, 0.0),
        )
        decomp = decompose_3d_affine_matrix(mat, anchor=(0.0, 0.0, 0.0))
        assert decomp["position"] == [960.0, 540.0, -1000.0]
        assert decomp["scale"] == [100.0, 100.0, 100.0]
        assert decomp["rotation_x"] == 0.0
        assert decomp["rotation_y"] == 0.0
        assert decomp["rotation_z"] == 0.0

    def test_3d_rotation_decomposition(self):
        # 45 deg roll around Z, 30 deg pitch around X
        mat = create_3d_affine_matrix(
            position=(500.0, 500.0, 200.0),
            scale=(200.0, 200.0, 200.0),
            rotation_xyz=(30.0, 0.0, 45.0),
            anchor=(50.0, 50.0, 0.0),
        )
        decomp = decompose_3d_affine_matrix(mat, anchor=(50.0, 50.0, 0.0))
        assert abs(decomp["scale"][0] - 200.0) < 0.01
        assert abs(decomp["rotation_x"] - 30.0) < 0.01
        assert abs(decomp["rotation_z"] - 45.0) < 0.01

    def test_multi_tier_3d_gimbal_solver(self):
        # Gimbal Null (parent) -> Camera (child)
        gimbal_null = LayerModel(
            index=1,
            name="Gimbal_Pan_Tilt",
            position=[960.0, 540.0, 0.0],
            scale=[100.0, 100.0, 100.0],
            rotation_y=45.0,
        )
        cam = LayerModel(
            index=2,
            name="Camera 1",
            layer_kind="camera",
            position=[0.0, 0.0, -1500.0],
            scale=[100.0, 100.0, 100.0],
            parent_index=1,
        )
        solver = KinematicSolver3D(
            layers=[gimbal_null, cam],
            source_width=1920,
            source_height=1080,
            target_width=1080,
            target_height=1920,
            uniform_scale=0.5625,
            depth_scale=0.5625,
            center_offset=(0.0, 0.0, 0.0),
        )
        res = solver.solve()
        assert "idx-1" in res
        assert "idx-2" in res
        assert res["idx-1"]["relayout_status"] == "OK"
        assert res["idx-2"]["relayout_status"] == "OK"
        # Child camera retains local offset relative to conformed gimbal
        assert res["idx-2"]["position"][2] == -1500.0
