# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Stage 2 — tagging overrides (unit_overrides.json).

Artist overrides from the tagging panel are persisted beside the manifest.
`ScaleEngine` reads them at conform time; this module owns path resolution
and load/save so conform and the CEP panel share one contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional




def session_dir_for_manifest(manifest_path: str | Path) -> Path:
    """Directory containing scrape_manifest.json (typically `.dimension/`)."""
    return Path(manifest_path).resolve().parent


def unit_overrides_path(manifest_path: str | Path) -> Path:
    return session_dir_for_manifest(manifest_path) / "unit_overrides.json"


def load_unit_overrides(manifest_path: str | Path) -> Optional[dict[str, Any]]:
    """Read unit_overrides.json if present; None when file missing."""
    path = unit_overrides_path(manifest_path)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_unit_overrides(
    manifest_path: str | Path,
    overrides_payload: dict,
) -> dict[str, Any]:
    """
    Serialize placement-unit overrides to disk. Returns the payload that was written.
    The payload should be a dictionary, typically with a 'unit_overrides' key.
    """
    path = unit_overrides_path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(overrides_payload, indent=2), encoding="utf-8")
    return overrides_payload