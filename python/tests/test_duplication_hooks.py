# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_duplication_hooks.py
v5.8 — coverage for DuplicationHook + HookRegistry + DefaultHooks.

The hook system is the load-bearing extension surface for Phase C/D.
These tests pin its behaviors:

  - DefaultHooks pass through (input == output)
  - HookRegistry fires every registered hook in order
  - Threading: a hook in slot N sees mutations from hook in slot N-1
  - Idempotent registration (same instance twice is no-op)
  - Hooks can short-circuit by raising
  - clear() wipes the registry
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


def _make_plan():
    from models.duplication_plan import DuplicationPlan
    return DuplicationPlan(
        session_id="sess-test",
        session_folder="From Dimensions/TEST_2026",
        preset_id="TEST",
        target_dimensions=(1080, 1920),
        aspect_ratio_changed=True,
        duplicates=[],
        rewires=[],
        depth_max=0,
    )


def _make_dup(uid="100"):
    from models.duplication_plan import PrecompDuplicate
    return PrecompDuplicate(
        original_uid=uid,
        original_name="Hero_Title",
        duplicate_name="Hero_Title_1080x1920",
        target_folder_path="From Dimensions/TEST_2026",
        original_width=1920,
        original_height=1080,
        reason="SHARED",
        is_protected=False,
        will_be_skipped=False,
    )


def _make_rewire():
    from models.duplication_plan import LayerRewire
    return LayerRewire(
        conformed_layer_uid="layer-uid",
        conformed_layer_name="hero",
        original_source_uid="100",
        new_source_uid="dup:100",
    )


# ── DefaultHooks ──────────────────────────────────────────────────────


class TestDefaultHooks:
    def test_default_hooks_pass_through_plan(self):
        from core.duplication_hooks import DefaultHooks
        h = DefaultHooks()
        plan = _make_plan()
        assert h.on_plan_built(plan) is plan

    def test_default_hooks_pass_through_dup(self):
        from core.duplication_hooks import DefaultHooks
        dup = _make_dup()
        assert DefaultHooks().on_pre_duplicate(dup) is dup

    def test_default_hooks_pass_through_rewire(self):
        from core.duplication_hooks import DefaultHooks
        rw = _make_rewire()
        assert DefaultHooks().on_pre_rewire(rw) is rw

    def test_default_hooks_post_and_complete_return_none(self):
        from core.duplication_hooks import DefaultHooks
        h = DefaultHooks()
        # No-ops — must not raise, return None.
        assert h.on_post_duplicate(_make_dup(), "ae-id-42") is None
        assert h.on_complete(_make_plan(), 0) is None


# ── HookRegistry ──────────────────────────────────────────────────────


class TestHookRegistry:
    def test_fire_on_plan_built_calls_each_hook(self):
        from core.duplication_hooks import DefaultHooks, HookRegistry

        class CountingHook(DefaultHooks):
            def __init__(self):
                self.calls = 0
            def on_plan_built(self, plan):
                self.calls += 1
                return plan

        reg = HookRegistry()
        a = CountingHook(); b = CountingHook()
        reg.register(a); reg.register(b)
        reg.fire_on_plan_built(_make_plan())
        assert a.calls == 1
        assert b.calls == 1

    def test_fire_threads_value_through_hooks_in_order(self):
        from core.duplication_hooks import DefaultHooks, HookRegistry

        class TaggingHook(DefaultHooks):
            def __init__(self, tag):
                self.tag = tag
            def on_plan_built(self, plan):
                # Append our tag to the session_id so we can observe order.
                return plan.model_copy(update={
                    "session_id": plan.session_id + "+" + self.tag,
                })

        reg = HookRegistry()
        reg.register(TaggingHook("A"))
        reg.register(TaggingHook("B"))
        reg.register(TaggingHook("C"))
        out = reg.fire_on_plan_built(_make_plan())
        assert out.session_id == "sess-test+A+B+C"

    def test_register_is_idempotent_on_identity(self):
        from core.duplication_hooks import DefaultHooks, HookRegistry
        reg = HookRegistry()
        h = DefaultHooks()
        reg.register(h); reg.register(h); reg.register(h)
        assert len(reg.hooks) == 1

    def test_clear_wipes_registry(self):
        from core.duplication_hooks import DefaultHooks, HookRegistry
        reg = HookRegistry()
        reg.register(DefaultHooks())
        reg.clear()
        assert reg.hooks == []

    def test_hook_can_short_circuit_by_raising(self):
        from core.duplication_hooks import DefaultHooks, HookRegistry

        class BoomHook(DefaultHooks):
            def on_pre_duplicate(self, dup):
                raise RuntimeError("nope")

        reg = HookRegistry()
        reg.register(BoomHook())
        with pytest.raises(RuntimeError, match="nope"):
            reg.fire_on_pre_duplicate(_make_dup())

    def test_default_registry_has_default_hooks_registered(self):
        """The module-level DEFAULT_REGISTRY is preconfigured so
        pipeline code doesn't need to touch it for Phase B."""
        from core.duplication_hooks import DEFAULT_REGISTRY, DefaultHooks
        assert any(isinstance(h, DefaultHooks)
                   for h in DEFAULT_REGISTRY.hooks)
