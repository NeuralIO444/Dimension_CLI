from types import SimpleNamespace

from logic.safe_zone_packs import (
    CUTOFF,
    GO,
    NUDGE,
    default_pack_for_target,
    resolve_pack_mask,
    synthesize_broadcast_mask,
)
from data.target_catalog import BUILTIN_TARGETS
from logic.safe_zone_insets import resolve_inset_mask


def test_broadcast_zones_on_100_square():
    m = synthesize_broadcast_mask(100, 100)
    assert m.shape == (100, 100)
    assert m[0, 50] == CUTOFF
    assert m[15, 50] == NUDGE
    assert m[50, 50] == GO


def test_cinema_and_uhd_default_to_broadcast():
    cinema = [t for t in BUILTIN_TARGETS if t.category == "digital_cinema"]
    uhd = [t for t in BUILTIN_TARGETS if t.category == "uhd_broadcast"]
    assert cinema and uhd
    assert all(default_pack_for_target(t) == "broadcast" for t in cinema + uhd)


def test_uhd_gets_a_mask():
    t = next(t for t in BUILTIN_TARGETS if t.category == "uhd_broadcast")
    via_inset = resolve_inset_mask(t)
    via_pack = resolve_pack_mask(t)
    assert via_pack is not None
    assert via_inset is not None
    assert via_inset.shape == (t.height, t.width)
    assert via_inset[0, t.width // 2] == CUTOFF
    assert via_inset[t.height // 2, t.width // 2] == GO


def test_tiktok_is_not_broadcast_pack():
    t = SimpleNamespace(
        category="social",
        channel="social",
        width=1080,
        height=1920,
        id="builtin:tiktok_video",
        subcategory="tiktok",
        metadata={},
    )
    assert default_pack_for_target(t) is None
