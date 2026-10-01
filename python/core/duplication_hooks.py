# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/duplication_hooks.py
v5.8 — Extension-hook plumbing for the Flat Duplication Engine.

This is the LOAD-BEARING decision per Q7: every place in Phase B that
touches duplicate behavior goes through a hook. Future phases extend
by adding hook implementations, NOT by editing Phase B code.

Phase B (this) ships only `DefaultHooks` — pass-through no-ops. The
machinery is here so:

  Phase C (v6.0) registers a RecursiveConformHook that, in
    `on_post_duplicate`, re-scrapes the duplicate and runs conform
    inside it.

  Phase D registers edge-case hooks (track-matte rewiring, expression
    remap, 3D camera handling) that mutate plan / dup / rewire entries
    in their respective `on_pre_*` methods.

Hook contract:
  - `on_plan_built(plan) -> plan`        : may augment / replace the plan
  - `on_pre_duplicate(dup) -> dup`       : may mutate one duplicate entry
  - `on_post_duplicate(dup, ae_uid)`     : side-effect after AE creates it
  - `on_pre_rewire(rewire) -> rewire`    : may mutate one rewire entry
  - `on_complete(plan, duplicates_made)` : final notification

A hook that raises propagates the exception up — used by Phase C to
short-circuit on a recursive-conform failure rather than corrupt the
project. Phase B's DefaultHooks never raise.
"""

from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from models.duplication_plan import (
    DuplicationPlan,
    LayerRewire,
    PrecompDuplicate,
)


@runtime_checkable
class DuplicationHook(Protocol):
    """Hook protocol. All methods have default no-op implementations
    in `DefaultHooks`; subclasses override only the methods they care
    about."""

    def on_plan_built(self, plan: DuplicationPlan) -> DuplicationPlan: ...

    def on_pre_duplicate(self, dup: PrecompDuplicate) -> PrecompDuplicate: ...

    def on_post_duplicate(self, dup: PrecompDuplicate, ae_uid: str) -> None: ...

    def on_pre_rewire(self, rewire: LayerRewire) -> LayerRewire: ...

    def on_complete(self, plan: DuplicationPlan,
                    duplicates_made: int) -> None: ...


class DefaultHooks:
    """No-op pass-through. Phase B registers exactly this and nothing
    else. Phase C/D subclass + register their own."""

    def on_plan_built(self, plan: DuplicationPlan) -> DuplicationPlan:
        return plan

    def on_pre_duplicate(self, dup: PrecompDuplicate) -> PrecompDuplicate:
        return dup

    def on_post_duplicate(self, dup: PrecompDuplicate, ae_uid: str) -> None:
        return None

    def on_pre_rewire(self, rewire: LayerRewire) -> LayerRewire:
        return rewire

    def on_complete(self, plan: DuplicationPlan,
                    duplicates_made: int) -> None:
        return None


class HookRegistry:
    """Ordered registry. Hooks fire in registration order — when two
    hooks both mutate a plan, the later registration sees the earlier
    one's output. Phase C/D ordering will matter once they ship; for
    Phase B with a single DefaultHooks the order is irrelevant."""

    def __init__(self) -> None:
        self._hooks: List[DuplicationHook] = []

    def register(self, hook: DuplicationHook) -> None:
        """Append a hook to the registry. Idempotent on identity —
        registering the same hook instance twice is silently a no-op
        so repeated init code can't double-fire."""
        if hook not in self._hooks:
            self._hooks.append(hook)

    def clear(self) -> None:
        """Wipe all hooks. Tests reach for this to start clean."""
        self._hooks.clear()

    @property
    def hooks(self) -> List[DuplicationHook]:
        return list(self._hooks)

    # ── Fire helpers ─────────────────────────────────────────────────
    # Each helper threads the value through every hook so a hook can
    # see the mutations of every prior hook. This is intentional:
    # it's how Phase D's track-matte hook can layer on top of Phase C's
    # recursive-conform hook without either knowing about the other.

    def fire_on_plan_built(self, plan: DuplicationPlan) -> DuplicationPlan:
        for h in self._hooks:
            plan = h.on_plan_built(plan)
        return plan

    def fire_on_pre_duplicate(self, dup: PrecompDuplicate) -> PrecompDuplicate:
        for h in self._hooks:
            dup = h.on_pre_duplicate(dup)
        return dup

    # Intentional extension point for a future post-duplicate hook.
    def fire_on_post_duplicate(self, dup: PrecompDuplicate, ae_uid: str) -> None:
        for h in self._hooks:
            h.on_post_duplicate(dup, ae_uid)

    # Intentional extension point for a future pre-rewire hook.
    def fire_on_pre_rewire(self, rewire: LayerRewire) -> LayerRewire:
        for h in self._hooks:
            rewire = h.on_pre_rewire(rewire)
        return rewire

    # Intentional extension point for a future completion hook.
    def fire_on_complete(self, plan: DuplicationPlan,
                         duplicates_made: int) -> None:
        for h in self._hooks:
            h.on_complete(plan, duplicates_made)


# Module-level default registry. Pipeline code grabs this; tests build
# their own HookRegistry() instance and don't touch the global.
DEFAULT_REGISTRY = HookRegistry()
DEFAULT_REGISTRY.register(DefaultHooks())
