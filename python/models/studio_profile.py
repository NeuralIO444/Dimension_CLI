# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/models/studio_profile.py
Dimension Studio Profile — schema for the v5.2 Nomenclature Registry.

A studio profile maps **prefix patterns on layer names** to a deterministic
chain of conform rules:

    layer name  →  prefix match  →  tag + gravity + scale + weight

Stored as YAML at:
    ~/Library/Application Support/Dimension/profiles/<id>.yaml

Inheritance: a profile can `extends` another profile by id; rules from
the parent are loaded first and any rule whose `match` collides with a
child rule is overridden by the child. `weight: pinned` rules cannot be
overridden by downstream sources (preset bundles, per-comp overrides).

This file is the schema-only layer. Loading + persistence lives in
`python/logic/studio_profile_registry.py` so the model stays import-light
(no filesystem dependency).
"""

from __future__ import annotations

from typing import List, Dict, Optional, Literal, Any
from pydantic import BaseModel, Field, field_validator


# Tag vocabulary mirrors the design HTML — full list intentionally
# permissive so authors can extend per-studio without code edits.
# Surveyor only consumes the canonical set; unknown tags pass through.
TagCode = str

GravityCode = Literal[
    "top", "bottom", "center",
    "centerH", "leftMid", "rightMid",
    "topC", "bottomC", "fill",
]

WeightCode = Literal["high", "normal", "pinned", "low"]


class ProfileRule(BaseModel):
    """One prefix→tag rule."""
    match: str = Field(..., description="Layer-name prefix, e.g. 'TITLE_'")
    tag: TagCode
    gravity: GravityCode = "center"
    scale: float = 1.0
    weight: WeightCode = "normal"

    # Issue #331 — opt-in gate for alpha-hull-derived optical-centroid
    # gravity (core.gravity.apply_gravity's HP-01 block). Defaults False:
    # a custom studio profile is a deliberately hand-tuned placement for a
    # specific client, so shifting it based on newly-available artwork
    # bounds needs an explicit decision per rule, not a silent default.
    # Contrast core.gravity._BaselineRule, whose equivalent defaults True —
    # HP-01 already shipped unconditionally for the untagged/baseline path
    # (see test_artwork_bounds_gravity.py's TestArtworkBoundsThroughRealConform,
    # live since #436/#440) before this field existed, so baseline keeps
    # that behavior rather than silently regressing it.
    use_artwork_boundary: bool = Field(
        default=False,
        description="Opt in to alpha-hull optical-centroid gravity (HP-01) for this rule.",
    )

    @field_validator("scale")
    @classmethod
    def _scale_in_range(cls, v: float) -> float:
        if v < 0.0 or v > 5.0:
            raise ValueError(f"scale {v} outside [0, 5]")
        return float(v)

    @field_validator("match")
    @classmethod
    def _match_is_prefix(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("rule.match must be a non-empty prefix")
        return v


class SafeArea(BaseModel):
    """Per-edge inset as fraction of the target frame (0.0–1.0).
    Total horizontal/vertical inset must leave a non-empty content box."""
    top: float = 0.05
    right: float = 0.05
    bottom: float = 0.05
    left: float = 0.05

    @field_validator("top", "right", "bottom", "left")
    @classmethod
    def _edge_in_range(cls, v: float) -> float:
        if v < 0.0 or v >= 0.5:
            raise ValueError(f"safe-area edge {v} must be in [0, 0.5)")
        return float(v)


class StudioProfile(BaseModel):
    """One studio's policy. Loaded from a YAML file under the registry
    directory. `extends` chains resolve at load time in the registry —
    by the time this object lives in memory, its `prefixes` and
    `type_overrides` already include parent contributions."""

    id: str = Field(..., description="filesystem-safe slug, matches filename")
    display_name: str = Field(default="")
    revision: str = Field(default="v0.1.0")
    updated: str = Field(default="")            # ISO date, free-form
    author: str = Field(default="")
    description: str = Field(default="")
    color: str = Field(default="#4a7e9a", description="studio mark color, hex")
    extends: Optional[str] = Field(
        default=None,
        description="parent profile id; rules merge with child overriding",
    )

    prefixes: List[ProfileRule] = Field(default_factory=list)
    type_overrides: Dict[TagCode, float] = Field(default_factory=dict)
    safe_area: SafeArea = Field(default_factory=SafeArea)
    extreme_ribbon: Optional[Dict[str, Any]] = Field(
        default=None,
        description="TASK-P2-04 (#252): Optional extreme ribbon scaling configuration (aspect ratio >= 10.0).",
    )

    @field_validator("id")
    @classmethod
    def _id_slug(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("profile.id must be non-empty")
        return v

    # ── Resolution API ────────────────────────────────────────────

    def resolve(self, layer_name: str) -> Optional[ProfileRule]:
        """Return the first prefix rule that matches `layer_name`, or
        None if nothing matches. Order matters: rules higher in the
        list win. Profiles ship with most-specific first; the registry
        merge preserves child-before-parent order."""
        if not layer_name:
            return None
        for rule in self.prefixes:
            if layer_name.startswith(rule.match):
                return rule
        return None

def merge_extends(
    child: StudioProfile,
    parent: StudioProfile,
) -> StudioProfile:
    """Merge a parent profile into a child. Used by the registry
    loader when `extends:` resolves.

    Semantics:
      - prefixes: parent rules first, then child rules. Resolve walks
        the merged list top-down so child rules override parent rules
        with the same `match` (because resolve returns the first hit
        and we put child rules AFTER parent — wait, no: child rules
        need to win, so we put them FIRST, then parent rules).
      - type_overrides: parent first, child overrides on key collision.
      - safe_area: child wins entirely if any edge differs from default.
      - other metadata: child's value wins (its own id, name, revision).

    Pinned-weight parent rules are protected: a child rule cannot
    override a parent rule with weight=pinned unless the child rule
    is also pinned. This is the v5.2 contract — studio masters can
    lock policy that bundles can't subvert.
    """
    # Index parent pinned rules so we can detect attempted overrides.
    parent_pinned: Dict[str, ProfileRule] = {
        r.match: r for r in parent.prefixes if r.weight == "pinned"
    }

    # Child rules first (higher priority in resolve), but a child rule
    # whose match collides with a pinned parent rule is dropped.
    merged_prefixes: List[ProfileRule] = []
    seen_matches: set = set()
    for r in child.prefixes:
        if r.match in parent_pinned and r.weight != "pinned":
            # Silently ignore — caller can re-emit with weight=pinned to
            # explicitly take the lock.
            continue
        merged_prefixes.append(r)
        seen_matches.add(r.match)
    for r in parent.prefixes:
        if r.match in seen_matches:
            continue
        merged_prefixes.append(r)

    # Type overrides: parent wins for pinned; otherwise child wins.
    merged_overrides: Dict[TagCode, float] = dict(parent.type_overrides)
    for tag, value in child.type_overrides.items():
        merged_overrides[tag] = value

    # Safe-area: if child uses defaults, inherit parent's; otherwise child wins.
    default_sa = SafeArea()
    child_sa_is_default = (
        child.safe_area.top == default_sa.top
        and child.safe_area.right == default_sa.right
        and child.safe_area.bottom == default_sa.bottom
        and child.safe_area.left == default_sa.left
    )
    safe_area = parent.safe_area if child_sa_is_default else child.safe_area

    return StudioProfile(
        id=child.id,
        display_name=child.display_name or parent.display_name,
        revision=child.revision,
        updated=child.updated,
        author=child.author or parent.author,
        description=child.description or parent.description,
        color=child.color or parent.color,
        extends=child.extends,
        prefixes=merged_prefixes,
        type_overrides=merged_overrides,
        safe_area=safe_area,
    )
