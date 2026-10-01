# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/variant_gate.py
Resolve `variant:` directives against one conform target (PR-V2).

Answers a single question for a whole manifest: **which layers should
not render on this target's orientation bucket?** Pure — no I/O, no
manifest mutation, no engine state.

Returns layer keys as `(containing_comp_id, index)` tuples, never bare
indices. `layer.index` alone is not unique across a recursive manifest;
two layers in two different precomps share index 3. CLAUDE.md's
comp-scoped-identity sharp edge is explicit that a flat-index version
works on every single-comp fixture and then corrupts output the first
time it meets a real precomp manifest.

## Why exclusion is not just "set enabled=false at the end"

A variant-inactive layer must drop out of two upstream calculations, not
merely be switched off at injection:

1. **The gravity group-centroid pre-pass.** `apply_gravity` anchors a
   same-tag GROUP's centroid and adds back each member's offset from it
   (the 2026-06-29 fix for five 87N text lines collapsing onto one
   pixel). A hidden stacked headline left in that group still drags the
   centroid, so the *visible* headline lands wrong — the exact class of
   bug that fix exists to prevent, reintroduced through a side door.
2. **SOE.** Spring relaxation spaces TOP/BOTTOM/CENTER layers away from
   platform UI and from each other. An invisible layer taking part
   pushes visible ones around to avoid a collision with nothing.

Both are handled by passing this module's key set into the placement
resolution and the occlusion engine, not by post-processing.

## Sealed units

A `variant:` directive on a member of a sealed precomp or a preserving
camera scene is **refused**, with a warning, and the layer stays active.
Hiding one member of a unit whose whole contract is "all members are
treated identically" would violate THE INVARIANT (CLAUDE.md) — and
would do it silently, since the unit tests assert transform equality,
not visibility. Whole-unit variants are a real feature; they need the
directive to sit on the unit's wrapper layer and are deliberately out of
scope for v1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from core.comment_directives import parse_directives
from core.orientation import Orientation, orientation_bucket

LayerKey = Tuple[Optional[int], int]


@dataclass(frozen=True)
class VariantResolution:
    """Outcome of resolving every layer's `variant:` directive against
    one target.

    `inactive_keys` — layers that should not render on this target.
    `buckets_by_key` — the declared bucket set per layer that carried a
    directive, for the report and for `variant_buckets` on the conformed
    layer. Layers with no directive are absent (they render everywhere).
    `warnings` — surfaced by the orchestrator as run warnings.
    """

    target_bucket: str
    inactive_keys: Set[LayerKey] = field(default_factory=set)
    buckets_by_key: Dict[LayerKey, List[str]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    @property
    def any_directives(self) -> bool:
        return bool(self.buckets_by_key)

def _layer_key(layer: Any) -> LayerKey:
    return (
        getattr(layer, "containing_comp_id", None),
        int(getattr(layer, "index", 0) or 0),
    )


def resolve_variants(
    manifest: Any,
    target_width: int,
    target_height: int,
    *,
    sealed_cids: Optional[Set] = None,
    preserving_cids: Optional[Set] = None,
) -> VariantResolution:
    """Resolve every layer's `variant:` directive against the target.

    `sealed_cids` / `preserving_cids` come from the run's
    `PlacementResolution`. Pass them whenever they are known; omit them
    only on a first pass that has to run before the resolution exists,
    then re-run once it does (see `ScaleEngine.build_units`). Omitting
    them means sealed-unit membership is not checked, never that it is
    assumed absent.

    Never raises on layer content: an unparseable comment yields a
    warning and an active layer. The default must always be "renders" —
    a directive nobody can read must not make artwork vanish.
    """
    bucket = orientation_bucket(target_width, target_height)
    sealed = set(sealed_cids or ())
    preserving = set(preserving_cids or ())

    inactive: Set[LayerKey] = set()
    buckets: Dict[LayerKey, List[str]] = {}
    warnings: List[str] = []

    for layer in getattr(manifest, "layers", []) or []:
        comment = getattr(layer, "comment", None)
        if not comment:
            continue

        directives = parse_directives(comment)
        name = getattr(layer, "name", "?")

        for w in directives.warnings:
            warnings.append(f"{name}: {w}")

        if directives.variants is None:
            continue

        key = _layer_key(layer)
        buckets[key] = sorted(directives.variants)

        cid = key[0]
        if cid in sealed or cid in preserving:
            unit = "sealed precomp" if cid in sealed else "preserving camera scene"
            warnings.append(
                f"{name}: `variant:` ignored — the layer is inside a {unit}, "
                "whose members must all be treated identically. Put the "
                "directive on the layer that wraps the unit instead."
            )
            continue

        if not directives.renders_in(bucket):
            inactive.add(key)

    return VariantResolution(
        target_bucket=bucket.value,
        inactive_keys=inactive,
        buckets_by_key=buckets,
        warnings=warnings,
    )


def summarize(resolution: VariantResolution, manifest: Any) -> str:
    """One-line human summary for the progress feed and the log."""
    if not resolution.any_directives:
        return ""
    hidden = len(resolution.inactive_keys)
    declared = len(resolution.buckets_by_key)
    names = [
        getattr(layer, "name", "?")
        for layer in (getattr(manifest, "layers", []) or [])
        if _layer_key(layer) in resolution.inactive_keys
    ]
    shown = ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
    if hidden == 0:
        return (
            f"Variants: {declared} layer(s) declare a variant; all render on "
            f"{resolution.target_bucket}"
        )
    return (
        f"Variants: {hidden} of {declared} layer(s) hidden on "
        f"{resolution.target_bucket} ({shown})"
    )


__all__ = [
    "LayerKey",
    "Orientation",
    "VariantResolution",
    "resolve_variants",
    "summarize",
]
