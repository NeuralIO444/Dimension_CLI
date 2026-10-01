# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_orientation.py

Covers `core/orientation.py` — the HORIZONTAL / SQUARE / VERTICAL bucket
for a single target composition.

The tests that matter here are the boundary ones. The bucket boundaries
are inclusive toward the outer buckets (ar >= 1.2 is HORIZONTAL, ar <= 0.8
is VERTICAL), and Instagram portrait at 1080x1350 lands on exactly 0.8 —
a real shipping preset sitting precisely on a boundary. If that ever
silently flips to SQUARE, every `variant:vertical` directive on an IG
portrait deliverable stops firing. So the boundary is pinned by a test
naming the actual preset, not just an abstract ratio.

Also asserted: the module never disagrees with `aspect_strategy.py`
about anything, because it deliberately answers a different question —
see the class docstring at the bottom.
"""

from __future__ import annotations

import json

import pytest

from config.constants import ORIENTATION_LOWER_AR, ORIENTATION_UPPER_AR
from core.orientation import (
    Orientation,
    classify,
    display_label,
    orientation_bucket,
)


class TestRealPresets:
    """Every dimension pair here is a preset Dimension actually ships."""

    @pytest.mark.parametrize(
        "width,height,expected,preset",
        [
            (1920, 1080, Orientation.HORIZONTAL, "HD 16:9"),
            (3840, 2160, Orientation.HORIZONTAL, "4K UHD 16:9"),
            (4096, 1716, Orientation.HORIZONTAL, "DCP 4K scope 2.39:1"),
            (2048, 858, Orientation.HORIZONTAL, "DCP 2K scope"),
            (1998, 1080, Orientation.HORIZONTAL, "DCP 2K flat 1.85:1"),
            (1080, 1080, Orientation.SQUARE, "Instagram / Facebook square"),
            (1080, 1920, Orientation.VERTICAL, "TikTok / Reels / Shorts 9:16"),
            (1080, 1350, Orientation.VERTICAL, "Instagram portrait 4:5"),
        ],
    )
    def test_shipping_preset_buckets(self, width, height, expected, preset):
        assert orientation_bucket(width, height) is expected, (
            f"{preset} ({width}x{height}) should be {expected.value}"
        )


class TestBoundaries:
    """The boundaries are inclusive toward HORIZONTAL and VERTICAL."""

    def test_upper_boundary_exactly_is_horizontal(self):
        # 1200x1000 == 1.2 exactly
        assert orientation_bucket(1200, 1000) is Orientation.HORIZONTAL

    def test_just_below_upper_boundary_is_square(self):
        assert orientation_bucket(1199, 1000) is Orientation.SQUARE

    def test_lower_boundary_exactly_is_vertical(self):
        # 800x1000 == 0.8 exactly
        assert orientation_bucket(800, 1000) is Orientation.VERTICAL

    def test_just_above_lower_boundary_is_square(self):
        assert orientation_bucket(801, 1000) is Orientation.SQUARE

    def test_instagram_portrait_sits_on_the_lower_boundary(self):
        """1080/1350 is exactly 0.8 — a shipping preset on the knife edge.

        This is the one that would break quietly: if the comparison ever
        loosens to `<`, IG portrait becomes SQUARE and every
        `variant:vertical` directive targeting it stops firing, with no
        error anywhere.
        """
        assert 1080 / 1350 == pytest.approx(ORIENTATION_LOWER_AR)
        assert orientation_bucket(1080, 1350) is Orientation.VERTICAL

    def test_square_is_square(self):
        assert orientation_bucket(1080, 1080) is Orientation.SQUARE

    def test_thresholds_are_ordered(self):
        assert ORIENTATION_LOWER_AR < ORIENTATION_UPPER_AR


class TestExtremes:
    """OOH work reaches ratios that break naive implementations."""

    def test_stadium_ribbon(self):
        assert orientation_bucket(44500, 100) is Orientation.HORIZONTAL

    def test_extreme_vertical(self):
        assert orientation_bucket(100, 44500) is Orientation.VERTICAL

    def test_one_pixel_square(self):
        assert orientation_bucket(1, 1) is Orientation.SQUARE

    def test_16k_header(self):
        assert orientation_bucket(15360, 2160) is Orientation.HORIZONTAL


class TestInvalidDimensions:
    @pytest.mark.parametrize(
        "width,height", [(0, 100), (100, 0), (-1920, 1080), (1920, -1080), (0, 0)]
    )
    def test_non_positive_raises(self, width, height):
        with pytest.raises(ValueError):
            orientation_bucket(width, height)

    def test_error_names_the_dimensions(self):
        with pytest.raises(ValueError, match=r"1920x0"):
            orientation_bucket(1920, 0)


class TestClassifyResult:
    def test_carries_diagnostics(self):
        r = classify(1080, 1920)
        assert r.orientation is Orientation.VERTICAL
        assert r.aspect_ratio == pytest.approx(0.5625)
        assert (r.width, r.height) == (1080, 1920)

    def test_result_is_frozen(self):
        r = classify(1920, 1080)
        with pytest.raises(Exception):
            r.orientation = Orientation.SQUARE  # type: ignore[misc]

    def test_bucket_and_classify_agree(self):
        for w, h in [(1920, 1080), (1080, 1080), (1080, 1920), (2048, 858)]:
            assert orientation_bucket(w, h) is classify(w, h).orientation


class TestSerialization:
    """The `str` mixin is load-bearing: buckets travel in JSON manifests
    and CEP payloads with no converter."""

    def test_serializes_as_plain_string(self):
        assert json.dumps({"o": orientation_bucket(1080, 1920)}) == '{"o": "VERTICAL"}'

    def test_compares_equal_to_its_string(self):
        assert orientation_bucket(1920, 1080) == "HORIZONTAL"

    @pytest.mark.parametrize(
        "orientation,label",
        [
            (Orientation.HORIZONTAL, "HORIZ"),
            (Orientation.SQUARE, "SQ"),
            (Orientation.VERTICAL, "VERT"),
        ],
    )
    def test_display_labels(self, orientation, label):
        assert display_label(orientation) == label

    def test_every_bucket_has_a_label(self):
        for o in Orientation:
            assert display_label(o)


class TestPurity:
    """No I/O, no global state, no dependency on the source comp — the
    module must stay safe to call from a UI hot path and from a report
    generator alike."""

    def test_is_deterministic(self):
        assert [orientation_bucket(1080, 1920) for _ in range(50)] == [
            Orientation.VERTICAL
        ] * 50

    def test_does_not_depend_on_source_comp(self):
        """The whole point of buckets vs aspect_strategy: TikTok is
        VERTICAL regardless of what the master comp was. `aspect_strategy`
        would give a different relationship for each source; the bucket is
        identical."""
        assert orientation_bucket(1080, 1920) is Orientation.VERTICAL
        # Same target, and there is no source parameter to vary — that
        # absence is the contract, so assert the signature holds it.
        import inspect

        params = list(inspect.signature(orientation_bucket).parameters)
        assert params == ["width", "height"], (
            "orientation_bucket must stay source-independent — adding a "
            "source parameter would make it a duplicate of aspect_strategy."
        )
