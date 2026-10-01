# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_constants.py — Issue #295
Verifies centralized constants, types, and invariant thresholds.
"""

from config.constants import (
    BABYSITTER_UNDO_GROUP_NAME,
    BYTES_PER_PIXEL_8BPC,
    BYTES_PER_PIXEL_32BPC,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_EPSILON,
    DEFAULT_SERVER_PORT,
    DEFAULT_WATCHDOG_NAME,
    DEFAULT_WATCHDOG_POLL_INTERVAL_S,
    EDGE_TOLERANCE_PX,
    EXTREME_ASPECT_RATIO_THRESHOLD,
    HIGHRES_AUDIT_TOLERANCE_PX,
    MAX_AE_COMP_DIMENSION_PX,
    MAX_CHUNK_SIZE,
    MAX_GPU_TEXTURE_PX,
    ORPHAN_CLEANUP_UNDO_GROUP_NAME,
    READ_INITIAL_DELAY_S,
    READ_MAX_ATTEMPTS,
    ROTATION_EPSILON_DEG,
    SOE_MAX_ITERATIONS,
    SOE_REPULSION_FLOOR_PX,
    SOE_REPULSION_MARGIN_PX,
    SUBPIXEL_AUDIT_TOLERANCE_PX,
    TOTAL_READ_RETRY_WINDOW_S,
    TYPO_FOOTNOTE_MAX_PT,
    TYPO_TITLE_MIN_PT,
)


class TestEngineConstants:
    def test_hardware_and_resolution_limits(self):
        assert MAX_GPU_TEXTURE_PX == 16384
        assert MAX_AE_COMP_DIMENSION_PX == 30000
        assert EXTREME_ASPECT_RATIO_THRESHOLD == 10.0
        assert BYTES_PER_PIXEL_8BPC == 4
        assert BYTES_PER_PIXEL_32BPC == 16

    def test_spatial_tolerances_and_margins(self):
        assert DEFAULT_EPSILON == 1e-4
        assert ROTATION_EPSILON_DEG == 1e-6
        assert EDGE_TOLERANCE_PX == 1e-9
        assert SUBPIXEL_AUDIT_TOLERANCE_PX == 0.05
        assert HIGHRES_AUDIT_TOLERANCE_PX == 0.21
        assert SOE_REPULSION_MARGIN_PX == 16.0
        assert SOE_REPULSION_FLOOR_PX == 8.0
        assert SOE_MAX_ITERATIONS == 100

    def test_pipeline_slicing_and_undo_groups(self):
        assert DEFAULT_CHUNK_SIZE == 30
        assert MAX_CHUNK_SIZE == 100
        assert BABYSITTER_UNDO_GROUP_NAME == "Dimension Conform Injection"
        assert ORPHAN_CLEANUP_UNDO_GROUP_NAME == "Clean Dimension Orphan Comps"

    def test_server_and_retry_windows(self):
        assert DEFAULT_SERVER_PORT == 4444
        assert DEFAULT_WATCHDOG_POLL_INTERVAL_S == 1.5
        assert DEFAULT_WATCHDOG_NAME == "After Effects"
        assert READ_MAX_ATTEMPTS == 5
        assert READ_INITIAL_DELAY_S == 0.02
        assert TOTAL_READ_RETRY_WINDOW_S == 0.30

    def test_typographic_thresholds(self):
        assert TYPO_FOOTNOTE_MAX_PT == 24.0
        assert TYPO_TITLE_MIN_PT == 48.0
