# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).

"""
python/core/conform_options.py

Per-session conform options persisted to `.dimension/conform_options.json`.

Used to carry user overrides (e.g. camera depth mode K vs S) that
are chosen during Gardener preflight and consumed by ScaleEngine.

File lifecycle:
  - Gardener prescan WRITES the AI recommendation.
  - CEP panel toggle UPDATES the file (user override).
  - ScaleEngine (via orchestrator) READS it at conform time.
  - File is intentionally ephemeral — it represents the current
    session's choices, not persistent preferences.

Schema (all fields optional — ScaleEngine falls back to defaults):
  {
    "camera_depth_mode": "K" | "S"   // K = perspective, S = uniform
  }
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_FILENAME = "conform_options.json"


def _options_path(dimension_dir: Path) -> Path:
    return dimension_dir / _FILENAME


def load_conform_options(dimension_dir: Path) -> dict:
    """Load conform options from `.dimension/conform_options.json`.

    Returns an empty dict when the file is missing or malformed —
    callers must handle both and fall back to engine defaults.
    """
    path = _options_path(dimension_dir)
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {}


def save_conform_options(dimension_dir: Path, options: dict) -> None:
    """Write conform options atomically to `.dimension/conform_options.json`.

    Creates the parent directory if needed.  Existing options are
    replaced entirely — callers should read → merge → write if they
    need to preserve unrelated fields.
    """
    path = _options_path(dimension_dir)
    try:
        os.makedirs(str(dimension_dir), exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(options, fh, ensure_ascii=False, indent=2)
        tmp.replace(path)
    except OSError:
        pass


def get_camera_depth_mode(dimension_dir: Optional[Path]) -> Optional[str]:
    """Return the camera depth mode string from conform_options.json.

    Returns None when the file is missing or the field is absent
    (caller falls back to engine auto-detection).  Returns "K" or "S"
    when explicitly set.
    """
    if dimension_dir is None:
        return None
    opts = load_conform_options(dimension_dir)
    mode = opts.get("camera_depth_mode")
    if mode in ("K", "S"):
        return mode
    return None
