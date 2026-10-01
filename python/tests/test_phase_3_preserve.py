# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_phase_3_preserve.py
Slot 7.5 Phase 3 Stage A — preserve rule set (zero-write).

Seven tests covering the locked Stage A contract:

  1. Dispatch routes PRESERVE → _apply_preserve_rule_set (not narrow).
  2. Conformed transforms equal source transforms exactly (Q5 byte-
     identity gate). Real 87N HD fixture, AV + camera covered.
  3. Every conformed_transforms entry has skip_inject=True (Q3 lock).
  4. SOE still runs and produces position adjustments for TT layers
     landing in unsafe zones; non-TT layers stay byte-identical to
     source (Q1 lock — SOE ON for preserve).
  5. Gravity is OFF for preserve — no gravity entries in
     `gravity_applied`; HERO transforms equal source verbatim.
  6. Active studio profile is ignored on preserve (Q4 lock).
  7. Stage A's dispatch change does NOT regress narrow / widen /
     equal_different_resolution outputs vs the Stage D baselines.

The synthetic fixture in test 4 is the only synthetic-data
exception in this file (per the implementation spec). All other
tests use the real 87N source manifest.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import cv2
import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.aspect_strategy import AspectStrategy      # noqa: E402
from core.occlusion_engine import (                  # noqa: E402
    OcclusionEngine,
    OcclusionMask,
)
from core.scale_engine import ScaleEngine            # noqa: E402
from models.scrape_manifest import ScrapeManifest    # noqa: E402
from models.studio_profile import ProfileRule, StudioProfile  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────


_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_SOURCE_MANIFEST = _FIXTURES / "bug_l" / "87n_source_manifest.json"
_BASELINES = _FIXTURES / "phase_3_stage_d_baselines"


@pytest.fixture(scope="module")
def source_manifest() -> ScrapeManifest:
    """Real 87N HD source manifest. 19 layers including a camera."""
    with open(_SOURCE_MANIFEST) as f:
        raw = json.load(f)
    return ScrapeManifest.model_validate(raw)


def _conform(manifest: ScrapeManifest, tw: int, th: int) -> dict:
    """Standard conform invocation (Fit mode, 5% bleed). Bleed is
    irrelevant on preserve (no scale) but the harness passes it
    uniformly across strategies."""
    return ScaleEngine(manifest, tw, th, "Fit", 0.05).conform()


# ── 1. Dispatch routing ──────────────────────────────────────────────


def test_preserve_dispatch_routes_to_preserve_rule_set(source_manifest, monkeypatch):
    """PRESERVE classification routes through `_apply_preserve_rule_set`
    and NOT through `_apply_narrow_rule_set`. Stage A's dispatch
    change replaced the Stage D fallback."""
    called = {"preserve": 0, "narrow": 0}

    orig_preserve = ScaleEngine._apply_preserve_rule_set
    orig_narrow = ScaleEngine._apply_narrow_rule_set

    def spy_preserve(self, **kwargs):
        called["preserve"] += 1
        return orig_preserve(self, **kwargs)

    def spy_narrow(self, **kwargs):
        called["narrow"] += 1
        return orig_narrow(self, **kwargs)

    monkeypatch.setattr(ScaleEngine, "_apply_preserve_rule_set", spy_preserve)
    monkeypatch.setattr(ScaleEngine, "_apply_narrow_rule_set", spy_narrow)

    result = _conform(source_manifest, 1920, 1080)
    assert result["aspect_strategy"] == AspectStrategy.PRESERVE.value
    assert called["preserve"] == 1
    assert called["narrow"] == 0


# ── 2. Byte-identity to source transforms ────────────────────────────


def test_preserve_output_byte_identical_to_source_transforms(source_manifest):
    """Q5 byte-identity gate: every transform field in the conformed
    output equals the source field exactly (no float tolerance).

    Per the locked schema, `skip_inject=True` is the zero-write
    signal; the transform values are still populated so SOE and the
    report generator can read them. This test asserts those values
    are byte-identical to source."""
    result = _conform(source_manifest, 1920, 1080)
    src_by_index = {L.index: L for L in source_manifest.layers}

    for layer_dict in result["layers"]:
        idx = layer_dict["index"]
        src = src_by_index[idx]
        tf = layer_dict["conformed_transforms"]
        kind = (layer_dict.get("layer_kind") or "av").lower()

        # AV-style transforms
        assert tf["position"] == (src.position or [0.0, 0.0, 0.0]), (
            f"layer {idx} '{src.name}' position diverged"
        )
        assert tf["scale"] == (src.scale or [100.0, 100.0, 100.0]), (
            f"layer {idx} '{src.name}' scale diverged"
        )
        assert tf["rotation"] == (src.rotation_z or 0.0), (
            f"layer {idx} '{src.name}' rotation diverged"
        )
        assert tf["anchor"] == (src.anchor or [0.0, 0.0, 0.0]), (
            f"layer {idx} '{src.name}' anchor diverged"
        )

        # Camera intrinsics
        if kind == "camera" and src.camera is not None:
            cam = tf.get("camera") or {}
            assert cam.get("zoom") == src.camera.zoom
            assert cam.get("pointOfInterest") == src.camera.pointOfInterest
            assert cam.get("focusDistance") == src.camera.focusDistance
            assert cam.get("aperture") == src.camera.aperture
            assert cam.get("blurLevel") == src.camera.blurLevel
            assert cam.get("depthOfField") == src.camera.depthOfField

        # Light intrinsics
        if kind == "light" and src.light is not None:
            lt = tf.get("light") or {}
            assert lt.get("lightType") == src.light.lightType
            assert lt.get("intensity") == src.light.intensity
            assert lt.get("color") == src.light.color
            assert lt.get("coneAngle") == src.light.coneAngle
            assert lt.get("falloffDistance") == src.light.falloffDistance
            assert lt.get("radius") == src.light.radius


# ── 3. Zero-write proof (skip_inject=True) ───────────────────────────


def test_preserve_marks_layers_skip_inject_for_babysitter_skip(source_manifest):
    """Per Q3 lock, preserve is zero-write. `skip_inject=True` tells
    Babysitter to early-return on every layer-kind writer,
    suppressing all AE setValue round-trips. JSX integration of this
    flag is verified manually in the AE smoke step (no JSX harness
    in the test suite today; documented as a known coverage gap)."""
    result = _conform(source_manifest, 1920, 1080)
    for layer_dict in result["layers"]:
        tf = layer_dict["conformed_transforms"]
        assert tf["skip_inject"] is True, (
            f"layer {layer_dict.get('index')} "
            f"'{layer_dict.get('name')}' missing skip_inject=True"
        )


# ── 4. SOE still runs ────────────────────────────────────────────────


def _build_minimal_synthetic_manifest() -> ScrapeManifest:
    """Synthetic-fixture exception: a minimal manifest with one AV
    layer tagged TT positioned in the bottom-half CUTOFF zone of the
    `split_mask` shape used by `test_occlusion_engine.py`. Required
    because the real 87N fixture lacks a TT layer in an SOE-
    triggering position, and the SOE smoke needs both a TT layer and
    a corresponding mask. All other tests in this file use real 87N
    data."""
    return ScrapeManifest.model_validate({
        "status": "OK",
        "project_info": {"name": "synth_soe_smoke", "width": 1920, "height": 1080, "fps": 30.0},
        "layers": [
            {
                "index": 1, "name": "TT_layer",
                "parent_index": -1, "layer_kind": "av",
                "position": [960.0, 900.0, 0.0],   # bottom-half (CUTOFF zone)
                "scale":    [100.0, 100.0, 100.0],
                "rotation_z": 0.0,
                "anchor":   [0.0, 0.0, 0.0],
                "source_rect": [-50, -10, 100, 20],
                "world_bounds": {"l": 910.0, "t": 890.0, "r": 1010.0, "b": 910.0},
                "hero_time": 0.0,
                "is_keyed": {"position": False, "scale": False, "rotation": False},
                "content_tag": "TT",
                "content_tag_source": "manual_picker",
            },
            {
                "index": 2, "name": "HERO_layer",
                "parent_index": -1, "layer_kind": "av",
                "position": [960.0, 200.0, 0.0],   # top-half (GO zone) — should stay put
                "scale":    [100.0, 100.0, 100.0],
                "rotation_z": 0.0,
                "anchor":   [0.0, 0.0, 0.0],
                "content_tag": "HERO",
                "content_tag_source": "manual_picker",
            },
        ],
    })


def test_preserve_soe_still_runs(tmp_path):
    """Q1 lock — SOE stays ON for preserve. After the zero-write
    pass, SOE consumes the conformed dict and produces position
    adjustments for TT layers in unsafe zones. Non-TT layers stay
    byte-identical to source."""
    manifest = _build_minimal_synthetic_manifest()

    # Split mask: top half GO, bottom half CUTOFF (matches the
    # occlusion_engine test convention).
    h, w = 1080, 1920
    img = np.zeros((h, w), dtype=np.uint8)
    img[: h // 2, :] = 255            # GO
    mask_path = tmp_path / "split.png"
    cv2.imwrite(str(mask_path), img)
    mask = OcclusionMask(mask_path, w, h)

    # Preserve conform first (zero-write, skip_inject=True everywhere).
    result = ScaleEngine(manifest, 1920, 1080, "Fit", 0.05).conform()
    assert result["aspect_strategy"] == AspectStrategy.PRESERVE.value

    # SOE consumes the conformed dict.
    engine = OcclusionEngine(mask, "split_test")
    corrected, corrections = engine.run(result)

    # SOE produced at least one correction on the TT layer.
    tt_corrections = [c for c in corrections if c.content_tag == "TT"]
    assert tt_corrections, (
        "SOE did not produce a TT correction — preserve broke the "
        "SOE pipeline for translatable layers"
    )

    # HERO (untranslatable) stays byte-identical to source.
    hero_dict = next(L for L in corrected["layers"] if L["name"] == "HERO_layer")
    src_hero_pos = [960.0, 200.0, 0.0]
    assert hero_dict["conformed_transforms"]["position"] == src_hero_pos
    assert hero_dict["conformed_transforms"]["skip_inject"] is True


# ── 5. Gravity is OFF for preserve ───────────────────────────────────


def test_preserve_gravity_is_off(source_manifest):
    """Per Section 3 Stage A, preserve runs no gravity. The engine's
    `gravity_applied` accumulator must be empty after a preserve
    conform, and HERO transforms must equal source (not gravity-
    snapped)."""
    engine = ScaleEngine(source_manifest, 1920, 1080, "Fit", 0.05)
    result = engine.conform()
    assert result["aspect_strategy"] == AspectStrategy.PRESERVE.value

    # No gravity-rule entries collected on the engine.
    assert engine.gravity_applied == [], (
        f"preserve fired gravity rules unexpectedly: {engine.gravity_applied}"
    )

    # HERO layers (if present) transform unchanged.
    src_by_index = {L.index: L for L in source_manifest.layers}
    for layer_dict in result["layers"]:
        src = src_by_index[layer_dict["index"]]
        if (src.content_tag or "").upper() in ("HERO", "KEYART"):
            tf = layer_dict["conformed_transforms"]
            assert tf["position"] == (src.position or [0.0, 0.0, 0.0]), (
                f"HERO/KEYART layer '{src.name}' was repositioned"
            )


# ── 6. Studio profile ignored ────────────────────────────────────────


def test_preserve_ignores_active_studio_profile(source_manifest):
    """Per Q4 lock, studio profiles gate on narrow only. Preserve
    applies the rule set verbatim regardless of active profile —
    a profile defining a custom HERO gravity rule does not move
    any HERO on a preserve conform."""
    # Build a profile with a HERO-style rule that, if applied, would
    # snap the layer to the top of the safe area.
    profile = StudioProfile(
        id="test_preserve_profile",
        display_name="Preserve Test Profile",
        prefixes=[
            ProfileRule(match="HERO_", tag="HERO", gravity="top", scale=1.5),
        ],
        type_overrides={"HERO": 2.0},
    )

    engine = ScaleEngine(source_manifest, 1920, 1080, "Fit", 0.05, profile=profile)
    result = engine.conform()
    assert result["aspect_strategy"] == AspectStrategy.PRESERVE.value

    # No gravity entries — profile did not fire.
    assert engine.gravity_applied == [], (
        "profile gravity fired on preserve (should be inert per Q4)"
    )

    # Any layer name starting with HERO_ has source-identical position.
    src_by_index = {L.index: L for L in source_manifest.layers}
    for layer_dict in result["layers"]:
        src = src_by_index[layer_dict["index"]]
        if src.name.startswith("HERO_"):
            tf = layer_dict["conformed_transforms"]
            assert tf["position"] == (src.position or [0.0, 0.0, 0.0])


# ── 7. Cross-bucket no-regression ────────────────────────────────────


_ADDITIVE_TF_FIELDS_SINCE_STAGE_D = ("skip_inject",)

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
    # Slot 15.6 (#6) — surveyor-recorded conflict signal.
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

_ADDITIVE_FLAGS_FIELDS_SINCE_SCHEMA_5_1 = (
    "continuously_rasterize",
)


def _strip_additive(d: dict) -> dict:
    """Strip Phase-3 additive `skip_inject` field AND the Slot 12.5
    schema-5.1 additive LayerModel + LayerFlags fields for fair
    comparison against pre-Stage-D baselines (same helper logic as
    test_phase_3_dispatch.py — duplicated here to keep this file
    self-contained)."""
    out = json.loads(json.dumps(d))
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


@pytest.mark.parametrize(
    "name, tw, th, expected_strategy",
    [
        ("narrow_tiktok",            1080, 1920, AspectStrategy.NARROW),
        ("widen_dcp_4k_container",   4096, 2160, AspectStrategy.WIDEN),
        # equal_different_resolution case retired when Stage B landed —
        # the bucket now routes to `_apply_equal_different_resolution_rule_set`
        # and the Stage D baseline is no longer the right reference.
        # Equivalent no-leak coverage lives in
        # `test_phase_3_equal_different_resolution.py::test_equal_different_resolution_does_not_affect_other_buckets`.
    ],
)
def test_preserve_does_not_affect_other_dispatch(
    source_manifest, name, tw, th, expected_strategy
):
    """Stage A's dispatch change must not leak into narrow or widen.
    Both still route through `_apply_narrow_rule_set` post-Stage-B
    (narrow direct; widen as the remaining Stage C fallback) and
    produce byte-identical output to the Stage D baselines."""
    result = _conform(source_manifest, tw, th)
    assert result["aspect_strategy"] == expected_strategy.value

    with open(_BASELINES / f"{name}.json") as f:
        baseline = json.load(f)

    result_json = json.dumps(_strip_additive(result), sort_keys=True)
    baseline_json = json.dumps(_strip_additive(baseline), sort_keys=True)
    assert result_json == baseline_json, (
        f"{name}: Stage A leaked into {expected_strategy.value} dispatch"
    )
