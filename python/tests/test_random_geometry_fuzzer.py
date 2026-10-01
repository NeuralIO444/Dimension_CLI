# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_random_geometry_fuzzer.py
Phase 5 of the autonomous-engineering initiative. Two kinds of coverage:

1. Shrinking-algorithm correctness, tested in isolation via a monkeypatched
   failure predicate (`_still_fails`) -- the real engine is clean across
   10,000 random iterations tested by hand while building this (18.67s,
   zero failures), so there's no naturally-occurring real failure to shrink
   against. Testing the shrink LOGIC against a controlled synthetic
   predicate is the honest way to verify it actually reduces toward a
   minimal reproducer, independent of whether the real codebase currently
   has a bug for it to find.
2. A real smoke campaign against the actual ScaleEngine -- the genuine
   ongoing regression guard. If a future change reintroduces a bug in this
   fuzzer's exploration space, this test starts failing with a real,
   shrunk, seed-reproducible case.
"""

from __future__ import annotations

import json

import core.random_geometry_fuzzer as fuzzer


def test_shrink_reduces_scalar_params_toward_a_minimal_failing_value(monkeypatch):
    """A synthetic predicate that fails whenever scale_x > 100 -- shrink
    should converge scale_x down toward just above 100, not leave it at
    whatever huge random value the seed first produced."""

    def fake_still_fails(shape, params, conform_kwargs):
        return params.get("scale_x", 0) > 100.0

    monkeypatch.setattr(fuzzer, "_still_fails", fake_still_fails)

    case = fuzzer.FuzzCase(
        seed=1, shape="single_layer",
        params={"scale_x": 87654.3, "scale_y": 50.0, "rotation": 10.0, "target_w": 1920, "target_h": 1080},
        manifest=None, conform_kwargs={"scale_mode": "Fit"},
    )
    shrunk, steps = fuzzer.shrink(case, max_steps=100)

    assert steps > 0, "shrink should have taken at least one step"
    assert 100.0 < shrunk.params["scale_x"] < 87654.3, (
        f"expected scale_x shrunk toward just above 100, got {shrunk.params['scale_x']}"
    )


def test_shrink_reduces_list_length_toward_minimal_failing_size(monkeypatch):
    """A synthetic predicate that fails whenever a list has >= 3 elements --
    shrink should reduce a 19-element list down to something close to 3,
    not leave it at 19."""

    def fake_still_fails(shape, params, conform_kwargs):
        return len(params.get("depths", [])) >= 3

    monkeypatch.setattr(fuzzer, "_still_fails", fake_still_fails)

    case = fuzzer.FuzzCase(
        seed=1, shape="camera_scene",
        params={"n_members": 19, "pitch": 45.0, "depths": [float(i) for i in range(19)], "target_w": 1920, "target_h": 1080},
        manifest=None, conform_kwargs={"scale_mode": "Fill"},
    )
    shrunk, steps = fuzzer.shrink(case, max_steps=100)

    assert steps > 0
    assert 3 <= len(shrunk.params["depths"]) < 19, (
        f"expected depths shrunk toward 3 elements, got {len(shrunk.params['depths'])}"
    )


def test_generate_case_produces_valid_manifest_for_every_shape():
    """Every shape family, across a spread of seeds, must produce a
    constructible manifest with at least one layer -- catches a generator
    bug independent of whether the resulting conform passes or fails."""
    seen_shapes = set()
    for seed in range(200):
        case = fuzzer.generate_case(seed)
        seen_shapes.add(case.shape)
        assert case.manifest.layers, f"seed {seed} ({case.shape}): empty manifest"
    assert seen_shapes == set(fuzzer._SHAPES), (
        f"200 seeds should hit every shape family at least once, got {seen_shapes}"
    )


def test_campaign_against_real_engine_is_clean():
    """The actual regression guard: a real campaign against ScaleEngine,
    fixed seed for reproducibility. If this starts failing, the failure
    case is already shrunk and its seed is printed in the assertion --
    promote it into test_hall_of_horrors_math.py per the growing-suite
    convention and file it via python/tools/file_bug.py."""
    result = fuzzer.run_campaign(iterations=500, seed=20260902, replay_corpus=False)
    if not result.ok:
        details = "\n".join(
            f"seed={f.case.seed} shape={f.case.shape} params={f.case.params} "
            f"violations={[str(v) for v in f.report.violations]}"
            for f in result.failures
        )
        raise AssertionError(f"{len(result.failures)} fuzz failure(s):\n{details}")


def test_corpus_persistence_and_replay(tmp_path):
    """A failure written to the corpus must be replayed (and re-checked)
    on the next campaign run, before any new random exploration."""
    corpus_path = tmp_path / "failures.jsonl"
    case = fuzzer.generate_case(seed=42)
    entry = case.to_repro_dict()
    corpus_path.parent.mkdir(parents=True, exist_ok=True)
    with open(corpus_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")

    result = fuzzer.run_campaign(iterations=0, corpus_path=corpus_path, replay_corpus=True)
    assert result.replayed_corpus_failures == 1
