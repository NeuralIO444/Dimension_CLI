# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_phase_3_dispatch.py
Slot 7.5 Phase 3 Stage D — dispatch routing + byte-identity gates.

Stage D is a structural refactor. It must produce byte-identical
output to pre-Stage-D `main` across all four aspect strategy
buckets. This file proves that invariant by loading the baseline
fixtures captured from main and asserting deep equality on every
field of the conformed manifest.

Baseline fixtures live under
``python/tests/fixtures/phase_3_stage_d_baselines/`` and were
committed in the same PR (commit 1) so the byte-identity reference
is stable across history.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.aspect_strategy import AspectStrategy  # noqa: E402
from core.scale_engine import ScaleEngine        # noqa: E402
from models.scrape_manifest import ScrapeManifest  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────


_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_SOURCE_MANIFEST = _FIXTURES / "bug_l" / "87n_source_manifest.json"
_BASELINES = _FIXTURES / "phase_3_stage_d_baselines"


@pytest.fixture(scope="module")
def source_manifest() -> ScrapeManifest:
    """Real 87N HD source manifest used by Bug L tests + Stage D
    baselines."""
    with open(_SOURCE_MANIFEST) as f:
        raw = json.load(f)
    return ScrapeManifest.model_validate(raw)


def _load_baseline(name: str) -> dict:
    """Load a pre-Stage-D baseline conformed manifest from fixture."""
    with open(_BASELINES / f"{name}.json") as f:
        return json.load(f)


def _conform(manifest: ScrapeManifest, tw: int, th: int) -> dict:
    """Run ScaleEngine.conform() at the given target. Mode=Fit and
    bleed=5% are the defaults the baselines were captured with."""
    return ScaleEngine(manifest, tw, th, "Fit", 0.05).conform()


_ADDITIVE_TF_FIELDS_SINCE_STAGE_D = ("skip_inject",)
"""Schema fields that landed AFTER the Stage D baselines were captured
and that are strictly additive (defaults preserve pre-existing
behavior). The byte-identity helper strips these before comparing so
the assertion still proves "Stage D's narrow path is unchanged" —
which is what the baselines were captured to verify. Each entry here
is justified by a Phase 3 stage:

  - skip_inject — Stage A (Slot 7.5 Phase 3). Default False on every
    conformed_transforms; only set True by `_apply_preserve_rule_set`.
"""

_ADDITIVE_LAYER_FIELDS_SINCE_SCHEMA_5_1 = (
    "containing_comp_id",
    "containing_comp_uid",
    "wrapper_layer_uid",
    "nesting_depth",
    # Stage B item 2 — same additive-evolution contract as the
    # Stage A breadcrumb fields above. Default None on every layer.
    "layer_references",
    "label",
    "match_name",
    # Slot 15.6 (#6) — surveyor-recorded "would-be" profile tag when
    # a manual tag shadows a profile rule with a different tag. None
    # on layers with no conflict and on every layer before the
    # surveyor runs, so the Stage D byte-identity baselines (which
    # didn't carry this key) need it stripped to stay aligned.
    "profile_suggested_tag",
    # Track B / B1 (2026-08-26) — human-readable explanation for the
    # profile_suggested_tag conflict, consumed by the CEP conflict
    # popover. Same additive-evolution contract as profile_suggested_tag
    # above: None until the surveyor runs, absent from Stage D baselines.
    "profile_conflict_reason",
    # Smart auto-classification (2026-06-26) — new optional fields.
    "source_coverage",
    "source_item",
    # PR #121 follow-up (2026-06-30) — group-aware gravity diagnostic.
    # Default None; populated whenever a layer goes through the
    # group-aware gravity branch. Diagnostic only, doesn't affect math.
    "gravity_group_size",
    # Stage 2 Item 1/5 (2026-06-30) — cross-tag pairing fields. Default
    # None on every layer; this scoped run only computes/persists these
    # via core/pairing.py + core/pair_decisions.py, never inside
    # ScaleEngine.conform() itself, so they're always None in conform
    # output today — gravity wiring (Item 3) is explicitly deferred.
    "paired_with",
    "pair_confidence",
    "pair_source",
    "pair_candidates",
    # Stage 2 follow-up to gravity_group_size — same additive-evolution
    # contract. Default None; only populated once Item 3 (deferred) wires
    # pairs into the gravity pre-pass.
    "pair_group_size",
    # PR #285 (2026-08-29) — optional typographic metadata for text layers
    "typographic_info",
    # PR 4 (2026-08-30) — layer archetype and artwork bounds
    "archetype",
    "artwork_bounds",
)
"""Top-level LayerModel fields added in schema 5.1 (Slot 12.5 Stage A).
Default None on every layer; populated by Stage B's recursive scrape.
Strictly additive — defaults preserve pre-existing Stage D conform
output, but their presence on serialized layers diverges from the
Stage D baselines, which were captured pre-Slot-12.5. Stripped here
to keep the byte-identity assertion's intent ("Stage D's math is
unchanged") aligned with the additive-schema-evolution contract."""

_ADDITIVE_FLAGS_FIELDS_SINCE_SCHEMA_5_1 = (
    "continuously_rasterize",
)
"""LayerFlags fields added in schema 5.1 (Slot 12.5 Stage A). Default
False; same additive-schema-evolution contract as the top-level fields."""


def _strip_additive_fields(d: dict) -> dict:
    """Return a deep-copy of the conformed manifest with the additive
    Phase-3 fields removed from every layer's conformed_transforms, the
    additive schema-5.1 fields removed from every layer dict, AND the
    additive schema-5.1 flags fields removed from every layer.flags.
    Pure transform — does not mutate input."""
    out = json.loads(json.dumps(d))  # deep copy via JSON
    out.pop("gpu_16k_limit_exceeded", None)
    if isinstance(out.get("warnings"), dict):
        out["warnings"].pop("gpu_16k_limit", None)
    for layer in out.get("layers", []):
        tf = layer.get("conformed_transforms")
        if isinstance(tf, dict):
            for key in _ADDITIVE_TF_FIELDS_SINCE_STAGE_D:
                tf.pop(key, None)
        for key in _ADDITIVE_LAYER_FIELDS_SINCE_SCHEMA_5_1:
            layer.pop(key, None)
        flags = layer.get("flags")
        if isinstance(flags, dict):
            for key in _ADDITIVE_FLAGS_FIELDS_SINCE_SCHEMA_5_1:
                flags.pop(key, None)
    return out


def _assert_byte_identical(result: dict, baseline: dict, label: str) -> None:
    """Deep equality via canonical JSON serialization, modulo the
    additive Phase-3 fields listed in `_ADDITIVE_TF_FIELDS_SINCE_STAGE_D`.

    The Stage D baselines were captured before Stage A's `skip_inject`
    field existed; stripping it preserves the test's intent ("Stage D's
    narrow-path math is unchanged") while accommodating additive schema
    evolution. The dict comparison `result == baseline` would also work
    on the stripped dicts, but JSON serialization gives a stable
    diagnostic when fields drift."""
    result_json = json.dumps(_strip_additive_fields(result), sort_keys=True)
    baseline_json = json.dumps(_strip_additive_fields(baseline), sort_keys=True)
    assert result_json == baseline_json, (
        f"{label}: ScaleEngine.conform() output diverged from "
        f"pre-Stage-D baseline. Stage D must be a no-op refactor."
    )


# ── 1. Narrow — the only "real" dispatch in Stage D ─────────────────


def test_narrow_dispatch_routes_to_narrow_rule_set(source_manifest):
    """HD → TikTok 1080×1920 classifies as narrow and routes through
    `_apply_narrow_rule_set`. This is the canonical dispatch case in
    Stage D."""
    result = _conform(source_manifest, 1080, 1920)
    assert result["aspect_strategy"] == AspectStrategy.NARROW.value
    _assert_byte_identical(
        result, _load_baseline("narrow_tiktok"), "narrow_tiktok"
    )


# ── 2-4. Stage D fallbacks ──────────────────────────────────────────
#
# Stage C will replace the widen branch with `_apply_widen_rule_set`,
# Stage B will replace the equal_different_resolution branch with
# `_apply_equal_different_resolution_rule_set`, and Stage A will
# replace the preserve branch with `_apply_preserve_rule_set`. Until
# those land, widen / equal_different_resolution / preserve all fall
# through to `_apply_narrow_rule_set` so behavior is unchanged across
# the catalog.


def test_widen_dispatch_falls_through_to_narrow_in_stage_d(source_manifest):
    """HD → DCP 4K Container 4096×2160 classifies as widen but
    falls through to the narrow rule set in Stage D. Stage C will
    replace this fallback; until then, widen + narrow produce
    identical output."""
    result = _conform(source_manifest, 4096, 2160)
    assert result["aspect_strategy"] == AspectStrategy.WIDEN.value
    _assert_byte_identical(
        result, _load_baseline("widen_dcp_4k_container"),
        "widen_dcp_4k_container",
    )


# Note: the Stage-D-era "equal_different_resolution falls through to
# narrow" assertion was retired when Stage B landed (Slot 7.5 Phase 3
# Stage B). Equal_different_resolution now routes to
# `_apply_equal_different_resolution_rule_set` — pure uniform scale,
# no bleed, no K/S split. See `test_phase_3_equal_different_resolution.py`
# for the active coverage.


# Note: the Stage-D-era "preserve falls through to narrow" assertion
# was retired when Stage A landed (Slot 7.5 Phase 3 Stage A). Preserve
# now routes to `_apply_preserve_rule_set` and produces zero-write
# output with `skip_inject=True` on every layer. See
# `test_phase_3_preserve.py` for the active preserve coverage.


# ── 5. Smoke: the extracted method exists and is callable ───────────


def test_extracted_method_signature():
    """Smoke check: `_apply_narrow_rule_set` exists on `ScaleEngine`
    and is callable. Confirms the extraction landed (not just the
    dispatch wrapper). Stages A/B/C will add three sibling methods
    next to this one."""
    assert hasattr(ScaleEngine, "_apply_narrow_rule_set")
    assert callable(getattr(ScaleEngine, "_apply_narrow_rule_set"))
