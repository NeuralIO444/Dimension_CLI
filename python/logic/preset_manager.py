# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/logic/preset_manager.py
Dimension Engine v5.0 — Preset Lookup Bridge (CLI compatibility shim)

PresetManager maps short CLI identifiers (e.g. "tiktok", "youtube") to
Target objects from the TargetStoreManager, so --preset works from the
headless CLI without a Tkinter session.

Lookup order (first match wins):
  1. Exact id match       — "social:tiktok_9_16"
  2. Trailing-slug match  — "tiktok_9_16" (part after the colon in builtin ids)
  3. Lowercase label slug — "tiktok_9:16" → "tiktok_916"

scale_mode defaults to "Fit" and bleed_pct to 0.0 because Target objects do
not carry a per-preset scale mode. orchestrator.py always uses CLI --mode /
--bleed (CEP panel) even when --preset is set; these Preset fields are
fallbacks for callers that omit those flags.

This module exists to satisfy the import in __main__.py. It MUST NOT import
any Tkinter modules at module level (it runs in headless CLI contexts).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional

from logic.target_store import TargetStoreManager


@dataclass
class Preset:
    """Slim, CLI-friendly view of a Target.
    Stage 4: includes optional duration/fps for arbitrary targets (used for reconstruction timing)."""
    id: str
    label: str
    width: int
    height: int
    scale_mode: str = "Fit"
    bleed_pct: float = 0.0
    duration: Optional[float] = None
    fps: Optional[float] = None
    # Mirrors Target.output_name_template (v6 default
    # "{source}_{preset}"). Read by core.output_naming.resolve_output_name_for_preset
    # and the Slot 12.5 mirror-tree builder. Without this field the
    # CLI's mirror-tree path crashed with AttributeError, which was
    # silently swallowed by orchestrator's try/except as
    # cli.mirror_tree.build_failed.
    output_name_template: str = "{source}_{preset}"


def _label_slug(label: str) -> str:
    """Collapse a label into a lowercase alnum+underscore slug.
    "TikTok 9:16" → "tiktok_916"
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", label.lower().strip())
    return slug.strip("_")


class PresetManager:
    """
    Resolve CLI preset names → Preset objects.

    Wraps TargetStoreManager so CLI and UI share the same builtin catalog
    and user customs.  Index is built lazily on first access.
    """

    def __init__(self) -> None:
        self._store = TargetStoreManager()
        self._index: Optional[Dict[str, Preset]] = None

    # ── Public API ────────────────────────────────────────────────────

    @property
    def presets(self) -> Dict[str, Preset]:
        """Dict of all resolvable preset keys → Preset.
        Callers use `mgr.presets.get(key)` — returns None on miss."""
        if self._index is None:
            self._index = self._build_index()
        return self._index

    def get(self, key: str) -> Optional[Preset]:
        """Resolve a single key. Alias for presets.get(key)."""
        return self.presets.get(key)

    def get_target(self, key: str):
        """Resolve a key to the FULL underlying Target (not the slim
        Preset view). Same key space as get() — full id, trailing slug,
        or label slug. Needed by callers that require Target-only fields
        (subcategory/channel), e.g. the SOE safe-zone mask resolver's
        multi-leg lookup. Returns None for unknown keys."""
        if not hasattr(self, "_target_index"):
            index: Dict[str, object] = {}
            for target in self._store.all_targets():
                for k in self._keys_for_target(target):
                    if k and k not in index:
                        index[k] = target
            self._target_index = index
        return self._target_index.get(key)

    @staticmethod
    def _keys_for_target(target) -> list:
        """The three index keys _build_index registers, in the same
        first-write-wins order."""
        keys = [target.id]
        if ":" in target.id:
            keys.append(target.id.split(":", 1)[1])
        keys.append(_label_slug(target.label))
        return keys

    # ── Internal ──────────────────────────────────────────────────────

    def _build_index(self) -> Dict[str, Preset]:
        """Build lookup index from all known targets (builtins + customs).

        Each target gets three index entries (first-write-wins):
          • Full id          — "social:tiktok_9_16"
          • Trailing slug    — "tiktok_9_16"
          • Label slug       — "tiktok_916"
        This means short unambiguous names just work, while full ids are
        always available for scripts that need deterministic lookup.
        """
        index: Dict[str, Preset] = {}

        for target in self._store.all_targets():
            preset = Preset(
                id=target.id,
                label=target.label,
                width=target.width,
                height=target.height,
                duration=getattr(target, 'duration', None),
                fps=getattr(target, 'fps', None),
            )

            def _register(key: str) -> None:
                if key and key not in index:
                    index[key] = preset

            _register(target.id)

            # "social:tiktok_9_16" → "tiktok_9_16"
            if ":" in target.id:
                _register(target.id.split(":", 1)[1])

            # "TikTok 9:16" → "tiktok_916"
            _register(_label_slug(target.label))

        return index
