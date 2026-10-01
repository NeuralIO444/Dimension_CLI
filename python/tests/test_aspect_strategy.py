# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_aspect_strategy.py
Slot 7.5 Phase 2 — Coverage for the pure aspect-strategy classifier.

Covers:
  • Trivial happy-path cases (preserve / equal_different_resolution /
    widen / narrow).
  • Tolerance-boundary cases that pin EPS=0.05 to the FB/IG landscape
    cluster (≈7% from 16:9 must classify as widen, not preserve).
  • Full 191-target catalog sweep against HD 16:9 source — asserts no
    exceptions, every result is a valid AspectStrategy value, and the
    bucket distribution matches the locked Phase 2 expectation.
  • Error cases (non-positive dimensions raise ValueError).
  • Diagnostic field correctness (signed ar_delta_pct).
"""

from __future__ import annotations

import os
import sys
from collections import Counter

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.aspect_strategy import (  # noqa: E402
    EPS,
    AspectStrategy,
    ClassificationResult,
    classify,
)
from data.target_catalog import BUILTIN_TARGETS  # noqa: E402


# ── 1. Trivial cases ─────────────────────────────────────────────────


def test_hd_to_hd_is_preserve():
    """Identical dimensions → preserve."""
    r = classify(1920, 1080, 1920, 1080)
    assert r.strategy is AspectStrategy.PRESERVE
    assert r.ar_delta_pct == pytest.approx(0.0, abs=1e-9)


def test_hd_to_4k_uhd_is_equal_different_resolution():
    """Same 16:9 aspect, different pixels → equal_different_resolution."""
    r = classify(1920, 1080, 3840, 2160)
    assert r.strategy is AspectStrategy.EQUAL_DIFFERENT_RESOLUTION
    assert r.ar_delta_pct == pytest.approx(0.0, abs=1e-9)


def test_hd_to_tiktok_is_narrow():
    """16:9 → 9:16 → narrow."""
    r = classify(1920, 1080, 1080, 1920)
    assert r.strategy is AspectStrategy.NARROW
    assert r.ar_delta_pct < 0


def test_hd_to_dcp_4k_scope_is_widen():
    """16:9 → 2.39:1 cinema scope → widen."""
    r = classify(1920, 1080, 4096, 1716)
    assert r.strategy is AspectStrategy.WIDEN
    assert r.ar_delta_pct > 0


# ── 2. Tolerance boundary cases ──────────────────────────────────────


@pytest.mark.parametrize(
    "src_w, src_h, tgt_w, tgt_h, expected, note",
    [
        # 6.9% wider (1.901 AR) — above 5% EPS → widen
        (1920, 1080, 1920, 1010, AspectStrategy.WIDEN, "~6.9% wider"),
        # 4.8% wider (1.864 AR) — under 5% EPS → equal_different_resolution
        (1920, 1080, 1920, 1030, AspectStrategy.EQUAL_DIFFERENT_RESOLUTION,
         "~4.8% wider, sub-EPS"),
        # FB/IG landscape cluster: 7.1% wider → widen
        (1920, 1080, 1200, 630, AspectStrategy.WIDEN, "FB landscape 1200x630"),
        # IG landscape: 7.3% wider → widen
        (1920, 1080, 1080, 566, AspectStrategy.WIDEN, "IG landscape 1080x566"),
    ],
)
def test_tolerance_boundaries(src_w, src_h, tgt_w, tgt_h, expected, note):
    r = classify(src_w, src_h, tgt_w, tgt_h)
    assert r.strategy is expected, (
        f"{note}: expected {expected.value}, got {r.strategy.value} "
        f"(delta={r.ar_delta_pct*100:.2f}%)"
    )


def test_fb_ig_cluster_classifies_as_widen():
    """All four FB/IG ~1.91 landscape targets must classify as widen,
    not preserve, with current EPS=0.05. This pins the locked
    tolerance — if EPS is loosened past ~7%, this cluster slides into
    equal_different_resolution and the test fails on purpose."""
    fb_ig_targets = [
        ("facebook_landscape", 1200, 630),
        ("facebook_event",     1920, 1005),
        ("facebook_group",     1640, 856),
        ("instagram_landscape", 1080, 566),
        ("x_twitter_link",     1200, 628),
    ]
    for name, w, h in fb_ig_targets:
        r = classify(1920, 1080, w, h)
        assert r.strategy is AspectStrategy.WIDEN, (
            f"{name} ({w}x{h}) should classify as widen "
            f"(delta={r.ar_delta_pct*100:.2f}%, EPS={EPS*100:.0f}%)"
        )


# ── 3. Full catalog sweep ────────────────────────────────────────────


# Locked expected distribution for the built-in catalog
# against HD 1920x1080 source. If a future PR adds or removes targets,
# this assertion fails — bump expectations alongside the catalog change
# in the same PR.
EXPECTED_CATALOG_DISTRIBUTION = {
    AspectStrategy.PRESERVE: 4,                       # HD→HD exact matches
    AspectStrategy.EQUAL_DIFFERENT_RESOLUTION: 30,    # 16:9 ladder + sub-EPS variants
    AspectStrategy.WIDEN: 138,                        # cinema scope, FB/IG landscape, banners, ribbons
    AspectStrategy.NARROW: 93,                        # portrait, square, 4:3, 5:4, vertical towers
}
EXPECTED_CATALOG_TOTAL = sum(EXPECTED_CATALOG_DISTRIBUTION.values())


def test_catalog_total_matches_expected():
    """If this fails, the catalog grew/shrank — update both
    BUILTIN_TARGETS count and EXPECTED_CATALOG_DISTRIBUTION
    together."""
    assert len(BUILTIN_TARGETS) == EXPECTED_CATALOG_TOTAL


def test_catalog_sweep_classifies_all_targets_without_error():
    """Every built-in target classifies successfully against HD 16:9."""
    for t in BUILTIN_TARGETS:
        r = classify(1920, 1080, t.width, t.height)
        assert isinstance(r, ClassificationResult)
        assert r.strategy in AspectStrategy


def test_catalog_sweep_distribution_matches_expected():
    """Distribution across the four buckets is stable.

    If a future PR shifts a target across the bucket boundary
    (e.g. by changing EPS or by editing target dims), this test
    fails with a diagnostic printout so the expected counts can be
    updated deliberately."""
    counts: Counter = Counter()
    for t in BUILTIN_TARGETS:
        r = classify(1920, 1080, t.width, t.height)
        counts[r.strategy] += 1

    summary = " | ".join(
        f"{k.value}={counts.get(k, 0)}"
        for k in (
            AspectStrategy.PRESERVE,
            AspectStrategy.EQUAL_DIFFERENT_RESOLUTION,
            AspectStrategy.WIDEN,
            AspectStrategy.NARROW,
        )
    )
    print(f"\nCatalog distribution (src=HD 1920x1080): {summary}")

    for strategy, expected in EXPECTED_CATALOG_DISTRIBUTION.items():
        assert counts.get(strategy, 0) == expected, (
            f"{strategy.value}: expected {expected}, got "
            f"{counts.get(strategy, 0)}. Full distribution: {summary}"
        )


# ── 4. Error cases ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "src_w, src_h, tgt_w, tgt_h, axis",
    [
        (0,    1080, 1920, 1080, "src_w=0"),
        (-1,   1080, 1920, 1080, "src_w<0"),
        (1920, 0,    1920, 1080, "src_h=0"),
        (1920, -10,  1920, 1080, "src_h<0"),
        (1920, 1080, 0,    1080, "tgt_w=0"),
        (1920, 1080, -1,   1080, "tgt_w<0"),
        (1920, 1080, 1920, 0,    "tgt_h=0"),
        (1920, 1080, 1920, -5,   "tgt_h<0"),
    ],
)
def test_non_positive_dimensions_raise(src_w, src_h, tgt_w, tgt_h, axis):
    with pytest.raises(ValueError):
        classify(src_w, src_h, tgt_w, tgt_h)


# ── 5. Diagnostic field correctness ──────────────────────────────────


def test_ar_delta_pct_positive_when_target_wider():
    r = classify(1920, 1080, 4096, 1716)  # 2.39:1
    assert r.ar_delta_pct > 0
    # Source AR ~1.778, target AR ~2.387 → delta ~34%.
    assert r.ar_delta_pct == pytest.approx((4096 / 1716 - 1920 / 1080) / (1920 / 1080))


def test_ar_delta_pct_negative_when_target_narrower():
    r = classify(1920, 1080, 1080, 1920)  # 9:16
    assert r.ar_delta_pct < 0
    # 9:16 vs 16:9 → delta ~ -68%.
    assert r.ar_delta_pct == pytest.approx((1080 / 1920 - 1920 / 1080) / (1920 / 1080))


def test_ar_delta_pct_zero_when_aspect_exact():
    r = classify(1920, 1080, 3840, 2160)
    assert r.ar_delta_pct == pytest.approx(0.0, abs=1e-9)


def test_source_and_target_ar_match_inputs():
    r = classify(1920, 1080, 1080, 1920)
    assert r.source_ar == pytest.approx(1920 / 1080)
    assert r.target_ar == pytest.approx(1080 / 1920)
