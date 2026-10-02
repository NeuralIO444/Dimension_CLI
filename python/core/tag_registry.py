# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/tag_registry.py
v5.10 — single source of truth for Dimension's tag vocabulary.

This module is THE TAG-REGISTRATION BOUNDARY. Every other module
that needs tag knowledge (label colors, AE label indices, gravity
defaults, valid-tag sets) imports from here. No other module
hardcodes tag identifiers.

The on-disk source is `config/tag_registry.yaml`. The JSX side
reads from `Scripts/Dimension_Assets/tag_registry.jsx`, generated
from the same YAML via `python/scripts/generate_jsx_tag_mirror.py`.

Audit Finding #1 (`docs/audits/integration-contract-2026-04-26.md`)
identified three independently-evolved tag stores
(`tag_labels.py`, `tag_colors.py`, `SovCore_Layer.jsx`'s TAG_TO_LABEL)
and the contract drift between them. This registry consolidates all
three.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Constants ──────────────────────────────────────────────────────────

def _registry_yaml_path() -> Path:
    from logic.config_paths import config_path
    return config_path("tag_registry.yaml")


_REGISTRY_YAML_PATH = _registry_yaml_path()

_VALID_GRAVITIES = frozenset({
    "top", "bottom", "center", "leftMid", "rightMid", "fill", "none",
})

_VALID_SEMANTIC_CLASSES = frozenset({
    "text", "graphic", "structural", "overlay", "bg",
})

# Pre-#117 per-alias AE label colours (git 665eee5^:config/tag_registry.yaml)
# that the 4-tag vocabulary no longer claims. Layers physically coloured
# with these indices must still round-trip to the canonical tag on
# rescan. Live registry colours take precedence when both map the same
# index.
LEGACY_LABEL_COLOR_MAP: dict[int, str] = {
    2: "TOP",       # Yellow (legacy TT/TYPE)
    3: "CENTER",    # ARTWORK
    4: "CENTER",    # SUP
    5: "CENTER",    # BOXART
    6: "CENTER",    # ANIMATION
    9: "CENTER",    # Green (Live Type / Hero)
    12: "CENTER",   # BODY
    14: "CENTER",   # LOGO
    15: "GUIDE",    # pre-Sprint-1 GUIDE colour
}


# ── Models ─────────────────────────────────────────────────────────────


class TagDefinition(BaseModel):
    """Canonical record for one tag in the vocabulary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    display_name: str
    abbrev: str
    color_hex: str
    ae_label_color: int
    default_gravity: str
    semantic_class: Optional[str] = None
    description: str = ""
    aliases: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("id", "abbrev")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be non-empty")
        return v

    @field_validator("color_hex")
    @classmethod
    def _hex_color(cls, v: str) -> str:
        if not (v.startswith("#") and len(v) == 7):
            raise ValueError(f"color_hex must be #RRGGBB, got {v!r}")
        try:
            int(v[1:], 16)
        except ValueError as e:
            raise ValueError(f"color_hex {v!r} is not valid hex") from e
        return v

    @field_validator("ae_label_color")
    @classmethod
    def _label_range(cls, v: int) -> int:
        if not (0 <= v <= 16):
            raise ValueError(f"ae_label_color must be 0..16, got {v}")
        return v

    @field_validator("default_gravity")
    @classmethod
    def _gravity_enum(cls, v: str) -> str:
        if v not in _VALID_GRAVITIES:
            raise ValueError(
                f"default_gravity {v!r} not in {sorted(_VALID_GRAVITIES)}"
            )
        return v

    @field_validator("semantic_class")
    @classmethod
    def _class_enum(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if v not in _VALID_SEMANTIC_CLASSES:
            raise ValueError(
                f"semantic_class {v!r} not in "
                f"{sorted(_VALID_SEMANTIC_CLASSES)}"
            )
        return v

    @field_validator("aliases", mode="before")
    @classmethod
    def _aliases_to_tuple(cls, v):
        if v is None:
            return tuple()
        return tuple(v)


class TagRegistry(BaseModel):
    """Loaded view of the canonical tag registry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str
    tags: tuple[TagDefinition, ...]

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_to_tuple(cls, v):
        return tuple(v) if v is not None else tuple()

    @field_validator("tags")
    @classmethod
    def _no_duplicates(cls, v: tuple[TagDefinition, ...]):
        seen_ids: set[str] = set()
        seen_aliases: dict[str, str] = {}
        for t in v:
            if t.id in seen_ids:
                raise ValueError(f"duplicate tag id: {t.id!r}")
            seen_ids.add(t.id)
            for a in t.aliases:
                if a in seen_ids:
                    raise ValueError(
                        f"alias {a!r} on {t.id!r} collides with "
                        f"another canonical tag id"
                    )
                if a in seen_aliases:
                    raise ValueError(
                        f"alias {a!r} declared on both "
                        f"{seen_aliases[a]!r} and {t.id!r}"
                    )
                seen_aliases[a] = t.id
        for a in seen_aliases:
            if a in seen_ids:
                raise ValueError(
                    f"alias {a!r} also exists as canonical id"
                )
        return v

    @field_validator("tags")
    @classmethod
    def _unique_label_colors(cls, v: tuple[TagDefinition, ...]):
        """AE label color must uniquely identify a tag.

        Pre-2026-04-28 the registry had two collisions (TT/SUP on
        label 2, LGL/DISC on label 10). When a user labelled a layer
        via AE's label-color shortcut, the bimap walked YAML order
        and returned the first match — predictable for the
        codebase, surprising for the user. This validator makes the
        registry refuse to load if any non-zero label is claimed by
        more than one tag, so a future edit can't silently
        reintroduce the collision.

        Label 0 is the documented "no label" sentinel: NULL and
        UNCLASS both claim it intentionally because both mean "no
        AE label color set." That case is excluded from the
        uniqueness rule.
        """
        seen: dict[int, str] = {}
        for t in v:
            label = t.ae_label_color
            if label == 0:
                # Intentional shared sentinel — NULL and UNCLASS
                # both use 0 to mean "no AE label color."
                continue
            if label in seen:
                raise ValueError(
                    f"AE label color {label} claimed by both "
                    f"{seen[label]!r} and {t.id!r} — every non-zero "
                    f"label must uniquely identify a tag. "
                    f"Reassign one of them to an unused index "
                    f"(0–16, see config/tag_registry.yaml)."
                )
            seen[label] = t.id
        return v

    # ── Lookup helpers ────────────────────────────────────────────────

    def by_id(self, tag_id: str) -> Optional[TagDefinition]:
        """Exact-match lookup. None for unknown tags."""
        if not tag_id:
            return None
        return self._by_id_map().get(tag_id)

    def normalize(self, tag_or_alias: Optional[str]) -> Optional[str]:
        """Resolve a tag id OR a known alias to its canonical id.

        Returns None for empty input or for completely unknown tags.
        Use this anywhere external data (manifests, profiles, user
        input) hands you a tag string."""
        if not tag_or_alias:
            return None
        s = str(tag_or_alias)
        if s in self._by_id_map():
            return s
        return self._alias_map().get(s)

    def all_ids(self) -> frozenset[str]:
        """Set of every canonical tag id (excludes aliases)."""
        return frozenset(self._by_id_map().keys())

    def all_aliases(self) -> frozenset[str]:
        """Set of every legacy alias string (sum across all tags)."""
        return frozenset(self._alias_map().keys())

    def color_for(self, tag_id: str) -> Optional[str]:
        """Hex color string. None for unknown tags."""
        t = self.by_id(self.normalize(tag_id) or "")
        return t.color_hex if t else None

    def label_for(self, tag_id: str) -> int:
        """AE label index. Returns 0 (no label) for unknown tags so
        the UI degrades gracefully."""
        t = self.by_id(self.normalize(tag_id) or "")
        return t.ae_label_color if t else 0

    def gravity_for(self, tag_id: str) -> Optional[str]:
        """Advisory default gravity. Profile rules override this in
        the conform pipeline. None for unknown tags."""
        t = self.by_id(self.normalize(tag_id) or "")
        return t.default_gravity if t else None

    def label_to_id(self, label: int) -> Optional[str]:
        """AE label index → canonical tag id. Each non-zero label
        uniquely identifies a tag — enforced by the
        `_unique_label_colors` validator on registry load. Label 0
        is the "no label" sentinel and returns None.

        The manifest-level `#TAG` comment marker remains the source
        of truth on read; this lookup is the fallback when only the
        AE label color is available."""
        if label is None or label == 0:
            return None
        for t in self.tags:
            if t.ae_label_color == int(label):
                return t.id
        legacy = LEGACY_LABEL_COLOR_MAP.get(int(label))
        if legacy:
            return self.normalize(legacy)
        return None

    # ── Internal cached maps ──────────────────────────────────────────

    @lru_cache(maxsize=1)
    def _by_id_map(self) -> dict[str, TagDefinition]:
        return {t.id: t for t in self.tags}

    @lru_cache(maxsize=1)
    def _alias_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for t in self.tags:
            for a in t.aliases:
                out[a] = t.id
        return out


# ── Loader ─────────────────────────────────────────────────────────────


def _load_registry(path: Path = _REGISTRY_YAML_PATH) -> TagRegistry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(
            f"tag_registry.yaml must be a mapping at top level, "
            f"got {type(raw).__name__}"
        )
    return TagRegistry.model_validate(raw)


# Module-level singleton. Loaded once at import time. The conform
# pipeline runs hot, so callers should NOT call _load_registry()
# repeatedly — read from REGISTRY.
REGISTRY: TagRegistry = _load_registry()


def reload() -> TagRegistry:
    """Reload the registry from disk. For tests + a future hot-
    reload feature only — never call from runtime hot paths."""
    global REGISTRY
    REGISTRY = _load_registry()
    return REGISTRY


__all__ = [
    "LEGACY_LABEL_COLOR_MAP",
    "REGISTRY",
    "TagDefinition",
    "TagRegistry",
    "reload",
]
