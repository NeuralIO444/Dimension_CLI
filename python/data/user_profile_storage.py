# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
user_profile_storage.py
Data model and disk storage layer for user-created Studio Profile overrides.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class UserProfile(BaseModel):
    """UserProfile model representing overrides that extend a base profile."""
    id: str = Field(..., description="Filesystem-safe identifier")
    name: str = Field(..., description="Display name of the user profile")
    base_profile_id: str = Field(..., description="ID of the base profile to extend")
    overrides: Dict[str, Any] = Field(default_factory=dict, description="Overrides for prefixes, type_overrides, and safe_area")
    created_at: str
    updated_at: str


class UserProfileStore:
    """Disk store for saving, loading, and deleting user profile JSON files."""

    def __init__(self, user_dir: Path):
        self.user_dir = user_dir

    def save(self, profile: UserProfile) -> str:
        """Atomically persist a UserProfile to disk."""
        self.user_dir.mkdir(parents=True, exist_ok=True)
        target = self.user_dir / f"{profile.id}.json"
        tmp = target.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(profile.model_dump(), f, indent=2)
        os.replace(tmp, target)
        return str(target)

    def load(self, profile_id: str) -> Optional[UserProfile]:
        """Load a UserProfile from disk by ID."""
        target = self.user_dir / f"{profile_id}.json"
        if not target.is_file():
            return None
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            return UserProfile.model_validate(data)
        except Exception:
            return None

    def delete(self, profile_id: str) -> bool:
        """Delete a UserProfile from disk by ID. Returns True if deleted."""
        target = self.user_dir / f"{profile_id}.json"
        if target.is_file():
            try:
                target.unlink()
                return True
            except OSError:
                return False
        return False
