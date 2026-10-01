# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Bug K — constant hold-keys preserve head and tail after dead-zone filter."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.lerp_engine import _dead_zone_filter


def test_hold_keys_preserve_head_and_tail():
    times = [0.0, 6.75, 9.96]
    values = [960.0, 960.0, 960.0]
    out_times, out_values, _, _, skipped = _dead_zone_filter(
        times, values, None, None,
    )
    assert skipped >= 1
    assert len(out_times) == 2
    assert out_times == [0.0, 9.96]
    assert out_values == [960.0, 960.0]