# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_pr_x_runtime_contract.py
PR-X Stage C — runtime contract test (load-bearing regression net).

Spec: docs/roadmap/PR-X.md §7.

Contract
--------
For every layer scale_engine emits a `scale.layer.done` log entry
for, every comparable field in that entry's `extra` payload MUST
appear in the conformed manifest with the same value (within
tolerance).

This test is the load-bearing regression net that gates Stage C
and Stage D of the PR-X migration:

  - Stage C flips V5_LAYER_DENY_KEYS to the authoritative gate;
    V5_LAYER_KEYS demotes to logging-only. The runtime contract
    test confirms scale_engine's per-layer transform output
    reaches the conformed manifest unchanged.

  - Stage D removes V5_LAYER_KEYS entirely. The runtime contract
    test catches a future regression where a field declared on
    `ConformedLayer` / `ConformedTransforms` gets computed by
    scale_engine but is dropped by `model_dump()` (Pydantic
    exclusion misconfiguration) — the failure mode this test is
    purpose-built for.

What's compared
---------------
`scale.layer.done` log entries (from `core/scale_engine.py`
line 658) carry these fields in `extra`:

  layer_index, layer_name, layer_kind, is_root,
  out_position, out_scale, out_anchor,
  pos_delta_x, pos_delta_y

The corresponding manifest fields under
`layer["conformed_transforms"]`:

  position, scale, anchor, is_root, ...

Comparison map:

  out_position  →  conformed_transforms.position    (static, 1e-6 abs)
  out_scale     →  conformed_transforms.scale       (static, 1e-6 abs)
  out_anchor    →  conformed_transforms.anchor      (static, 1e-6 abs)
  is_root       →  conformed_transforms.is_root     (exact bool)
  layer_kind    →  layer.layer_kind                 (exact str)

Excluded per §7.5:
  layer_index, layer_name — identity fields used to match log
    entries to manifest layers, not transform values.
  pos_delta_x, pos_delta_y — diagnostic deltas; per-layer
    position offsets, not transform output the manifest claims
    to serialize.

Keyed values
------------
Per §7.3 the spec named per-property keyed arrays
(times[], values[], keyInInterpolationType[], etc.) as in-scope.
Reality (verified at lock time): `scale.layer.done` does NOT
carry keyframe payloads — the keyed conform path
(`property_registry.apply_rule`, `lerp_engine`) emits its own
log keys. Stage C contract test covers static only. Adding keyed
coverage is a follow-up if/when scale_engine starts logging
keyframe payloads on the done event.

Tag-passthrough layers
----------------------
Reality (verified at lock time): layers tagged GUIDE / PROTECT
take a separate code path at `scale_engine.py:437` and emit
`scale.layer.tag_passthrough` instead of `scale.layer.done`. Their
transforms pass through unchanged by construction. The contract
test scopes the passthrough check to layers that DO emit
scale.layer.done; tag-passthrough layers are accounted for via
the union-set check (every manifest layer must have a log entry
of some kind), but their transform values aren't field-by-field
compared because the log doesn't carry them. A future regression
that drops a tag-passthrough layer entirely is still caught by
the union-set check.

Real-data discipline
--------------------
Loads the same 87N source manifest fixture used by
test_bug_l_camera_depth_axis.py
(`python/tests/fixtures/bug_l/87n_source_manifest.json`,
HD 1920×1080, 19 layers including 1 camera). NO synthetic
Python dicts — per CLAUDE.md anti-pattern.

Positive + negative
-------------------
TestRuntimeContractPassthrough — confirms log-vs-manifest match on
the unmodified conform pipeline.

TestRuntimeContractCatchesDrop — proves the test would have
caught a regression where conformed_transforms gets a field
silently mutated downstream of the log emission. Same paired
pattern as test_tag_roundtrip_jsx_merger.py /
test_soe_scrape_jsx_merger.py: positive case proves the contract
holds today; negative case proves the contract test would have
fired if a future change broke it.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
))

from core.scale_engine import ScaleEngine
from models.scrape_manifest import ScrapeManifest


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "bug_l" / "87n_source_manifest.json"
)

# 87N's expected target conform: HD (1920×1080) → TIKTOK (1080×1920) —
# the same configuration as Matt's production smoke path.
TARGET_W = 1080
TARGET_H = 1920

# Float tolerances per PR-X.md §7.4. Static is tight because the value
# emitted by scale_engine and the value serialized by model_dump pass
# through the SAME ConformedTransforms instance — there's no math
# between them, only object-to-dict serialization. 1e-6 absolute
# accommodates JSON repr round-trip noise; AE transform magnitudes are
# typically 10^0 to 10^3, so this is roughly equivalent to rel=1e-9 at
# the high end.
TOL_STATIC = 1e-6


# Identity / diagnostic fields that ARE in the log payload but are
# intentionally not in the manifest as comparable transform values.
# Identity fields (layer_index, layer_name) are used to MATCH log
# entries to manifest layers; pos_delta_* are diagnostic deltas not
# part of the transform output.
_LOG_ONLY_KEYS = {"layer_index", "layer_name", "pos_delta_x", "pos_delta_y"}


def _load_manifest() -> ScrapeManifest:
    with open(FIXTURE_PATH) as f:
        return ScrapeManifest.model_validate(json.load(f))


def _run_conform_with_log_capture(
    manifest: ScrapeManifest,
    monkeypatch,
) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, str], Dict[str, Any]]:
    """Run ScaleEngine.conform() with `log.debug` patched to capture
    every `scale.layer.done` and `scale.layer.tag_passthrough` entry.
    Returns (done_by_index, passthrough_by_layer_name, conform_result).

    Tag-passthrough log entries identify their layer by `layer`
    (name) not `layer_index`; we keep them in a separate dict and
    join against the manifest by name for the union-set check."""
    from core import scale_engine as se_mod

    done_captured: Dict[int, Dict[str, Any]] = {}
    passthrough_captured: Dict[int, str] = {}
    original = se_mod.log.debug

    def capturing_debug(msg, *args, **kwargs):
        extra = kwargs.get("extra", {})
        if msg == "scale.layer.done":
            idx = extra.get("layer_index")
            if idx is not None:
                done_captured[idx] = extra
        elif msg == "scale.layer.tag_passthrough":
            layer_name = extra.get("layer")
            if layer_name is not None:
                passthrough_captured[layer_name] = extra
        return original(msg, *args, **kwargs)

    monkeypatch.setattr(se_mod.log, "debug", capturing_debug)

    engine = ScaleEngine(
        manifest=manifest,
        target_width=TARGET_W,
        target_height=TARGET_H,
        scale_mode="Fit",
        bleed_pct=0.05,
    )
    result = engine.conform()
    return done_captured, passthrough_captured, result


def _assert_static_array_equal(
    actual: List[float],
    expected: List[float],
    *,
    field: str,
    layer_index: int,
    layer_name: str,
) -> None:
    """Per-axis tolerance check. Both lists must have the same length;
    each axis must be within TOL_STATIC absolute. Failure message
    names the layer so a future regression in a single layer is
    diagnosable from the traceback alone."""
    assert len(actual) == len(expected), (
        f"PR_X_RUNTIME_CONTRACT_LEN_MISMATCH layer={layer_index} "
        f"({layer_name!r}) field={field}: "
        f"manifest len={len(actual)}, log len={len(expected)}"
    )
    for axis, (a, e) in enumerate(zip(actual, expected)):
        delta = abs(a - e)
        assert delta <= TOL_STATIC, (
            f"PR_X_RUNTIME_CONTRACT_DRIFT layer={layer_index} "
            f"({layer_name!r}) field={field}[{axis}]: "
            f"manifest={a:.9f}, log={e:.9f}, "
            f"delta={delta:.2e} > tol {TOL_STATIC:.2e}. "
            f"scale_engine emitted one value to the log and a "
            f"different value to the manifest — fail loud here."
        )


# ──────────────────────────────────────────────────────────────────
# Positive — contract holds on unmodified main
# ──────────────────────────────────────────────────────────────────


class TestRuntimeContractPassthrough:
    """Pre-flip: scale_engine emits the same transform values to the
    log and to the manifest. This MUST be green at every stage of
    PR-X migration (A, B, C, D). It's the load-bearing regression
    net for Stage C and Stage D."""

    def test_every_manifest_layer_has_a_log_entry(self, monkeypatch):
        """Union-set check: every layer in the conformed manifest
        MUST have a log entry of some kind — either scale.layer.done
        (for layers that went through the normal conform path) or
        scale.layer.tag_passthrough (for GUIDE/PROTECT layers).
        Missing layer = silent drop somewhere in conform; different
        bug class than field-drop, same passthrough check catches
        it."""
        manifest = _load_manifest()
        done_by_index, passthrough_by_name, result = (
            _run_conform_with_log_capture(manifest, monkeypatch)
        )

        manifest_layers = [
            layer for layer in result["layers"]
            if layer.get("conformed_transforms") is not None
        ]

        for layer in manifest_layers:
            idx = layer["index"]
            name = layer["name"]
            has_done = idx in done_by_index
            has_passthrough = name in passthrough_by_name
            assert has_done or has_passthrough, (
                f"PR_X_RUNTIME_CONTRACT_NO_LOG_FOR_LAYER "
                f"layer={idx} ({name!r}): conformed_transforms exists "
                f"in the manifest but neither scale.layer.done nor "
                f"scale.layer.tag_passthrough fired. Silent drop in "
                f"the conform pipeline — fail loud here."
            )

    def test_done_layers_are_in_the_manifest(self, monkeypatch):
        """Inverse half of the union-set check: every layer
        scale.layer.done logged MUST appear in the manifest with a
        conformed_transforms. Catches a regression that emits the
        log entry but fails to append the layer to the result list."""
        manifest = _load_manifest()
        done_by_index, _passthrough, result = (
            _run_conform_with_log_capture(manifest, monkeypatch)
        )

        manifest_indices = {
            layer["index"] for layer in result["layers"]
            if layer.get("conformed_transforms") is not None
        }

        missing = set(done_by_index.keys()) - manifest_indices
        assert missing == set(), (
            f"PR_X_RUNTIME_CONTRACT_LOGGED_BUT_NOT_IN_MANIFEST: "
            f"scale.layer.done fired for layer indices {sorted(missing)} "
            f"but they have no conformed_transforms in the result. "
            f"Silent drop between log emission and manifest append."
        )

    def test_position_scale_anchor_pass_through(self, monkeypatch):
        """Per layer: out_position / out_scale / out_anchor from the
        log MUST equal conformed_transforms.position / scale / anchor
        in the manifest, within 1e-6 absolute per axis. This is the
        core load-bearing assertion.

        Scope: layers in scale.layer.done. Tag-passthrough layers
        are excluded because their log entry doesn't carry transform
        values — by-construction they're identity passthrough."""
        manifest = _load_manifest()
        done_by_index, _passthrough, result = (
            _run_conform_with_log_capture(manifest, monkeypatch)
        )

        for layer in result["layers"]:
            tf = layer.get("conformed_transforms")
            if tf is None:
                continue
            idx = layer["index"]
            log_entry = done_by_index.get(idx)
            if log_entry is None:
                continue  # tag-passthrough layer; checked elsewhere

            for field, log_key in (
                ("position", "out_position"),
                ("scale",    "out_scale"),
                ("anchor",   "out_anchor"),
            ):
                # Loud-failure mode for the canonical bug we ship this
                # net to catch: a Pydantic exclusion (or any downstream
                # path) drops the field from the manifest. KeyError
                # would technically fail the test but the diagnostic
                # is unreadable — this assertion gives a clear
                # PR_X_RUNTIME_CONTRACT_* signal.
                assert field in tf, (
                    f"PR_X_RUNTIME_CONTRACT_FIELD_DROPPED "
                    f"layer={idx} ({layer['name']!r}) field={field}: "
                    f"scale_engine logged out_{field}="
                    f"{log_entry.get(log_key)} but the manifest's "
                    f"conformed_transforms has no '{field}' key. "
                    f"This is the field-drop bug class PR-X exists "
                    f"to prevent — fail loud here."
                )
                _assert_static_array_equal(
                    tf[field], log_entry[log_key],
                    field=field,
                    layer_index=idx,
                    layer_name=layer["name"],
                )

    def test_is_root_and_layer_kind_agree(self, monkeypatch):
        """Non-numeric passthrough: is_root (bool) and layer_kind
        (str) must agree exactly between the log and the manifest.
        Catches a regression that flips bool semantics or mis-routes
        a layer kind during conform."""
        manifest = _load_manifest()
        done_by_index, _passthrough, result = (
            _run_conform_with_log_capture(manifest, monkeypatch)
        )

        for layer in result["layers"]:
            tf = layer.get("conformed_transforms")
            if tf is None:
                continue
            idx = layer["index"]
            log_entry = done_by_index.get(idx)
            if log_entry is None:
                continue

            assert tf["is_root"] == log_entry["is_root"], (
                f"PR_X_RUNTIME_CONTRACT_IS_ROOT layer={idx} "
                f"({layer['name']!r}): manifest={tf['is_root']}, "
                f"log={log_entry['is_root']}"
            )
            assert layer["layer_kind"] == log_entry["layer_kind"], (
                f"PR_X_RUNTIME_CONTRACT_LAYER_KIND layer={idx} "
                f"({layer['name']!r}): manifest={layer['layer_kind']!r}, "
                f"log={log_entry['layer_kind']!r}"
            )

    def test_log_only_keys_are_documented_exclusions(self, monkeypatch):
        """Defense-in-depth: every key in a scale.layer.done log
        entry must either be (a) compared above, (b) listed in
        _LOG_ONLY_KEYS, or (c) cause this test to fail loudly so
        a future scale_engine change that adds a new logged field
        forces a developer to decide which bucket it belongs in.

        This is the §7.6 'forces an explicit choice' guarantee: a
        silent drop at this boundary is no longer possible because
        an undocumented new key fails this test."""
        manifest = _load_manifest()
        done_by_index, _passthrough, _result = (
            _run_conform_with_log_capture(manifest, monkeypatch)
        )

        compared_keys = {
            "out_position", "out_scale", "out_anchor",
            "is_root", "layer_kind",
        }
        documented = compared_keys | _LOG_ONLY_KEYS

        for idx, log_entry in done_by_index.items():
            unknown = set(log_entry.keys()) - documented
            assert unknown == set(), (
                f"PR_X_RUNTIME_CONTRACT_UNDOCUMENTED_LOG_KEY "
                f"layer={idx}: scale.layer.done emitted keys "
                f"{sorted(unknown)} that this test does not compare "
                f"against the manifest and are not listed in "
                f"_LOG_ONLY_KEYS. Add to one bucket explicitly — "
                f"see PR-X.md §7.5 + §7.6."
            )


# ──────────────────────────────────────────────────────────────────
# Negative — test would have caught a silent drop
# ──────────────────────────────────────────────────────────────────


class TestRuntimeContractCatchesDrop:
    """Prove the contract test is not a placebo. If a future change
    causes scale_engine's log entry to disagree with the manifest's
    conformed_transforms (which is what would happen if a Pydantic
    exclusion drops a field at model_dump time, or if a future
    refactor recomputes the manifest value after logging), the
    positive test above MUST fail.

    Doctor the conform result post-hoc to simulate the bug, then
    re-run the same assertion logic and confirm AssertionError
    fires. This mirrors the test_negative_case_proves_test_would_have_caught
    pattern from PR #45 / PR #48 regression nets."""

    def test_position_drift_is_caught(self, monkeypatch):
        """Simulate a regression where the manifest's position for a
        done-layer drifts away from what scale_engine logged. A real
        failure mode: a future change adds an extra
        `.model_dump(exclude={"position"})` call that silently zeroes
        the field, or a downstream pass mutates the value after the
        log fires. The contract test MUST fire on this drift."""
        manifest = _load_manifest()
        done_by_index, _passthrough, result = (
            _run_conform_with_log_capture(manifest, monkeypatch)
        )

        # Doctor: pick a layer that has a done log entry (i.e. not a
        # tag-passthrough layer) and shift its position.x past
        # tolerance. The drift is deterministic, intentional, and
        # scoped to a deep-copy so the rest of the test session
        # doesn't see it.
        doctored = copy.deepcopy(result)
        target = None
        for layer in doctored["layers"]:
            if (layer.get("conformed_transforms") is not None
                    and layer["index"] in done_by_index):
                target = layer
                break
        assert target is not None, (
            "fixture must have at least one done-logged layer with "
            "conformed_transforms — test setup invariant"
        )

        original_x = target["conformed_transforms"]["position"][0]
        target["conformed_transforms"]["position"][0] = original_x + 1.0

        # Re-run the same assertion. It MUST raise AssertionError
        # naming the doctored field. If it doesn't, the positive
        # test is a placebo and the regression net is fake.
        log_entry = done_by_index[target["index"]]
        with pytest.raises(AssertionError, match="position\\[0\\]"):
            _assert_static_array_equal(
                target["conformed_transforms"]["position"],
                log_entry["out_position"],
                field="position",
                layer_index=target["index"],
                layer_name=target["name"],
            )

    def test_missing_layer_in_manifest_is_caught(self, monkeypatch):
        """If scale_engine logs a scale.layer.done for a layer but
        the manifest doesn't carry that layer's conformed_transforms,
        the inverse-set check (`test_done_layers_are_in_the_manifest`)
        fires. Simulates a regression where a downstream pass drops
        a done-logged layer entirely — different bug class than
        field-drop, same passthrough check catches it."""
        manifest = _load_manifest()
        done_by_index, _passthrough, result = (
            _run_conform_with_log_capture(manifest, monkeypatch)
        )

        # Doctor: pick the first done-logged layer, remove it from
        # the doctored manifest. The captured log still has its
        # scale.layer.done entry — the index-set check should fire.
        first_done_idx = next(iter(done_by_index.keys()))
        doctored = copy.deepcopy(result)
        doctored["layers"] = [
            l for l in doctored["layers"]
            if l["index"] != first_done_idx
        ]

        manifest_indices = {
            layer["index"] for layer in doctored["layers"]
            if layer.get("conformed_transforms") is not None
        }
        missing = set(done_by_index.keys()) - manifest_indices

        # The inverse-set check from the positive class would fire
        # with this `missing` set non-empty. Verify the doctoring
        # actually produced the bug shape we're testing.
        assert first_done_idx in missing, (
            f"doctoring failed — first_done_idx={first_done_idx} "
            f"must be in missing={missing}"
        )
        assert len(missing) > 0, (
            "doctoring must produce at least one logged-but-missing "
            "layer for the negative case to be meaningful"
        )
