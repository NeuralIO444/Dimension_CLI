from types import SimpleNamespace

import numpy as np

from logic.safe_zone_insets import (
    load_inset_catalog,
    reset_inset_catalog_cache,
    resolve_inset_mask,
    spec_for_target,
    synthesize_inset_mask,
)


def setup_function(_fn=None):
    reset_inset_catalog_cache()


def test_catalog_has_tiktok_ui_chrome():
    cat = load_inset_catalog()
    assert cat["presets"]["tiktok"]["ui_chrome"]["bottom"] == 21.9


def test_synthesize_percent_edges():
    mask = synthesize_inset_mask(100, 200, {"top": 10, "bottom": 20, "left": 5, "right": 15})
    assert mask.shape == (200, 100)
    assert mask.dtype == np.uint8
    assert mask[0, 50] == 0
    assert mask[19, 50] == 0
    assert mask[20, 50] == 255
    assert mask[199, 50] == 0  # bottom 20%
    assert mask[100, 0] == 0
    assert mask[100, 50] == 255


def test_resolve_uses_subcategory():
    t = SimpleNamespace(
        id="builtin:tiktok_video",
        subcategory="tiktok",
        width=1080,
        height=1920,
        metadata={},
    )
    mask = resolve_inset_mask(t)
    assert mask is not None
    assert mask.shape == (1920, 1080)
    # top 13% of 1920 = 249.6 → 250
    assert mask[0, 540] == 0
    assert mask[260, 540] == 255


def test_metadata_override_wins():
    t = SimpleNamespace(
        id="builtin:tiktok_video",
        subcategory="tiktok",
        width=100,
        height=100,
        metadata={"safe_zone": {"top": 0, "bottom": 0, "left": 50, "right": 0, "unit": "percent"}},
    )
    mask = resolve_inset_mask(t)
    assert mask[50, 0] == 0
    assert mask[50, 60] == 255


def test_unknown_preset_returns_none():
    t = SimpleNamespace(id="builtin:odd_billboard", subcategory="ooh", width=10, height=10, metadata={})
    assert resolve_inset_mask(t) is None


def test_spec_for_bare_slug():
    t = SimpleNamespace(id="builtin:youtube_shorts", subcategory=None, width=1080, height=1920, metadata={})
    spec = spec_for_target(t)
    assert spec is not None
    assert "ui_chrome" in spec
