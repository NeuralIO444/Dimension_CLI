# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_variants.py

PR-V2 — `variant:` directives resolved against a target's orientation
bucket, and the resulting layers excluded from the gravity group-centroid
pre-pass and from SOE.

Built on the **87N fixture**, not a synthetic single-layer manifest.
CLAUDE.md's tagging/gravity checklist item 5 is explicit about this, and
the reason is visible in this exact file: 87N carries five HERO text
lines at different Y positions — the same five lines whose collapse onto
one pixel motivated the 2026-06-29 group-aware gravity fix. A synthetic
one-layer fixture would pass every assertion below while the feature was
completely broken, because a group of one has a centroid equal to its own
position and the exclusion would be unobservable.

The load-bearing test is `TestCentroidExclusion`. Everything else checks
plumbing; that one checks the thing that would actually ship a wrong
frame to a client: if a hidden layer still contributes to its group's
centroid, the VISIBLE layers land in the wrong place. That is not a
cosmetic error — it is the collapse-class bug arriving through a side
door.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path


from core.orientation import Orientation, orientation_bucket
from core.scale_engine import ScaleEngine
from core.variant_gate import resolve_variants, summarize
from models.scrape_manifest import ScrapeManifest

FIXTURE = (
    Path(__file__).parent / "fixtures" / "bug_l" / "87n_source_manifest.json"
)

# 87N is 1920x1080. Targets chosen so the aspect strategy stays `narrow`
# (the rule set that runs the group-centroid pre-pass) while the
# ORIENTATION bucket differs — that combination is what makes a
# `variant:vertical` directive active on one target and inactive on the
# other, with the centroid machinery live in both.
SQUARE_TARGET = (1080, 1080)   # bucket SQUARE   -> variant:vertical hidden
VERTICAL_TARGET = (1080, 1920)  # bucket VERTICAL -> variant:vertical shown

# The five stacked HERO lines. Named rather than indexed so a fixture
# reorder fails loudly instead of silently testing the wrong layer.
HERO_LINES = ["EVERYTHING ", "YOU WANT", "IS ON THE", "OTHER SIDE"]


def _raw() -> dict:
    with open(FIXTURE) as f:
        return json.load(f)


def _manifest(data: dict) -> ScrapeManifest:
    return ScrapeManifest.model_validate(data)


def _with_comment(data: dict, layer_name: str, comment: str) -> dict:
    """Append a directive to one named layer's existing comment.

    Appends rather than replaces: every 87N layer already carries a
    `uid:<hex>` stamp, and clobbering it would test a manifest shape that
    never occurs in production.
    """
    out = copy.deepcopy(data)
    hits = [l for l in out["layers"] if l.get("name") == layer_name]
    assert len(hits) == 1, f"expected exactly one layer named {layer_name!r}"
    existing = hits[0].get("comment") or ""
    hits[0]["comment"] = f"{existing} {comment}".strip()
    return out


def _conform(data: dict, target: tuple) -> dict:
    engine = ScaleEngine(
        manifest=_manifest(data),
        target_width=target[0],
        target_height=target[1],
        scale_mode="Auto",
        bleed_pct=0.0,
    )
    return engine.conform()


def _by_name(result: dict) -> dict:
    return {l["name"]: l for l in result["layers"]}


def _pos(result: dict, name: str):
    return _by_name(result)[name]["conformed_transforms"]["position"]


# ── Gate resolution ──────────────────────────────────────────────────


class TestVariantGate:
    def test_no_directives_means_nothing_inactive(self):
        r = resolve_variants(_manifest(_raw()), *SQUARE_TARGET)
        assert r.inactive_keys == set()
        assert not r.any_directives

    def test_directive_hides_layer_on_non_matching_bucket(self):
        data = _with_comment(_raw(), "YOU WANT", "variant:vertical")
        r = resolve_variants(_manifest(data), *SQUARE_TARGET)
        assert r.target_bucket == "SQUARE"
        assert len(r.inactive_keys) == 1

    def test_same_directive_shows_layer_on_matching_bucket(self):
        data = _with_comment(_raw(), "YOU WANT", "variant:vertical")
        r = resolve_variants(_manifest(data), *VERTICAL_TARGET)
        assert r.target_bucket == "VERTICAL"
        assert r.inactive_keys == set()
        assert r.any_directives  # declared, just not hidden here

    def test_negated_directive(self):
        data = _with_comment(_raw(), "YOU WANT", "variant:!vertical")
        assert resolve_variants(_manifest(data), *VERTICAL_TARGET).inactive_keys
        assert not resolve_variants(_manifest(data), *SQUARE_TARGET).inactive_keys

    def test_keys_are_comp_scoped_not_bare_indices(self):
        """`layer.index` alone is not unique across a recursive manifest —
        CLAUDE.md's comp-scoped-identity sharp edge. A flat-index key
        works on this single-comp fixture and corrupts the first real
        precomp manifest it meets, so assert the shape here."""
        data = _with_comment(_raw(), "YOU WANT", "variant:vertical")
        r = resolve_variants(_manifest(data), *SQUARE_TARGET)
        for key in r.inactive_keys:
            assert isinstance(key, tuple) and len(key) == 2

    def test_unreadable_directive_leaves_layer_active(self):
        """The default must always be 'renders'. A directive nobody can
        parse must never make artwork disappear."""
        data = _with_comment(_raw(), "YOU WANT", "variant:diagonal")
        r = resolve_variants(_manifest(data), *SQUARE_TARGET)
        assert r.inactive_keys == set()
        assert any("not a valid directive" in w for w in r.warnings)

    def test_summary_names_the_hidden_layers(self):
        data = _with_comment(_raw(), "YOU WANT", "variant:vertical")
        m = _manifest(data)
        text = summarize(resolve_variants(m, *SQUARE_TARGET), m)
        assert "YOU WANT" in text and "SQUARE" in text

    def test_summary_is_empty_when_no_directives(self):
        m = _manifest(_raw())
        assert summarize(resolve_variants(m, *SQUARE_TARGET), m) == ""


# ── The one that matters ─────────────────────────────────────────────


class TestCentroidExclusion:
    """A hidden layer must leave its gravity group, not merely be switched
    off at the end."""

    def test_hiding_a_line_moves_its_visible_siblings(self):
        """87N's five HERO lines are anchored as a group: `apply_gravity`
        pins the GROUP's centroid and adds back each member's offset from
        it. Removing one line changes the centroid, so every remaining
        line must land somewhere different than it did with all five
        present.

        If this fails with identical positions, the exclusion never
        reached `gather_gravity_group_positions` and a hidden layer is
        still dragging the visible ones — the collapse-class bug the
        2026-06-29 fix exists to prevent.
        """
        base = _conform(_raw(), SQUARE_TARGET)
        hidden = _conform(
            _with_comment(_raw(), "YOU WANT", "variant:vertical"),
            SQUARE_TARGET,
        )

        moved = [
            name
            for name in HERO_LINES
            if name != "YOU WANT" and _pos(base, name) != _pos(hidden, name)
        ]
        assert moved, (
            "Hiding one of five stacked HERO lines did not move any of its "
            "siblings — the variant exclusion is not reaching the gravity "
            "group-centroid pre-pass, so hidden layers still drag visible "
            "ones off their mark."
        )

    def test_layers_in_other_groups_are_untouched(self):
        """The shift must be scoped to the hidden layer's own group.

        Note which layers those are. 87N is tagged with the legacy
        vocabulary, and `HERO`, `BOXART` and `ARTWORK` are all aliases of
        `CENTER` — so the plates ("Shake", "Grain", "EFX") share a gravity
        group with the text lines and legitimately move too. The genuine
        controls are the `TT` outline layers (canonical `TOP`) and the
        `GUIDE` layer, which belong to different groups and must land
        byte-identically.

        Getting this wrong in the obvious direction — assuming a layer
        that looks unrelated is in another group — is precisely the
        normalization-gap trap CLAUDE.md documents: compare canonical
        tags, never the raw ones.
        """
        base = _conform(_raw(), SQUARE_TARGET)
        hidden = _conform(
            _with_comment(_raw(), "YOU WANT", "variant:vertical"),
            SQUARE_TARGET,
        )
        for name in (
            "EVERYTHING  Outlines",
            "YOU WANT Outlines",
            "OTHER SIDE Outlines",
            "CheckersAndMattes_3.0",
        ):
            assert _pos(base, name) == _pos(hidden, name), name

    def test_every_member_of_the_hidden_layers_group_shifts(self):
        """Complement of the above: the exclusion changes the group's
        centroid, so every remaining CENTER member moves — not just the
        text lines that visually resemble the hidden one."""
        base = _conform(_raw(), SQUARE_TARGET)
        hidden = _conform(
            _with_comment(_raw(), "YOU WANT", "variant:vertical"),
            SQUARE_TARGET,
        )
        for name in ("Shake", "Grain", "EFX", "87N.mov"):
            assert _pos(base, name) != _pos(hidden, name), name

    def test_no_directive_run_is_unchanged(self):
        """Byte-identity guard: a manifest with no directives must conform
        exactly as it did before PR-V2 existed."""
        a = _conform(_raw(), SQUARE_TARGET)
        b = _conform(_raw(), SQUARE_TARGET)
        assert [l["conformed_transforms"] for l in a["layers"]] == [
            l["conformed_transforms"] for l in b["layers"]
        ]

    def test_active_variant_matches_an_undirected_run(self):
        """A directive that does NOT hide the layer on this target must
        change nothing about the geometry — it only declares intent."""
        plain = _conform(_raw(), VERTICAL_TARGET)
        declared = _conform(
            _with_comment(_raw(), "YOU WANT", "variant:vertical"),
            VERTICAL_TARGET,
        )
        for name in HERO_LINES:
            assert _pos(plain, name) == _pos(declared, name), name


# ── Stamped output ───────────────────────────────────────────────────


class TestConformedFields:
    def test_hidden_layer_is_marked_disabled(self):
        r = _conform(
            _with_comment(_raw(), "YOU WANT", "variant:vertical"),
            SQUARE_TARGET,
        )
        assert _by_name(r)["YOU WANT"]["conformed_enabled"] is False

    def test_shown_layer_is_marked_enabled(self):
        r = _conform(
            _with_comment(_raw(), "YOU WANT", "variant:vertical"),
            VERTICAL_TARGET,
        )
        assert _by_name(r)["YOU WANT"]["conformed_enabled"] is True

    def test_declared_buckets_are_recorded(self):
        r = _conform(
            _with_comment(_raw(), "YOU WANT", "variant:horizontal,square"),
            SQUARE_TARGET,
        )
        assert _by_name(r)["YOU WANT"]["variant_buckets"] == [
            "HORIZONTAL",
            "SQUARE",
        ]

    def test_undirected_layers_carry_no_variant_fields(self):
        """Absent, not False. Babysitter must leave the video switch of a
        layer nobody wrote a directive for completely alone."""
        r = _conform(
            _with_comment(_raw(), "YOU WANT", "variant:vertical"),
            SQUARE_TARGET,
        )
        other = _by_name(r)["OTHER SIDE"]
        assert other.get("conformed_enabled") is None
        assert other.get("variant_buckets") is None

    def test_no_variant_fields_at_all_without_directives(self):
        r = _conform(_raw(), SQUARE_TARGET)
        for layer in r["layers"]:
            assert layer.get("conformed_enabled") is None
            assert layer.get("variant_buckets") is None

    def test_stamped_on_every_rule_set(self):
        """All four rule sets converge on the same stamping point, so a
        directive behaves identically whichever one a target dispatches
        to.

        The precedent this guards against is `gravity_group_size`, which
        is stamped inside `scale_engine_narrow` and therefore reaches
        NARROW conforms and WIDEN ones (widen delegates to narrow) but
        silently vanishes on PRESERVE and EQUAL_DIFFERENT_RESOLUTION —
        i.e. on every same-aspect conform, HD->4K included. A visibility
        flag that disappeared on 4K masters would be a far worse bug than
        a missing diagnostic field.

        Targets below cover all four strategies:
          1080x1080 narrow | 2560x1080 widen
          1920x1080 preserve | 3840x2160 equal_different_resolution
        """
        data = _with_comment(_raw(), "YOU WANT", "variant:vertical")
        for target in [
            (1080, 1080),
            (1080, 1920),
            (1920, 1080),
            (2560, 1080),
            (3840, 2160),
        ]:
            r = _conform(data, target)
            layer = _by_name(r)["YOU WANT"]
            expected = orientation_bucket(*target) is Orientation.VERTICAL
            assert layer["conformed_enabled"] is expected, target


# ── Sealed units ─────────────────────────────────────────────────────


class TestSealedUnits:
    def test_directive_on_a_sealed_member_is_refused_with_a_warning(self):
        """A unit whose contract is 'every member treated identically'
        cannot have one member hidden. THE INVARIANT (CLAUDE.md) is
        asserted on transforms; visibility would slip past it silently,
        so the gate refuses instead."""
        data = _with_comment(_raw(), "YOU WANT", "variant:vertical")
        m = _manifest(data)
        target_cid = next(
            (l.containing_comp_id for l in m.layers if l.name == "YOU WANT"),
            None,
        )
        r = resolve_variants(m, *SQUARE_TARGET, sealed_cids={target_cid})
        assert r.inactive_keys == set()
        assert any("sealed precomp" in w for w in r.warnings)

    def test_preserving_scene_member_is_also_refused(self):
        data = _with_comment(_raw(), "YOU WANT", "variant:vertical")
        m = _manifest(data)
        target_cid = next(
            (l.containing_comp_id for l in m.layers if l.name == "YOU WANT"),
            None,
        )
        r = resolve_variants(m, *SQUARE_TARGET, preserving_cids={target_cid})
        assert r.inactive_keys == set()
        assert any("preserving camera scene" in w for w in r.warnings)


# ── Degradation ──────────────────────────────────────────────────────


class TestDegradation:
    def test_engine_survives_a_broken_gate(self, monkeypatch):
        """If variant resolution raises, every layer renders. A feature
        that hides artwork must fail toward showing it."""
        import core.variant_gate as vg

        def boom(*a, **k):
            raise RuntimeError("simulated gate failure")

        monkeypatch.setattr(vg, "resolve_variants", boom)
        r = _conform(
            _with_comment(_raw(), "YOU WANT", "variant:vertical"),
            SQUARE_TARGET,
        )
        assert r["status"] == "SAFE"
        for layer in r["layers"]:
            assert layer.get("conformed_enabled") is not False

    def test_empty_manifest_is_safe(self):
        data = _raw()
        data["layers"] = []
        r = resolve_variants(_manifest(data), *SQUARE_TARGET)
        assert r.inactive_keys == set()


class TestSOEExclusion:
    def test_inactive_layer_is_skipped_by_the_occlusion_engine(self):
        """An invisible layer taking part in spring relaxation pushes the
        VISIBLE ones around to avoid colliding with nothing."""
        from core.occlusion.engine import OcclusionEngine

        engine = OcclusionEngine(
            mask=None, preset_id="test", inactive_keys={(None, 10)}
        )
        correction = engine._process_layer(
            {
                "containing_comp_id": None,
                "index": 10,
                "name": "hidden line",
                "content_tag": "CENTER",
                "layer_kind": "av",
                "conformed_transforms": {"position": [100.0, 200.0]},
            }
        )
        assert correction is not None
        assert correction.strategy == "SKIPPED_VARIANT_INACTIVE"

    def test_active_layers_are_not_skipped_for_that_reason(self):
        from core.occlusion.engine import OcclusionEngine

        engine = OcclusionEngine(mask=None, preset_id="test")
        assert engine.inactive_keys == set()


# ── Regression: sealed-refusal warnings must reach run_warnings (#372) ──


class TestSealedRefusalWarningReachesRunWarnings:
    """`variant_gate.py::resolve_variants` computes a warning when a
    `variant:` directive sits on a sealed-precomp/preserving-scene member
    (see TestSealedUnits above), but until this fix nothing downstream
    read `.warnings` off the resolution — the refusal was silent from the
    artist's point of view. This exercises the real full-pipeline path
    (`stages.conform.run_conform`), not `resolve_variants` in isolation,
    because the bug lived entirely in the plumbing between the two."""

    def test_directive_on_a_real_sealed_member_surfaces_in_run_warnings(
        self, tmp_path
    ):
        from stages.conform import ConformConfig, run_conform

        fresh_fixture = (
            Path(__file__).parent / "fixtures" / "session_2026_07_02"
            / "87n_fresh_manifest.json"
        )
        data = json.loads(fresh_fixture.read_text())

        # "Cyan Solid 2" lives inside the innermost sealed precomp of this
        # fixture — same ground truth test_unit_invariant.py's
        # `_graft_nested_temporal_data` relies on (a real nested comp, not
        # a fabricated sealed_cids set like TestSealedUnits above).
        hits = [l for l in data["layers"] if l.get("name") == "Cyan Solid 2"]
        assert len(hits) == 1, "fixture must carry exactly one 'Cyan Solid 2' layer"
        existing = hits[0].get("comment") or ""
        hits[0]["comment"] = f"{existing} variant:vertical".strip()

        src_path = tmp_path / "manifest.json"
        src_path.write_text(json.dumps(data))

        # SQUARE target deliberately, not VERTICAL: `variant:vertical`
        # resolves as ACTIVE on a vertical target (nothing to refuse), so
        # the re-resolve-with-sealed-cids path in build_units() only
        # triggers once the first pass finds the layer inactive on THIS
        # target's bucket -- see core/scale_engine.py::build_units().
        result = run_conform(ConformConfig(
            source=str(src_path),
            width=1080,
            height=1080,
            layout="auto",
            no_report=True,
            output=str(tmp_path / "chunks"),
        ))

        assert any(
            "Cyan Solid 2" in w and "sealed precomp" in w
            for w in result.run_warnings
        ), result.run_warnings
