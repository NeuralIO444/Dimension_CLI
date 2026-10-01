# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial. See LICENSE.

"""Locate bundled runtime config (profiles, masks, channels, tag registry).

Resolution order:
  1. Frozen PyInstaller tree: ``sys._MEIPASS / config``.
  2. Installed wheel: ``dimension/bundled_config`` next to the ``logic``
     package (copied in by ``setup.py`` at build time).
  3. Checkout: repo-root ``config/`` (``parents[2]`` from this file).

Checkout tests keep working. A pip install does not depend on the
repo still being on disk.
"""

from __future__ import annotations

import sys
from pathlib import Path


def bundled_config_dir() -> Path:
    """Directory that contains ``channels.yaml``, ``profiles/``, ``safe_zones/``."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "config"

    here = Path(__file__).resolve()
    installed = here.parents[1] / "dimension" / "bundled_config"
    if (installed / "channels.yaml").is_file():
        return installed

    checkout = here.parents[2] / "config"
    if (checkout / "channels.yaml").is_file():
        return checkout

    return installed


def config_path(*parts: str) -> Path:
    """Path under the resolved config dir. Does not check existence."""
    return bundled_config_dir().joinpath(*parts)
