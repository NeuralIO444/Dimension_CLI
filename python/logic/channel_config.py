# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/logic/channel_config.py
Channel-level safe-zone derivation config loader.

Reads `<repo>/config/channels.yaml` at import time and exposes the
parsed channels dict. The resolver dispatches from `target.channel`
to a strategy factory in `python/logic/safe_zone_strategies.py`,
which consults this config to instantiate the right strategy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def _config_path() -> Path:
    from logic.config_paths import config_path
    return config_path("channels.yaml")


def _load_channels() -> Dict[str, Dict[str, Any]]:
    """Parse the YAML once at import time. Missing file → empty map,
    so downstream code falls back cleanly to PNG-only lookup."""
    p = _config_path()
    if not p.is_file():
        return {}
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    channels = data.get("channels", {}) or {}
    if not isinstance(channels, dict):
        return {}
    return channels


CHANNELS: Dict[str, Dict[str, Any]] = _load_channels()


def get_channel_config(channel: str) -> Optional[Dict[str, Any]]:
    """Return the config dict for `channel`, or None if unknown."""
    if not channel:
        return None
    return CHANNELS.get(channel)
