# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_orientation_js_mirror.py

`core/orientation.py` and `cep/js/preset_browser.js`'s `orientationBucket`
implement the same classification on two runtimes. Nothing structural
stops them drifting, and the 2026-09-01 audit (finding B7) found exactly
that failure already shipped elsewhere: the natural-keyword comment map
exists as two hand-maintained copies in `surveyor.py` and
`SovCore_Layer.jsx`, with no generator and no test, unlike
`tag_registry.yaml` which has both.

Rather than add a third hand-synced pair, this test parses the two
boundary constants out of the JS source and asserts they equal the Python
ones. Two floats is too small to justify a code generator; it is not too
small to justify a gate.

Drift here is silent and expensive: the buckets are what
`variant:<bucket>` / `nudge:...@<bucket>` directives will resolve
against, so a panel that says a target is SQUARE while the engine treats
it as VERTICAL means the artist's directive does not fire and nothing
reports an error.
"""

from __future__ import annotations

import os
import re

import pytest

from config.constants import ORIENTATION_LOWER_AR, ORIENTATION_UPPER_AR
from core.orientation import Orientation, orientation_bucket

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_JS_PATH = os.path.join(_REPO_ROOT, "cep", "js", "preset_browser.js")


def _js_source() -> str:
    with open(_JS_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _js_const(name: str) -> float:
    """Pull `var NAME = <number>;` out of the JS source."""
    src = _js_source()
    m = re.search(rf"var\s+{re.escape(name)}\s*=\s*([0-9]*\.?[0-9]+)\s*;", src)
    assert m, (
        f"{name} not found in cep/js/preset_browser.js. If the constant was "
        "renamed or moved, update this test — do not delete it; it is the "
        "only thing keeping the JS and Python buckets in agreement."
    )
    return float(m.group(1))


class TestBoundaryConstantParity:
    def test_upper_matches(self):
        assert _js_const("ORIENTATION_UPPER_AR") == ORIENTATION_UPPER_AR

    def test_lower_matches(self):
        assert _js_const("ORIENTATION_LOWER_AR") == ORIENTATION_LOWER_AR


class TestComparisonDirectionParity:
    """Equal constants with flipped comparisons would still disagree at the
    boundary — where Instagram portrait (exactly 0.8) sits. Assert the JS
    uses the same inclusive-toward-outer form Python does."""

    def test_upper_comparison_is_inclusive(self):
        src = _js_source()
        assert re.search(r"ar\s*>=\s*ORIENTATION_UPPER_AR", src), (
            "JS upper bound must be `ar >= ORIENTATION_UPPER_AR` to match "
            "Python; a strict `>` moves 6:5 from HORIZONTAL to SQUARE."
        )

    def test_lower_comparison_is_inclusive(self):
        src = _js_source()
        assert re.search(r"ar\s*<=\s*ORIENTATION_LOWER_AR", src), (
            "JS lower bound must be `ar <= ORIENTATION_LOWER_AR` to match "
            "Python; a strict `<` moves Instagram portrait (1080x1350, "
            "exactly 0.8) from VERTICAL to SQUARE, silently disabling every "
            "`variant:vertical` directive on that preset."
        )

    def test_js_returns_the_same_bucket_names(self):
        src = _js_source()
        for name in (o.value for o in Orientation):
            assert f"'{name}'" in src, (
                f"JS must return the literal bucket name {name!r} — the "
                "strings travel between panel and engine uncompared."
            )


class TestKnownPresetsAgree:
    """Python side of the parity table. The JS side of the identical table
    lives in cep/tests/orientation.test.js; both must stay in step."""

    @pytest.mark.parametrize(
        "width,height,expected",
        [
            (1920, 1080, "HORIZONTAL"),
            (3840, 2160, "HORIZONTAL"),
            (4096, 1716, "HORIZONTAL"),
            (1080, 1080, "SQUARE"),
            (1200, 1000, "HORIZONTAL"),
            (1199, 1000, "SQUARE"),
            (801, 1000, "SQUARE"),
            (800, 1000, "VERTICAL"),
            (1080, 1350, "VERTICAL"),
            (1080, 1920, "VERTICAL"),
            (44500, 100, "HORIZONTAL"),
        ],
    )
    def test_python_bucket(self, width, height, expected):
        assert orientation_bucket(width, height).value == expected
