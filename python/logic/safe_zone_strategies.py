# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/logic/safe_zone_strategies.py
Safe-zone derivation strategies. v1 ships one concrete strategy:
DerivedSymmetricSingleRect for the theatrical channel.

A strategy converts a Target (resolution + aspect + channel metadata)
into a uint8 numpy array compatible with
`python/core/occlusion_engine.py::OcclusionMask`'s classifier:
  - shape (target.height, target.width)
  - dtype uint8
  - binary GO (255) / CUTOFF (0); no NUDGE band in v1
"""

from __future__ import annotations

from typing import Optional, Protocol

import numpy as np

from logic.channel_config import get_channel_config
from models.target import Target


class SafeZoneStrategy(Protocol):
    """Minimal strategy interface. Implementations are stateless
    per-call; per-strategy config is held in instance attributes
    populated at construction by `get_strategy_for_channel`."""

    def produce_mask(self, target: Target) -> Optional[np.ndarray]:
        ...


class DerivedSymmetricSingleRect:
    """Compute a single safe rectangle by inset percentage, symmetric
    on all four edges. Output is a binary GO/CUTOFF mask:
      - inside the inset rectangle: 255 (GO)
      - outside:                    0   (CUTOFF)
    No NUDGE band — the engine's three-zone classifier will still
    work; mid-gray values just aren't present in this output."""

    def __init__(self, action_safe_inset: float):
        if not 0.0 < action_safe_inset < 0.5:
            # Inset must be a positive fraction strictly under 50%
            # (otherwise the safe rectangle collapses or inverts).
            raise ValueError(
                f"action_safe_inset must be in (0.0, 0.5); got {action_safe_inset}"
            )
        self.action_safe_inset = float(action_safe_inset)

    def produce_mask(self, target: Target) -> Optional[np.ndarray]:
        w = int(target.width)
        h = int(target.height)
        if w <= 0 or h <= 0:
            return None
        arr = np.zeros((h, w), dtype=np.uint8)
        inset_x = int(round(w * self.action_safe_inset))
        inset_y = int(round(h * self.action_safe_inset))
        # Guard against pathologically small canvases where the inset
        # would consume the entire frame.
        if inset_x * 2 >= w or inset_y * 2 >= h:
            return None
        arr[inset_y:h - inset_y, inset_x:w - inset_x] = 255
        return arr


def get_strategy_for_channel(channel_name: str) -> Optional[SafeZoneStrategy]:
    """Factory mapping a channel name to a strategy instance.
    Returns None when:
      - channel is unknown
      - channel's strategy is `authored_png` (fall through to PNG)
      - strategy is unrecognized
      - required strategy parameters are missing
    """
    cfg = get_channel_config(channel_name)
    if not cfg:
        return None
    strategy_name = cfg.get("strategy")
    if strategy_name == "derived_symmetric_single_rect":
        inset = cfg.get("action_safe_inset")
        if inset is None:
            return None
        return DerivedSymmetricSingleRect(action_safe_inset=float(inset))
    # authored_png + anything else: fall through to PNG lookup.
    return None
