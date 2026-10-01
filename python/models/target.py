# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Target schema — the resolution + aspect-ratio entries that a designer picks
when conforming a comp.

The catalog has two halves:

  • Built-in targets — hardcoded in `python/data/target_catalog.py`, ship with
    the app, immutable, never written to disk.
  • User customs    — added via the database modal's "+ Add Custom" dialog,
    persisted to `~/Library/Application Support/NeuralIO_Dimension/dimension_targets.json`.

A Target is uniquely identified by its `id` (e.g. `builtin:dc_4k_185`,
`custom:billboard_times_sq`). The `id` is what favorites and last-used point
at, so renaming or relabeling a target must NOT change its id.
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


# Top-level drawer the target lives in. These map 1:1 to the 5 buttons on
# the main panel (Favorites is a *view*, not a category — anything can be
# starred regardless of which drawer it lives in).
TargetCategory = Literal[
    "digital_cinema",   # DCP standards + 2K..8K K-tier ladder
    "uhd_broadcast",    # 4K UHD, 8K UHD, 1080p, 720p
    "social",           # IG / FB / TT / X / Threads / Pinterest / YT
    "custom_signage",   # User-defined sizes incl. DOOH / outdoor signs
]


# Distribution channel — orthogonal to TargetCategory. Drives the safe-zone
# derivation rule (see python/logic/safe_zone_strategies.py). Optional;
# untagged targets fall through to PNG lookup only.
Channel = Literal[
    "theatrical",
    "broadcast",
    "streaming",
    "social",
    "ooh",
    "social_square",
    "social_test",
    "theatrical_test",
]


class Target(BaseModel):
    """One pickable resolution+aspect entry. Used for both built-in and
    custom targets — they share a schema so the modal grid renders them
    identically. The `source` field is the only thing that distinguishes
    them at the data layer."""

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    category: TargetCategory
    # Free-form sub-grouping inside a drawer. Examples:
    #   digital_cinema  → "dcp", "k_tier_2k", "k_tier_4k", "k_tier_8k", ...
    #   uhd_broadcast   → "uhd", "hd"
    #   social          → "instagram", "tiktok", "facebook", "youtube", ...
    #   custom_signage  → "dooh", "user"
    # The modal uses this for tab/section breaks within a drawer.
    subcategory: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    # Numeric aspect ratio (width / height). Float so 1.85, 2.39 etc round-trip
    # cleanly. Auto-computed in factory helpers if the caller doesn't supply.
    aspect_ratio: float = Field(gt=0)
    # Display label for the aspect column in the grid. Free-form because the
    # source data uses a mix: "16:9", "1.85", "Flat (1.85)", "9:16", "1.91:1".
    aspect_label: str = Field(min_length=1)
    source: Literal["builtin", "custom"]
    channel: Optional[Channel] = None

    # Stage 4 additions — arbitrary target support
    duration: Optional[float] = None  # seconds, for reconstruction timing
    fps: Optional[float] = None
    safe_zones: Optional[dict] = None  # e.g. {"top": 0.1, "bottom": 0.05} or rects
    pixel_aspect: float = 1.0
    metadata: dict = Field(default_factory=dict)

    # Slot 12.5 Stage D item 1 — Q4 Path A-minus output naming.
    #
    # Every conformed output comp's name — root + every recursive precomp
    # duplicate under Q3A's mirror tree — resolves through
    # `core.output_naming.resolve_output_name_for_preset` against this
    # template. Default `"{source}_D"` produces e.g. "Hero_D" for source
    # "Hero". Designers can author per-preset templates by editing the
    # preset YAML directly (same posture as `width` / `height` / `aspect`);
    # the Preset Library UI for visual editing is the deferred fast-follow
    # in `docs/roadmap/deferred/TODO-unified-preset-system.md`.
    #
    # Supported tokens (live in Slot 12.5):
    #   {source}  — the source comp's name (the comp being conformed)
    #   {width}   — target width  (preset.width)
    #   {height}  — target height (preset.height)
    #
    # Unknown tokens pass through unchanged for forward-compat (future
    # tokens can be added to the resolver without invalidating presets
    # that already use them). Collision-bump (`_v2`, `_v3`, ...) is
    # applied by the resolver after substitution; templates do not
    # need to encode it.
    output_name_template: str = Field(
        default="{source}_{preset}",
        min_length=1,
        description=(
            "Output naming template (v6 default: `{source}_{preset}`, "
            "e.g. `Final Comp_tiktok_video`). "
            "Substituted via core.output_naming.resolve_output_name_for_preset. "
            "Tokens: {source}, {preset}, {width}, {height}. The {preset} "
            "token resolves to a slug of the preset id with the "
            "`builtin:`/`custom:`/`user:` prefix stripped (so "
            "`builtin:tiktok_video` → `tiktok_video`)."
        ),
    )

    @field_validator("aspect_ratio")
    @classmethod
    def _round_aspect(cls, v: float) -> float:
        # Six decimals is enough for stable equality checks without losing
        # the precision of values like 1.777778 (16:9).
        return round(float(v), 6)

    @classmethod
    def make(
        cls,
        *,
        id: str,
        label: str,
        category: TargetCategory,
        subcategory: str,
        width: int,
        height: int,
        aspect_label: str,
        source: Literal["builtin", "custom"] = "builtin",
        aspect_ratio: Optional[float] = None,
        channel: Optional[Channel] = None,
        duration: Optional[float] = None,
        fps: Optional[float] = None,
        safe_zones: Optional[dict] = None,
        pixel_aspect: float = 1.0,
        metadata: Optional[dict] = None,
        output_name_template: Optional[str] = None,
    ) -> "Target":
        """Factory that auto-computes aspect_ratio from W/H when not given.
        Cleaner than scattering `width / height` calls across the catalog seed.
        Stage 4: added duration, fps, safe_zones, etc. for arbitrary targets.
        Track B / B2 (2026-08-26): output_name_template is Optional here
        so callers that don't pass it get the field's own schema default
        (`"{source}_{preset}"`) rather than an explicit None overriding it."""
        if aspect_ratio is None:
            aspect_ratio = width / height
        kwargs = dict(
            id=id,
            label=label,
            category=category,
            subcategory=subcategory,
            width=width,
            height=height,
            aspect_ratio=aspect_ratio,
            aspect_label=aspect_label,
            source=source,
            channel=channel,
            duration=duration,
            fps=fps,
            safe_zones=safe_zones,
            pixel_aspect=pixel_aspect,
            metadata=metadata or {},
        )
        if output_name_template:
            kwargs["output_name_template"] = output_name_template
        return cls(**kwargs)


class TargetStore(BaseModel):
    """On-disk state for the target database. Built-in targets are NOT in
    here — they live in code. This file only persists the things the user
    actually changes: their custom sizes, their favorites, and what they
    picked last (so the modal can pre-select it on next open)."""

    version: int = 1
    customs: List[Target] = Field(default_factory=list)
    favorites: List[str] = Field(default_factory=list)  # Target ids
    last_used: Optional[str] = None  # Target id

    @field_validator("customs")
    @classmethod
    def _customs_must_be_user_sourced(cls, v: List[Target]) -> List[Target]:
        for t in v:
            if t.source != "custom":
                raise ValueError(
                    f"TargetStore.customs may only contain source='custom' entries; "
                    f"got source='{t.source}' for id={t.id!r}"
                )
        return v
