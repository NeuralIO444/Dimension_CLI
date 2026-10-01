# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_batch_layout_param.py

stages/batch.py::run_batch_conform's `layout` parameter (2026-07-21 Dashboard
redesign work). PR #174 hardcoded `layout="auto"` in the ConformConfig this
function builds, to fix a real precomp-resizing bug in the Dashboard's batch
path. This widens the hardcoded literal to a real, callable parameter (still
defaulting to "auto", so PR #174's fix is preserved for any caller that
doesn't pass one explicitly) so the Dashboard's ported Layout select has a
real effect instead of being cosmetic.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from stages.batch import run_batch_conform


def _fake_result():
    result = MagicMock()
    result.chunk_manifest_path = "/tmp/chunk_manifest.json"
    return result


def test_default_layout_is_auto():
    """No caller-supplied layout — must still default to 'auto', same as
    PR #174's fix, not silently regress to ConformConfig's own 'tags'
    default."""
    with patch("stages.batch.run_conform", return_value=_fake_result()) as mock_run:
        run_batch_conform(
            "scrape_manifest.json",
            [{"width": 1080, "height": 1080}],
        )
        cfg = mock_run.call_args[0][0]
        assert cfg.layout == "auto"


def test_explicit_layout_reaches_conform_config():
    """A caller (the Dashboard's ported Layout select) explicitly choosing
    'tags' or 'scene' must have real effect on the ConformConfig — not be
    a cosmetic-only control."""
    with patch("stages.batch.run_conform", return_value=_fake_result()) as mock_run:
        run_batch_conform(
            "scrape_manifest.json",
            [{"width": 1080, "height": 1080}],
            layout="tags",
        )
        cfg = mock_run.call_args[0][0]
        assert cfg.layout == "tags"


def test_layout_applies_uniformly_across_all_targets_in_one_batch():
    """layout is a batch-level setting (like profile/mode/bleed), not
    per-target — every target in one /api/execute-batch call gets the
    same value."""
    with patch("stages.batch.run_conform", return_value=_fake_result()) as mock_run:
        run_batch_conform(
            "scrape_manifest.json",
            [
                {"width": 1080, "height": 1080},
                {"width": 1080, "height": 1920},
            ],
            layout="scene",
        )
        assert mock_run.call_count == 2
        for call in mock_run.call_args_list:
            assert call[0][0].layout == "scene"
