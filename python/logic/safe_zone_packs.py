# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).

"""Guide packs (M0) + pack_format (M4)."""
from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

PACK_FORMAT = 1  # 1 = percent insets + dual-zone broadcast

ACTION_SAFE_PCT = 10.0
TITLE_SAFE_PCT = 20.0
CUTOFF, NUDGE, GO = 0, 128, 255
BROADCAST_CATEGORIES = frozenset({"digital_cinema", "uhd_broadcast"})


def default_pack_for_target(target: Any) -> Optional[str]:
    meta = getattr(target, "metadata", None) or {}
    if isinstance(meta, dict):
        sz = meta.get("safe_zone") or {}
        if isinstance(sz, dict) and sz.get("pack"):
            return str(sz["pack"])
    cat = getattr(target, "category", "") or ""
    if cat in BROADCAST_CATEGORIES:
        return "broadcast"
    if getattr(target, "channel", None) == "theatrical":
        return "broadcast"
    return None


def synthesize_broadcast_mask(
    width: int,
    height: int,
    *,
    action_pct: float = ACTION_SAFE_PCT,
    title_pct: float = TITLE_SAFE_PCT,
) -> "np.ndarray":
    import numpy as np

    w, h = int(width), int(height)
    mask = np.full((h, w), GO, dtype=np.uint8)

    def band(pct: float):
        t = max(0, min(h, int(round(h * pct / 100.0))))
        l = max(0, min(w, int(round(w * pct / 100.0))))
        return t, t, l, l

    at, ab, al, ar = band(action_pct)
    tt, tb, tl, tr = band(title_pct)
    if at:
        mask[:at, :] = CUTOFF
    if ab:
        mask[h - ab :, :] = CUTOFF
    if al:
        mask[:, :al] = CUTOFF
    if ar:
        mask[:, w - ar :] = CUTOFF
    if tt > at:
        mask[at:tt, al : w - ar] = NUDGE
        mask[h - tb : h - ab, al : w - ar] = NUDGE
    if tl > al:
        mask[tt : h - tb, al:tl] = NUDGE
        mask[tt : h - tb, w - tr : w - ar] = NUDGE
    return mask


def resolve_pack_mask(target: Any) -> Optional["np.ndarray"]:
    pack = default_pack_for_target(target)
    if pack != "broadcast":
        return None
    w = int(getattr(target, "width", 0) or 0)
    h = int(getattr(target, "height", 0) or 0)
    if w <= 0 or h <= 0:
        return None
    meta = getattr(target, "metadata", None) or {}
    sz = meta.get("safe_zone") if isinstance(meta, dict) else None
    action = ACTION_SAFE_PCT
    title = TITLE_SAFE_PCT
    if isinstance(sz, dict):
        action = float(sz.get("action_pct") or action)
        title = float(sz.get("title_pct") or title)
    return synthesize_broadcast_mask(w, h, action_pct=action, title_pct=title)
