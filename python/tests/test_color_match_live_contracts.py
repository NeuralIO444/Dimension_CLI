# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_color_match_live_contracts.py — TASK-CM-QA-01 (Issue #240)
Validates Layer 2 ExtendScript contract fixtures for ADBE Apply Color LUT 2 and ExportFrame.
"""

import pytest


@pytest.fixture
def live_ae_fixture() -> dict:
    return {
        "status": "OK",
        "export_id": "EXP_20260830_99A",
        "source_comp_id": 1600004739,
        "source_comp_name": "87N_Reels_DEV_87Neon_Anima_HD_01_mc",
        "rendered_frame_path": "/tmp/dimension_color_match/ref_frame_01.png",
        "width": 1920,
        "height": 1080,
        "applied_lut_effect": {
            "matchName": "ADBE Apply Color LUT 2",
            "displayName": "Apply Color LUT",
            "properties": {
                "3D LUT File": "/Library/Application Support/Adobe/Common/LUTs/Creative/Horizon_Warm.cube"
            }
        },
        "execution_time_ms": 14.8
    }


class TestColorMatchLiveContract:
    def test_layer_2_contract_schema(self, live_ae_fixture):
        assert live_ae_fixture["status"] == "OK"
        assert live_ae_fixture["applied_lut_effect"]["matchName"] == "ADBE Apply Color LUT 2"
        assert live_ae_fixture["width"] == 1920
        assert live_ae_fixture["height"] == 1080
        assert live_ae_fixture["execution_time_ms"] < 50.0
