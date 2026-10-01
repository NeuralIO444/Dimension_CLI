from core.naming_context import (
    apply_template,
    build_naming_context,
    context_from_target,
    slug_preset,
)
from types import SimpleNamespace


def test_legacy_four_tokens():
    ctx = build_naming_context(
        source="Hero", preset_id="builtin:tiktok_video", width=1080, height=1920
    )
    assert apply_template("{source}_{preset}", ctx) == "Hero_tiktok_video"
    assert apply_template("{source}_{width}x{height}", ctx) == "Hero_1080x1920"
    assert ctx["wxh"] == "1080x1920"


def test_unknown_token_survives():
    ctx = build_naming_context(source="A")
    assert apply_template("{source}_{future}", ctx) == "A_{future}"


def test_optional_empty_on_social():
    ctx = build_naming_context(source="A", preset_id="builtin:tiktok_video")
    assert apply_template("{source}_p{panel}", ctx) == "A_p"


def test_slug_strips_prefix():
    assert slug_preset("builtin:tiktok_video") == "tiktok_video"
    assert slug_preset("") == "conform"


def test_context_from_target():
    t = SimpleNamespace(
        id="builtin:tiktok_video",
        label="TikTok · Standard Video",
        width=1080,
        height=1920,
        subcategory="tiktok",
        aspect_label="9:16",
        category="social",
        metadata={"safe_zone": {"pack": "chrome"}},
    )
    ctx = context_from_target(t, source="Comp")
    assert ctx["platform"] == "tiktok"
    assert ctx["aspect"] == "9x16"
    assert ctx["pack"] == "chrome"
    assert apply_template("{source}_{platform}_{aspect}", ctx) == "Comp_tiktok_9x16"
