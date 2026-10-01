# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/logic/user_support_dir.py
Single source of truth for NeuralIO Dimension user support directory resolution.

This module centralizes the directory rule (~/Library/Application Support/NeuralIO_Dimension/
on macOS, else ~/.config/NeuralIO_Dimension/) to eliminate duplication across
preferences_state.py and studio_profile_registry.py. Mirrors cep/js/working_dir.js's
"decision vs. application" split.

Public API:
    SUPPORT_DIR_NAME         — directory name constant ("NeuralIO_Dimension")
    user_support_dir()       → Path to ~/Library/Application Support/NeuralIO_Dimension/
    state_json_path()        → Path to state.json file
"""

from __future__ import annotations

from pathlib import Path


SUPPORT_DIR_NAME = "NeuralIO_Dimension"


def user_support_dir() -> Path:
    """Single source of truth for NeuralIO Dimension user support directory.

    macOS: ~/Library/Application Support/NeuralIO_Dimension/
    Other: ~/.config/NeuralIO_Dimension/ (forward-compat for v5.4)
    """
    home = Path.home()
    mac = home / "Library" / "Application Support" / SUPPORT_DIR_NAME
    if mac.parent.exists():
        return mac
    return home / ".config" / SUPPORT_DIR_NAME


def state_json_path() -> Path:
    """Path to state.json, which holds both preferences and active studio profile.

    macOS: ~/Library/Application Support/NeuralIO_Dimension/state.json
    Other: ~/.config/NeuralIO_Dimension/state.json
    """
    return user_support_dir() / "state.json"
