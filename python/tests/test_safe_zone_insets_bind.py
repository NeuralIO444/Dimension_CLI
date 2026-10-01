from logic.safe_zone_insets import reset_inset_catalog_cache, resolve_inset_mask, spec_for_target
from data.target_catalog import BUILTIN_TARGETS
from logic.safe_zone_resolver import reset_missing_log, resolve_mask_for_target


def setup_function(_fn=None):
    reset_inset_catalog_cache()
    reset_missing_log()


def test_every_social_builtin_has_an_inset_spec():
    missing = []
    for t in BUILTIN_TARGETS:
        if t.category != "social":
            continue
        if spec_for_target(t) is None:
            missing.append(t.id)
    assert missing == [], f"social targets with no inset spec: {missing}"


def test_every_social_builtin_resolves_a_mask():
    missing = []
    for t in BUILTIN_TARGETS:
        if t.category != "social":
            continue
        hit = resolve_mask_for_target(t)
        if hit is None:
            missing.append(t.id)
    assert missing == [], f"social targets still MASK_MISSING: {missing}"


def test_non_social_untouched_when_no_spec():
    ooh = [t for t in BUILTIN_TARGETS if t.category == "custom_signage"]
    if not ooh:
        return
    # OOH without multi_panel and without a YAML key may still be None
    # — that is correct. Just assert the helper does not crash.
    resolve_inset_mask(ooh[0])
