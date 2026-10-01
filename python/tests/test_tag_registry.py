# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_tag_registry.py
v5.10 — coverage for the canonical tag registry.

Background: audit Finding #1
(`docs/audits/integration-contract-2026-04-26.md`) identified three
independently-evolved tag stores (Python `tag_labels.py`, Python
`tag_colors.py`, JSX `SovCore_Layer.jsx::TAG_TO_LABEL`) and the
contract drift between them. v5.10 consolidates those into
`config/tag_registry.yaml`, loaded by `python/core/tag_registry.py`.
JSX side reads a generated mirror at
`Scripts/Dimension_Assets/tag_registry.jsx`.

These tests enforce: registry shape is valid, no duplicates, the
generated JSX mirror is in sync, downstream Python consumers
actually read from the registry, and the v5.8.13 round-trip
(uid + tag in layer.comment) still works against the new bimap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.tag_registry import REGISTRY, TagDefinition, TagRegistry, reload


_REPO_ROOT = Path(__file__).resolve().parents[2]


# ── Loader + shape ─────────────────────────────────────────────────────


class TestRegistryLoader:
    def test_registry_loads_from_yaml(self):
        assert isinstance(REGISTRY, TagRegistry)
        assert REGISTRY.schema_version == "1.0"
        assert len(REGISTRY.tags) > 0

    def test_registry_is_singleton(self):
        from core.tag_registry import REGISTRY as a
        from core.tag_registry import REGISTRY as b
        assert a is b

    def test_reload_returns_registry(self):
        r = reload()
        assert isinstance(r, TagRegistry)
        # And the module-level REGISTRY now points to the new one.
        from core.tag_registry import REGISTRY as current
        assert current is r

    def test_registry_has_no_duplicate_ids(self):
        ids = [t.id for t in REGISTRY.tags]
        assert len(ids) == len(set(ids)), (
            f"duplicate ids: {[i for i in ids if ids.count(i) > 1]}"
        )

    def test_registry_has_no_duplicate_aliases(self):
        seen: dict[str, str] = {}
        for t in REGISTRY.tags:
            for a in t.aliases:
                if a in seen:
                    pytest.fail(
                        f"alias {a!r} declared on both "
                        f"{seen[a]!r} and {t.id!r}"
                    )
                seen[a] = t.id

    def test_alias_does_not_collide_with_canonical_id(self):
        ids = REGISTRY.all_ids()
        for a in REGISTRY.all_aliases():
            assert a not in ids, (
                f"alias {a!r} also exists as a canonical id — "
                f"normalize() would loop"
            )


# ── Vocabulary coverage ───────────────────────────────────────────────


class TestRegistryCoverage:
    """Defensive — anyone trimming the registry must update these
    explicit allow-lists. Catches silent drops."""

    def test_registry_covers_all_legacy_tags(self):
        """The legacy tag vocabulary (TYPE/LEGALS/etc.) must remain
        reachable as either canonical ids or aliases.
        Old heuristic forms; the registry must normalize them."""
        legacy = {"TYPE", "LEGALS", "BACKGROUND", "KEYART", "BOXART",
                  "ARTWORK", "ANIMATION", "GUIDE", "PROTECT", "NULL",
                  "TT", "LGL", "BG", "HERO", "LOGO", "BODY", "CTA", "SUP"}
        reachable = REGISTRY.all_ids() | REGISTRY.all_aliases()
        missing = legacy - reachable
        assert not missing, (
            f"registry dropped legacy heuristic tags: {sorted(missing)}"
        )

    def test_registry_covers_v6_canonical_tags(self):
        """v6.0 4-tag canonical vocabulary must exist as canonical ids."""
        v6_canonical = {"FILL", "CENTER", "TOP", "BOTTOM", "OVERLAY",
                        "DISC", "GUIDE", "PROTECT", "NULL", "UNCLASS"}
        canonical = REGISTRY.all_ids()
        missing = v6_canonical - canonical
        assert not missing, (
            f"registry missing v6.0 canonical ids: "
            f"{sorted(missing)}"
        )


# ── Lookup helpers ─────────────────────────────────────────────────────


class TestRegistryLookups:
    def test_by_id_returns_definition(self):
        t = REGISTRY.by_id("TOP")
        assert isinstance(t, TagDefinition)
        assert t.id == "TOP"

    def test_by_id_unknown_returns_none(self):
        assert REGISTRY.by_id("NOT_A_REAL_TAG") is None
        assert REGISTRY.by_id("") is None

    def test_normalize_canonical_returns_self(self):
        assert REGISTRY.normalize("TOP") == "TOP"
        assert REGISTRY.normalize("BOTTOM") == "BOTTOM"
        assert REGISTRY.normalize("FILL") == "FILL"
        assert REGISTRY.normalize("CENTER") == "CENTER"

    def test_normalize_alias_returns_canonical(self):
        # v6.0 alias mapping:
        assert REGISTRY.normalize("TT") == "TOP"
        assert REGISTRY.normalize("TYPE") == "TOP"
        assert REGISTRY.normalize("LGL") == "BOTTOM"
        assert REGISTRY.normalize("LEGALS") == "BOTTOM"
        assert REGISTRY.normalize("BG") == "FILL"
        assert REGISTRY.normalize("BACKGROUND") == "FILL"
        assert REGISTRY.normalize("HERO") == "CENTER"
        assert REGISTRY.normalize("KEYART") == "CENTER"
        assert REGISTRY.normalize("BOXART") == "CENTER"
        assert REGISTRY.normalize("ARTWORK") == "CENTER"
        assert REGISTRY.normalize("ANIMATION") == "CENTER"
        assert REGISTRY.normalize("LOGO") == "CENTER"
        assert REGISTRY.normalize("BODY") == "CENTER"
        assert REGISTRY.normalize("SUP") == "CENTER"
        assert REGISTRY.normalize("CTA") == "BOTTOM"

    def test_normalize_unknown_returns_none(self):
        assert REGISTRY.normalize("WHATEVER") is None
        assert REGISTRY.normalize(None) is None
        assert REGISTRY.normalize("") is None

    def test_color_for_returns_hex(self):
        c = REGISTRY.color_for("TOP")
        assert c is not None
        assert c.startswith("#") and len(c) == 7
        # alias resolves to same color (TT → TOP)
        assert REGISTRY.color_for("TT") == c

    def test_label_for_returns_int(self):
        assert isinstance(REGISTRY.label_for("TOP"), int)
        # TT is now an alias for TOP, so they share the same label
        assert REGISTRY.label_for("TOP") == REGISTRY.label_for("TT")
        # unknown → 0 (no label, UI degrades gracefully)
        assert REGISTRY.label_for("NOPE") == 0

    def test_gravity_for_returns_valid_string(self):
        valid = {"top", "bottom", "center", "leftMid",
                 "rightMid", "fill", "none"}
        for t in REGISTRY.tags:
            g = REGISTRY.gravity_for(t.id)
            assert g in valid, f"{t.id} → invalid gravity {g!r}"

    def test_label_to_id_unique(self):
        """AE label color uniquely identifies a tag (per the
        registry uniqueness validator). v6.0 assigns:
          FILL=1 (Red), CENTER=9 (Green), TOP=10 (Purple), BOTTOM=8 (Blue), DISC=13.

        Label 0 → None (no label), unchanged."""
        assert REGISTRY.label_to_id(10) == "TOP"
        assert REGISTRY.label_to_id(8) == "BOTTOM"
        assert REGISTRY.label_to_id(1) == "FILL"
        assert REGISTRY.label_to_id(9) == "CENTER"
        assert REGISTRY.label_to_id(13) == "DISC"
        assert REGISTRY.label_to_id(0) is None

    def test_label_to_id_legacy_pre_117_colors(self):
        """Pre-#117 per-alias colours round-trip to canonical tags."""
        assert REGISTRY.label_to_id(2) == "TOP"      # Yellow legacy TT
        assert REGISTRY.label_to_id(3) == "CENTER"   # ARTWORK
        assert REGISTRY.label_to_id(4) == "CENTER"   # SUP
        assert REGISTRY.label_to_id(5) == "CENTER"   # BOXART
        assert REGISTRY.label_to_id(15) == "GUIDE"
        assert REGISTRY.label_to_id(16) is None

    def test_no_two_non_zero_labels_share_a_tag(self):
        """The registry-uniqueness validator runs on load. This test
        is a positive smoke check that the canonical registry
        actually satisfies the rule, not just that the validator
        would catch a violation. Pinning the rule lets a future
        registry edit that reintroduces a collision fail CI here
        before reaching production."""
        seen: dict[int, str] = {}
        for t in REGISTRY.tags:
            if t.ae_label_color == 0:
                continue
            assert t.ae_label_color not in seen, (
                f"label {t.ae_label_color} claimed by both "
                f"{seen[t.ae_label_color]!r} and {t.id!r} — "
                f"the uniqueness validator should have refused this"
            )
            seen[t.ae_label_color] = t.id


# ── Validation ─────────────────────────────────────────────────────────


class TestRegistryValidation:
    def test_invalid_color_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TagDefinition(
                id="X", display_name="X", abbrev="X",
                color_hex="not-a-color", ae_label_color=2,
                default_gravity="top",
            )

    def test_invalid_label_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TagDefinition(
                id="X", display_name="X", abbrev="X",
                color_hex="#aabbcc", ae_label_color=99,
                default_gravity="top",
            )

    def test_invalid_gravity_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TagDefinition(
                id="X", display_name="X", abbrev="X",
                color_hex="#aabbcc", ae_label_color=2,
                default_gravity="diagonal",
            )

    def test_invalid_semantic_class_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TagDefinition(
                id="X", display_name="X", abbrev="X",
                color_hex="#aabbcc", ae_label_color=2,
                default_gravity="top",
                semantic_class="banana",
            )

    def test_duplicate_non_zero_label_raises(self):
        """Registry-level uniqueness validator: two tags claiming
        the same non-zero AE label color must fail registry load.
        This is the negative-case proof that the validator added
        2026-04-28 actually fires when a future edit would
        reintroduce the TT/SUP-style collision."""
        from pydantic import ValidationError
        a = TagDefinition(
            id="ALPHA", display_name="Alpha", abbrev="A",
            color_hex="#aabbcc", ae_label_color=5,
            default_gravity="top",
        )
        b = TagDefinition(
            id="BETA", display_name="Beta", abbrev="B",
            color_hex="#ddeeff", ae_label_color=5,  # same as ALPHA
            default_gravity="top",
        )
        with pytest.raises(ValidationError) as exc_info:
            TagRegistry(schema_version="1.0", tags=(a, b))
        assert "ALPHA" in str(exc_info.value)
        assert "BETA" in str(exc_info.value)
        assert "5" in str(exc_info.value)

    def test_label_zero_collision_is_allowed(self):
        """Label 0 is the documented "no label" sentinel — NULL and
        UNCLASS both use it intentionally. The validator must
        EXCLUDE label 0 from the uniqueness rule."""
        a = TagDefinition(
            id="NONE_A", display_name="None A", abbrev="A",
            color_hex="#aabbcc", ae_label_color=0,
            default_gravity="none",
        )
        b = TagDefinition(
            id="NONE_B", display_name="None B", abbrev="B",
            color_hex="#ddeeff", ae_label_color=0,
            default_gravity="none",
        )
        # Should NOT raise — both claim the sentinel.
        registry = TagRegistry(schema_version="1.0", tags=(a, b))
        assert len(registry.tags) == 2


# ── Downstream consumer wiring ────────────────────────────────────────


class TestConsumerModulesReadFromRegistry:
    """Verify the migrations actually wired through —
    each consumer should reflect a registry change without code
    edits."""

    def test_surveyor_VALID_TAGS_includes_canonical_and_aliases(self):
        from core.surveyor import VALID_TAGS
        for t in REGISTRY.tags:
            assert t.id in VALID_TAGS
            for a in t.aliases:
                assert a in VALID_TAGS


# ── JSX mirror parity ─────────────────────────────────────────────────


_JSX_PATH = (
    _REPO_ROOT / "Scripts" / "Dimension_Assets" / "tag_registry.jsx"
)


class TestJsxMirrorInSync:
    def test_jsx_file_exists(self):
        assert _JSX_PATH.exists(), (
            "JSX mirror missing — run "
            "`python python/scripts/generate_jsx_tag_mirror.py`"
        )

    def test_jsx_mirror_matches_yaml(self):
        """Regenerate the mirror from the current YAML and compare
        byte-for-byte against the on-disk file. Fails loudly if you
        edited the YAML without regenerating, or vice versa."""
        from scripts.generate_jsx_tag_mirror import render
        on_disk = _JSX_PATH.read_text(encoding="utf-8")
        generated = render()
        assert on_disk == generated, (
            "tag_registry.jsx is out of sync with tag_registry.yaml. "
            "Run: python python/scripts/generate_jsx_tag_mirror.py"
        )

    def test_jsx_lists_every_canonical_id(self):
        text = _JSX_PATH.read_text(encoding="utf-8")
        for t in REGISTRY.tags:
            assert f'"{t.id}"' in text, (
                f"canonical id {t.id!r} absent from JSX mirror"
            )

    def test_jsx_lists_every_alias(self):
        text = _JSX_PATH.read_text(encoding="utf-8")
        for a in REGISTRY.all_aliases():
            assert f'"{a}"' in text, (
                f"alias {a!r} absent from JSX TAG_ALIASES"
            )

    def test_jsx_schema_version_matches(self):
        text = _JSX_PATH.read_text(encoding="utf-8")
        assert (
            f'DIMENSION_TAG_REGISTRY_VERSION = "{REGISTRY.schema_version}"'
            in text
        )


# ── CEP JS mirror parity ─────────────────────────────────────────────


_JS_PATH = _REPO_ROOT / "cep" / "js" / "tag_registry.js"


class TestJsMirrorInSync:
    """Same contract as the JSX mirror, but for the CEP panel's
    Chromium-side tag registry. The HTML/JS layer cannot parse YAML
    without bundling a dependency, so the generator emits a JS
    mirror alongside the JSX mirror. Drift between YAML and either
    mirror fails CI."""

    def test_js_file_exists(self):
        assert _JS_PATH.exists(), (
            "JS mirror missing — run "
            "`python python/scripts/generate_js_tag_mirror.py`"
        )

    def test_js_mirror_matches_yaml(self):
        from scripts.generate_js_tag_mirror import render
        on_disk = _JS_PATH.read_text(encoding="utf-8")
        generated = render()
        assert on_disk == generated, (
            "cep/js/tag_registry.js is out of sync with tag_registry.yaml. "
            "Run: python python/scripts/generate_js_tag_mirror.py"
        )

    def test_js_lists_every_canonical_id(self):
        text = _JS_PATH.read_text(encoding="utf-8")
        for t in REGISTRY.tags:
            assert f'"{t.id}"' in text, (
                f"canonical id {t.id!r} absent from JS mirror"
            )

    def test_js_lists_every_alias(self):
        text = _JS_PATH.read_text(encoding="utf-8")
        for a in REGISTRY.all_aliases():
            assert f'"{a}"' in text, (
                f"alias {a!r} absent from JS TAG_ALIASES"
            )

    def test_js_schema_version_matches(self):
        text = _JS_PATH.read_text(encoding="utf-8")
        assert (
            f'DIMENSION_TAG_REGISTRY_VERSION = "{REGISTRY.schema_version}"'
            in text
        )


# ── v5.8.13 round-trip — registry must not break the tag-write fix ───


class TestV58RoundTripUnbroken:
    """v5.8.12-14 fixed the `layer not found for uid` family of
    bugs. The fix relies on TAG_TO_LABEL.hasOwnProperty(tag) in
    SovCore_Layer.jsx::applyManualTag. After registry consolidation
    that bimap is built dynamically from the registry — so every
    tag the +TAG popover offers must still resolve to a valid
    label index."""

    def test_every_canonical_tag_writes_a_label(self):
        for t in REGISTRY.tags:
            if t.ae_label_color == 0:
                # NULL/UNCLASS — intentional 0
                continue
            assert REGISTRY.label_for(t.id) == t.ae_label_color

    def test_v6_canonical_tags_write_labels(self):
        """v6.0 canonical tags resolve to their declared AE label colors."""
        for tag, expected_label in [
            ("FILL", 1), ("CENTER", 9), ("TOP", 10), ("BOTTOM", 8),
            ("OVERLAY", 7), ("DISC", 13),
        ]:
            assert REGISTRY.label_for(tag) == expected_label, (
                f"v6.0 contract break: label_for({tag!r}) "
                f"returned {REGISTRY.label_for(tag)} not {expected_label}"
            )

    def test_legacy_aliases_still_write_labels(self):
        # Legacy forms now resolve through v6.0 canonical aliases.
        assert REGISTRY.label_for("TT") == REGISTRY.label_for("TOP")
        assert REGISTRY.label_for("TYPE") == REGISTRY.label_for("TOP")
        assert REGISTRY.label_for("LGL") == REGISTRY.label_for("BOTTOM")
        assert REGISTRY.label_for("LEGALS") == REGISTRY.label_for("BOTTOM")
        assert REGISTRY.label_for("BG") == REGISTRY.label_for("FILL")
        assert REGISTRY.label_for("BACKGROUND") == REGISTRY.label_for("FILL")
        assert REGISTRY.label_for("HERO") == REGISTRY.label_for("CENTER")
        assert REGISTRY.label_for("KEYART") == REGISTRY.label_for("CENTER")
