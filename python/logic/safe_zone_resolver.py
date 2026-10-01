# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/logic/safe_zone_resolver.py
Safe-zone mask resolver.

Two public entry points:

  `resolve_mask_path(slug)` — v5.2.5 spec-named slug-based PNG lookup.
     Standalone since Slot 10 follow-up B (was an alias for the now-
     removed `resolve_mask`). Probes user override dir, then repo
     default dir; emits `MASK_MISSING` once per (slug) per run when
     both miss.

  `resolve_mask_for_target(target)` — Slot 10 target-aware multi-leg
     lookup. Tries per-target PNG override, then per-subcategory PNG
     (preserves the v5.2.5 tiktok.png path), then multi_panel gap-zone
     geometry (issue #346 — returns an ndarray synthesized from
     `core/panel_slicer.py`'s gap_zones for OOH triptych/multi-panel
     targets), then channel-derived strategy (returns ndarray directly
     via OcclusionMask's polymorphic constructor), then `MASK_MISSING`
     once per (target.id) per run.

Both share the `_missing_logged` dedup set. The two key spaces don't
collide — slug callers pass bare strings (`tiktok_video`,
`dcp_4k_full`), while target.id callers pass prefixed strings
(`builtin:tiktok_video`).

`MASK_MISSING` log line (preserved verbatim for smoke assertions):
  log.info("MASK_MISSING", extra={"preset_id", "checked_user", "checked_repo"})
The target-aware path also adds `target_id` and `channel` extras.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Set, Union, TYPE_CHECKING

from core.logger import log

if TYPE_CHECKING:
    import numpy as np
    from models.target import Target

try:
    import numpy as np
    # yaml is imported for its side effect on this probe only: if it is
    # missing, the ImportError below must flip the availability flag.
    # It is deliberately not referenced afterwards, so F401 is expected.
    import yaml  # noqa: F401
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False


_USER_OVERRIDE_DIR = Path.home() / ".config" / "NeuralIO_Dimension" / "safe_zones"


_missing_logged: Set[str] = set()



# SOE-4: Zone color constants moved to module level for clarity and efficiency.
_ZONE_COLORS = {
    "CUTOFF": 0,
    "NUDGE": 128,
    "TITLE_SAFE": 255,  # GO color
}

def _repo_default_dir() -> Path:
    import sys
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / "config" / "safe_zones"
    return Path(__file__).resolve().parents[2] / "config" / "safe_zones"


def _generate_mask_from_vectors(definition: list, width: int, height: int) -> "np.ndarray":
    """
    Generates a safe-zone mask from a list of vector-based zone definitions.
    SOE-2: Initial implementation supports CUTOFF zones only.
    SOE-3: Added NUDGE zone support.
    SOE-4: Added TITLE_SAFE zone support.
    """
    # GO = 255 (white), NUDGE = 128 (gray), CUTOFF = 0 (black)
    mask = np.full((height, width), 255, dtype=np.uint8)

    if not isinstance(definition, list):
        return mask

    # SOE-4: Separate title-safe zones to handle them as a base layer.
    title_safe_zones = [z for z in definition if isinstance(z, dict) and z.get("zone") == "TITLE_SAFE"]
    other_zones = [z for z in definition if isinstance(z, dict) and z.get("zone") != "TITLE_SAFE"]

    # If there's a title-safe definition, the default area outside it is NUDGE.
    if title_safe_zones:
        mask.fill(_ZONE_COLORS["NUDGE"])

    # Process title-safe zones first, drawing the GO area.
    # Then process other zones, which will correctly overwrite the edges.
    for zone in title_safe_zones + other_zones:
        if not isinstance(zone, dict):
            continue

        zone_type = zone.get("zone")
        bounds = zone.get("bounds")
        color = _ZONE_COLORS.get(zone_type)

        if color is None or not (isinstance(bounds, list) and len(bounds) == 4):
            continue

        try:
            # Unpack and scale normalized coordinates to pixel values
            x1_norm, y1_norm, x2_norm, y2_norm = bounds
            x1 = int(x1_norm * width)
            y1 = int(y1_norm * height)
            x2 = int(x2_norm * width)
            y2 = int(y2_norm * height)
            # Draw the rectangle for the zone
            mask[y1:y2, x1:x2] = color
        except (TypeError, ValueError):
            continue  # Skip malformed bounds
    return mask


class _VectorStrategy:
    """A strategy-like object that wraps _generate_mask_from_vectors."""

    def __init__(self, zones: list):
        self.zones = zones

    def produce_mask(self, target: "Target") -> "np.ndarray":
        """Generates a mask from the vector definition."""
        return _generate_mask_from_vectors(self.zones, target.width, target.height)


def _slug_filename(slug: str) -> str:
    """target.id contains a colon (`builtin:dcp_4k_full`) and possibly
    a slash; both are problematic on disk. Map to underscores."""
    return slug.replace(":", "_").replace("/", "_") + ".png"


def _slug_lookup(slug: str) -> Optional[Path]:
    """Probe user-override dir first, then repo default dir, for
    `<slug>.png`. Return the first existing file, or None.

    Symlink behaviour (v5.2.5): `Path.is_file()` resolves the symlink
    and returns False on dangling links, which is the contract SOE
    expects."""
    if not slug:
        return None
    filename = _slug_filename(slug)
    user_path = _USER_OVERRIDE_DIR / filename
    if user_path.is_file():
        return user_path
    repo_path = _repo_default_dir() / filename
    if repo_path.is_file():
        return repo_path
    return None


def resolve_mask_path(slug: str) -> Optional[Path]:
    """v5.2.5 spec — slug-based PNG lookup.

    Lookup order:
      1. User override — `~/.config/NeuralIO_Dimension/safe_zones/<slug>.png`
      2. Repo default — `<repo>/config/safe_zones/<slug>.png`
      3. None → `MASK_MISSING` log once per (slug) per run

    Distinct from `resolve_mask_for_target` — this is for callers that
    have a preset slug directly, not a full Target object. The
    target-aware lookup (channel-derived rule, per-target PNG
    override, per-subcategory PNG fallback) lives in
    `resolve_mask_for_target`.

    Symlink behaviour: dangling user-override symlinks fall through
    to the repo default because `Path.is_file()` returns False on
    broken symlinks.

    History: was an alias for `resolve_mask` v5.2.5–Slot 10; Slot 10
    follow-up B deleted the bare `resolve_mask` and promoted this to
    standalone. The MASK_MISSING log + dedup contract that lived on
    `resolve_mask` pre-Slot-10 is restored here."""
    if not slug:
        return None
    hit = _slug_lookup(slug)
    if hit is not None:
        return hit
    # MASK_MISSING — deduped per (slug) per run, shared dedup set
    # with resolve_mask_for_target (key spaces don't collide).
    if slug not in _missing_logged:
        _missing_logged.add(slug)
        filename = _slug_filename(slug)
        log.info(
            "MASK_MISSING",
            extra={
                "preset_id": slug,
                "checked_user": str(_USER_OVERRIDE_DIR / filename),
                "checked_repo": str(_repo_default_dir() / filename),
            },
        )
    return None


def resolve_mask_for_target(
    target: "Target",
) -> Optional[Union[Path, "np.ndarray"]]:
    """Full-target lookup with the Slot 10 ordering (+ issue #370 fix):
       1. Per-target PNG override — `<target.id>.png` (returns Path)
       1b. Same, with the id's `<namespace>:` prefix stripped —
           `<target.id split on first ':'>.png` (returns Path)
       2. Per-subcategory PNG (legacy) — `<target.subcategory>.png` (returns Path)
       3. Channel-derived rule — strategy ndarray returned directly
          (no disk round-trip; OcclusionMask's polymorphic constructor
          accepts the ndarray as of follow-up A)
       4. None → MASK_MISSING log once per (target.id) per run
    """
    # Leg 1 — per-target PNG override (target.id slugified for disk).
    hit = _slug_lookup(target.id)
    if hit is not None:
        return hit

    # Leg 1b — same lookup with the id's namespace prefix stripped
    # (`builtin:instagram_story` -> `instagram_story`). Target ids are
    # namespaced (`<namespace>:<slug>`), but existing mask assets on
    # disk are named by the bare slug alone, not the full namespaced
    # id (issue #370: `instagram_story.png`/`youtube_shorts.png` exist
    # on disk but only matched by Leg 1 once this prefix is stripped).
    # Purely additive fallback -- Leg 1's full-id lookup still runs
    # first and wins, so this can only add matches, never change one.
    if ":" in target.id:
        bare_slug = target.id.split(":", 1)[1]
        hit = _slug_lookup(bare_slug)
        if hit is not None:
            return hit

    # Leg 2 — per-subcategory PNG (legacy contract, preserves
    # tiktok.png).
    if target.subcategory:
        hit = _slug_lookup(target.subcategory)
        if hit is not None:
            return hit


    # Leg 2.4 — numeric insets (issue #522). YAML is the spec.
    # Placement: after subcategory PNG (Leg 2), before multi_panel gap (2.5).
    from logic.safe_zone_insets import resolve_inset_mask
    inset = resolve_inset_mask(target)
    if inset is not None:
        log.info(
            "Safe-zone mask synthesized from insets",
            extra={"target_id": target.id, "mask_shape": list(inset.shape)},
        )
        return inset

    # Leg 2.5 — multi_panel gap-zone geometry (issue #346). A multi-panel
    # OOH target (e.g. a 3-panel transit triptych) has no safe-zone PNG
    # asset for any transit_triptychs spec today, so without this leg
    # it falls straight through to Leg 4 (MASK_MISSING) and gets zero
    # SOE protection -- TYPE/LEGALS text could land directly across a
    # physical pillar gap with no warning. `core/panel_slicer.py`'s own
    # `gap_zones` already carry that geometry; synthesize an in-memory
    # mask from it (gap_zones -> CUTOFF, everywhere else GO) instead of
    # looking for a PNG that will never exist for this target class.
    # Runs after the PNG legs (1/1b/2) so a human-authored override for
    # this exact target still wins, and before the channel-derived rule
    # (Leg 3) since this geometry is specific to the target, not a
    # channel-wide policy.
    multi_panel_meta = (getattr(target, "metadata", None) or {}).get("multi_panel")
    if multi_panel_meta:
        from core.panel_slicer import PanelSlicingPlanner, synthesize_gap_zone_mask

        base_name = target.id or target.label or "PANEL"
        plan = PanelSlicingPlanner.from_metadata(multi_panel_meta, base_name).plan()
        if plan.master_width == target.width and plan.master_height == target.height:
            mask_array = synthesize_gap_zone_mask(plan)
            log.info(
                "Safe-zone mask synthesized from multi_panel gap zones",
                extra={
                    "target_id": target.id,
                    "gap_zones": len(plan.gap_zones),
                    "mask_shape": list(mask_array.shape),
                },
            )
            return mask_array
        log.warning(
            "multi_panel plan dimensions do not match target — gap-zone "
            "mask skipped, falling through to the normal safe-zone lookup",
            extra={
                "target_id": target.id,
                "plan_dims": [plan.master_width, plan.master_height],
                "target_dims": [target.width, target.height],
            },
        )

    # Leg 3 — channel-derived strategy. Returns the strategy's
    # uint8 ndarray directly; the caller hands it straight to
    # OcclusionMask.
    channel = getattr(target, "channel", None)
    if channel:
        # Import here to avoid pulling numpy / yaml at module-import
        # time for callers that only need the legacy slug lookup.
        from logic.channel_config import get_channel_config
        config = get_channel_config(channel)
        if config and config.get("strategy") == "derived_from_vectors":
            strategy = _VectorStrategy(config.get("zones", []))
            mask_array = strategy.produce_mask(target)
            if mask_array is not None:
                log.info(
                    "Safe-zone mask derived from channel rule",
                    extra={
                        "target_id": target.id,
                        "channel": channel,
                        "strategy": "VectorStrategy",
                        "zones_count": len(config.get("zones", [])),
                        "mask_shape": list(mask_array.shape),
                    },
                )
                return mask_array

        from logic.safe_zone_strategies import get_strategy_for_channel
        strategy = get_strategy_for_channel(channel)
        if strategy is not None:
            mask_array = strategy.produce_mask(target)
            if mask_array is not None:
                log.info(
                    "Safe-zone mask derived from channel rule",
                    extra={
                        "target_id": target.id,
                        "channel": channel,
                        "strategy": type(strategy).__name__,
                        "inset": getattr(strategy, "action_safe_inset", None),
                        "mask_shape": list(mask_array.shape),
                    },
                )
                return mask_array

    # Leg 4 — MASK_MISSING log, deduped per target.id per run.
    if target.id not in _missing_logged:
        _missing_logged.add(target.id)
        target_id_filename = _slug_filename(target.id)
        log.info(
            "MASK_MISSING",
            extra={
                "preset_id": target.subcategory or target.id,
                "target_id": target.id,
                "channel": channel,
                "checked_user": str(_USER_OVERRIDE_DIR / target_id_filename),
                "checked_repo": str(_repo_default_dir() / target_id_filename),
            },
        )
    return None


def available_masks() -> Set[str]:
    """Return the slugs of every PNG mask currently resolvable
    (union of repo default + user override). Does NOT include
    channel-derived masks — those are produced on demand."""
    out: Set[str] = set()
    for d in (_repo_default_dir(), _USER_OVERRIDE_DIR):
        if d.exists() and d.is_dir():
            for p in d.glob("*.png"):
                out.add(p.stem)
    return out


def reset_missing_log() -> None:
    """Test hook — empties the once-per-run log dedup set so tests
    can re-assert the log behaviour from a clean state."""
    _missing_logged.clear()
