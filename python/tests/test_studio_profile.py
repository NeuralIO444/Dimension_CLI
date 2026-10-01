# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_studio_profile.py
v5.2.0 regression coverage:
  - YAML loader (baseline profiles round-trip)
  - extends inheritance + child overrides
  - pinned-weight protection (parent pinned not overridable by child)
  - resolve(layer_name) — first-prefix-wins ordering
  - state.json active-profile persistence
  - to_yaml round-trip stability (load → emit → reparse)

Slot 11.5 Phase 2 — 9 retired profiles (Universal_HV / Netflix /
Disney+ / A24 / 87N / sports_broadcast / news_broadcast /
agency_commercial / trailer_theatrical) replaced with 4 flat
use-case profiles (default / social / theatrical / ooh). The
extends-inheritance behavior is still in the schema; the new
data just doesn't exercise it, so the data-driven extends tests
(Disney+ extends Universal_HV) are dropped here — coverage stays
via the synthetic-profile tests in TestExtendsInheritance.
"""

import os
import sys
import json
import tempfile
import shutil
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
))

from logic.studio_profile_registry import StudioProfileRegistry
from models.studio_profile import (
    StudioProfile,
    ProfileRule,
    SafeArea,
    merge_extends,
)


REPO_BASELINES = Path(__file__).resolve().parents[2] / "config" / "profiles"


@pytest.fixture
def temp_registry():
    """Fresh registry rooted in a tmp dir. Each test gets its own state.
    Baselines come from the repo's `config/profiles/` so we test against
    the real shipped data rather than mock fixtures."""
    tmp = Path(tempfile.mkdtemp(prefix="dim_sp_"))
    user_dir = tmp / "profiles"
    reg = StudioProfileRegistry(user_dir=user_dir, baselines_dir=REPO_BASELINES)
    yield reg
    shutil.rmtree(tmp, ignore_errors=True)


# ── Baseline loading ────────────────────────────────────────────────


class TestBaselineLoading:
    def test_seeds_from_repo_baselines(self, temp_registry):
        ids = {p.id for p in temp_registry.list_profiles()}
        assert "default" in ids
        assert "social" in ids
        assert "theatrical" in ids
        assert "ooh" in ids

    def test_default_sorts_first(self, temp_registry):
        # After the 9→4 reduction the legacy `Universal_HV` first-
        # sort key is no longer present, so the registry falls
        # through to lowercase-alphabetical: default < ooh < social
        # < theatrical.
        first = temp_registry.list_profiles()[0]
        assert first.id == "default", (
            "default must sort first in the registry list"
        )

    def test_default_rules_count(self, temp_registry):
        d = temp_registry.get("default")
        assert d is not None
        # v6.0 — `default.yaml` ships more prefix rules to cover both
        # legacy (HERO_, BOXART_) and canonical (CENTER_, FILL_, TOP_,
        # BOTTOM_) naming conventions. At least 8 rules expected.
        assert len(d.prefixes) >= 8

    def test_all_new_profiles_have_no_extends(self, temp_registry):
        # Slot 11.5 Phase 2 — the new four-profile model is flat;
        # no profile inherits from another. Schema still supports
        # `extends:` (covered by TestExtendsInheritance synthetic
        # tests), the new data just doesn't use it.
        for pid in ("default", "social", "theatrical", "ooh"):
            p = temp_registry.get(pid)
            assert p is not None
            assert p.extends is None, (
                f"{pid} must not extend another profile in the "
                f"flat four-profile baseline"
            )


# ── Extends inheritance ─────────────────────────────────────────────


class TestExtendsInheritance:
    # Slot 11.5 Phase 2 — the new four-profile baseline is flat
    # (no extends). Schema still supports `extends:`; coverage now
    # lives entirely in the synthetic-profile tests below. Removed
    # from this class: `test_disneyplus_inherits_unspecified_rules`
    # and `test_disneyplus_overrides_title_gravity` — both relied
    # on legacy Disney+/Universal_HV data that no longer ships.

    def test_synthetic_child_inherits_unspecified_rules(self):
        """A child that doesn't redefine a parent's prefix
        inherits it via merge_extends. Synthetic profiles so the
        coverage is data-independent."""
        parent = StudioProfile(
            id="P", display_name="P",
            prefixes=[
                ProfileRule(match="SUP_", tag="SUP", gravity="top",
                            scale=1.0, weight="normal"),
                ProfileRule(match="HERO_", tag="HERO",
                            gravity="center", scale=1.0,
                            weight="normal"),
            ],
        )
        child = StudioProfile(
            id="C", display_name="C", extends="P",
            prefixes=[
                # Child only redefines HERO_.
                ProfileRule(match="HERO_", tag="HERO",
                            gravity="fill", scale=1.05,
                            weight="high"),
            ],
        )
        merged = merge_extends(child, parent)
        sup_rule = next((r for r in merged.prefixes
                          if r.match == "SUP_"), None)
        assert sup_rule is not None, (
            "child should inherit parent's SUP_ rule when it "
            "doesn't redefine it"
        )
        assert sup_rule.tag == "SUP"

    def test_synthetic_child_overrides_parent_gravity(self):
        """A child that redefines a parent's prefix wins on
        gravity / scale / weight. Synthetic version of the
        retired Disney+/Universal_HV TITLE_ override test."""
        parent = StudioProfile(
            id="P", display_name="P",
            prefixes=[
                ProfileRule(match="TITLE_", tag="TT",
                            gravity="top", scale=1.0,
                            weight="high"),
            ],
        )
        child = StudioProfile(
            id="C", display_name="C", extends="P",
            prefixes=[
                ProfileRule(match="TITLE_", tag="TT",
                            gravity="bottom", scale=1.05,
                            weight="high"),
            ],
        )
        merged = merge_extends(child, parent)
        title_rule = next(r for r in merged.prefixes
                           if r.match == "TITLE_")
        assert title_rule.gravity == "bottom"
        assert title_rule.scale == 1.05

    def test_pinned_parent_protected(self):
        """A child rule can't override a parent rule with weight=pinned
        unless the child is also pinned. Verify in isolation."""
        parent = StudioProfile(
            id="P", display_name="P",
            prefixes=[
                ProfileRule(match="LEGAL_", tag="LGL", gravity="bottom",
                            scale=0.9, weight="pinned"),
            ],
        )
        child = StudioProfile(
            id="C", display_name="C", extends="P",
            prefixes=[
                # Tries to override pinned LEGAL_ without itself being pinned.
                ProfileRule(match="LEGAL_", tag="LGL", gravity="top",
                            scale=2.0, weight="normal"),
            ],
        )
        merged = merge_extends(child, parent)
        legal = next(r for r in merged.prefixes if r.match == "LEGAL_")
        assert legal.weight == "pinned"
        assert legal.gravity == "bottom"
        assert legal.scale == 0.9, "parent pinned rule must win"

    def test_pinned_can_be_overridden_by_pinned(self):
        """Child can take the lock with its own weight=pinned."""
        parent = StudioProfile(
            id="P", display_name="P",
            prefixes=[
                ProfileRule(match="LEGAL_", tag="LGL", gravity="bottom",
                            scale=0.9, weight="pinned"),
            ],
        )
        child = StudioProfile(
            id="C", display_name="C", extends="P",
            prefixes=[
                ProfileRule(match="LEGAL_", tag="DISC", gravity="bottom",
                            scale=0.75, weight="pinned"),
            ],
        )
        merged = merge_extends(child, parent)
        legal = next(r for r in merged.prefixes if r.match == "LEGAL_")
        assert legal.tag == "DISC"
        assert legal.scale == 0.75


# ── Rule provenance (Track B / B3, 2026-08-26) ─────────────────────


class TestRuleProvenance:
    """`StudioProfileRegistry.rule_provenance` — is_inherited /
    source_profile annotation consumed by profile_cli.py `show` and the
    CEP Profile Editor's inherited-vs-local rule styling.

    Populates `_cache` directly rather than via `save()`/`reload()`: on
    a brand-new empty user_dir, the registry's first-load migration
    path (`_migrate_user_dir_if_needed`) can wipe and reseed from
    baselines, which is unrelated behavior this test isn't exercising.
    Bypassing it keeps the test scoped to `rule_provenance` alone.
    """

    def _registry(self, tmp_path):
        reg = StudioProfileRegistry(user_dir=tmp_path / "profiles")
        reg._loaded = True  # skip _ensure_loaded's migration/seed path
        return reg

    def test_no_extends_marks_every_rule_local(self, tmp_path):
        reg = self._registry(tmp_path)
        profile = StudioProfile(
            id="flat", display_name="Flat",
            prefixes=[ProfileRule(match="TT_", tag="TT")],
        )
        reg._cache = {"flat": profile}
        prov = reg.rule_provenance(profile)
        assert len(prov) == 1
        assert prov[0]["is_inherited"] is False
        assert prov[0]["source_profile"] == "flat"

    def test_verbatim_carryover_marked_inherited(self, tmp_path):
        reg = self._registry(tmp_path)
        parent = StudioProfile(
            id="P", display_name="P",
            prefixes=[ProfileRule(match="SUP_", tag="SUP", gravity="top")],
        )
        child = StudioProfile(
            id="C", display_name="C", extends="P",
            prefixes=[ProfileRule(match="HERO_", tag="HERO", gravity="fill")],
        )
        merged = merge_extends(child, parent)
        reg._cache = {"P": parent, "C": merged}

        prov = reg.rule_provenance(merged)
        sup = next(r for r in prov if r["match"] == "SUP_")
        hero = next(r for r in prov if r["match"] == "HERO_")
        assert sup["is_inherited"] is True
        assert sup["source_profile"] == "P"
        assert hero["is_inherited"] is False
        assert hero["source_profile"] == "C"

    def test_overridden_rule_marked_local_not_inherited(self, tmp_path):
        """A child rule sharing a parent's match but changing any field
        (gravity/scale/weight/tag) is local — an override, not a
        carryover — even though the match string is the same."""
        reg = self._registry(tmp_path)
        parent = StudioProfile(
            id="P", display_name="P",
            prefixes=[ProfileRule(match="TITLE_", tag="TT",
                                   gravity="top", scale=1.0)],
        )
        child = StudioProfile(
            id="C", display_name="C", extends="P",
            prefixes=[ProfileRule(match="TITLE_", tag="TT",
                                   gravity="bottom", scale=1.05)],
        )
        merged = merge_extends(child, parent)
        reg._cache = {"P": parent, "C": merged}

        prov = reg.rule_provenance(merged)
        title = next(r for r in prov if r["match"] == "TITLE_")
        assert title["is_inherited"] is False
        assert title["source_profile"] == "C"
        assert title["gravity"] == "bottom"

    def test_missing_parent_falls_back_to_all_local(self, tmp_path):
        """extends points at an id the registry doesn't have (deleted
        parent, load-order edge case) — don't crash; every rule reports
        local rather than raising."""
        reg = self._registry(tmp_path)
        orphan = StudioProfile(
            id="orphan", display_name="Orphan", extends="ghost",
            prefixes=[ProfileRule(match="TT_", tag="TT")],
        )
        reg._cache = {"orphan": orphan}
        prov = reg.rule_provenance(orphan)
        assert prov[0]["is_inherited"] is False
        assert prov[0]["source_profile"] == "orphan"


# ── resolve() ───────────────────────────────────────────────────────


class TestResolve:
    def test_first_prefix_wins(self, temp_registry):
        # social profile ships TITLE_ → TOP/top (v6.0 canonical).
        s = temp_registry.get("social")
        rule = s.resolve("TITLE_MAIN_v2")
        assert rule is not None
        assert rule.tag == "TOP"
        assert rule.gravity == "top"

    def test_unknown_layer_returns_none(self, temp_registry):
        d = temp_registry.get("default")
        assert d.resolve("RANDOM_NAME") is None
        assert d.resolve("") is None

    def test_ooh_scale_modifier(self, temp_registry):
        """ooh profile ships HERO_ → center with scale 1.15
        (aggressive fit for large-format displays). Replaces the
        retired A24 centerH×1.20 coverage."""
        o = temp_registry.get("ooh")
        rule = o.resolve("HERO_BIG")
        assert rule is not None
        assert rule.gravity == "center"
        assert rule.scale == 1.15

    def test_social_legal_pinned(self, temp_registry):
        """social profile ships LEGAL_ → LGL with weight=pinned
        (legal copy can't be overridden by downstream presets).
        Replaces the retired Netflix LOGO_LOCKUP pinned coverage."""
        s = temp_registry.get("social")
        rule = s.resolve("LEGAL_DISCLOSURE")
        assert rule is not None
        assert rule.weight == "pinned"


# ── Active-profile persistence ──────────────────────────────────────


class TestActiveState:
    def test_default_active_is_default_profile(self, temp_registry):
        # No state.json → get_active falls through to the
        # alphabetically-first profile, which is `default` after
        # the 9→4 reduction. (`get_active`'s "Universal_HV"
        # hardcode is leftover and harmless — `get("Universal_HV")`
        # returns None, the `or` clause falls through to
        # `list_profiles()[0]`. Commit E retires the hardcode.)
        active = temp_registry.get_active()
        assert active is not None
        assert active.id == "default"

    def test_set_and_persist_active(self, temp_registry):
        ok = temp_registry.set_active("social")
        assert ok
        # Re-read state.json and verify.
        assert temp_registry.state_path.exists()
        with temp_registry.state_path.open() as f:
            state = json.load(f)
        assert state["active_studio_profile"] == "social"

    def test_set_active_rejects_unknown(self, temp_registry):
        ok = temp_registry.set_active("DoesNotExist")
        assert not ok

    def test_active_survives_reload(self, temp_registry):
        temp_registry.set_active("theatrical")
        temp_registry.reload()
        assert temp_registry.get_active().id == "theatrical"


# ── YAML round-trip ─────────────────────────────────────────────────


class TestYAMLRoundTrip:
    def test_to_yaml_emits_valid_yaml(self, temp_registry):
        d = temp_registry.get("default")
        body = temp_registry.to_yaml(d)
        # Trivial parse-back via pyyaml.
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml missing")
        data = yaml.safe_load(body)
        assert data["id"] == "default"
        # v6.0 default.yaml ships more prefix rules (at least 8).
        assert len(data["prefixes"]) >= 8

    def test_save_and_reload(self, temp_registry):
        d = temp_registry.get("default")
        # Mutate a copy and round-trip. v6.0: use TOP (was TT).
        edited = d.model_copy(update={
            "revision": "v9.9.9",
            "type_overrides": {**d.type_overrides, "TOP": 1.42},
        })
        path = temp_registry.save(edited)
        assert os.path.exists(path)
        temp_registry.reload()
        reloaded = temp_registry.get("default")
        assert reloaded.revision == "v9.9.9"
        assert abs(reloaded.type_overrides["TOP"] - 1.42) < 1e-6


# ── Schema guards ───────────────────────────────────────────────────


class TestSurveyorProfileWiring:
    """v5.2.1: Surveyor consumes the active Studio Profile.

    classify_layer Pass 2 runs a `profile.resolve(layer.name)` lookup
    BEFORE the structural / heuristic passes. Hits emit source=profile
    and a high-confidence TagResult. tag_breakdown returns per-tag counts
    suitable for the orchestrator's chip row."""

    def _layer(self, name: str):
        # Lightweight stand-in for LayerModel — Surveyor only reads
        # `name` and writes `content_tag*`.
        class L:
            def __init__(self, n):
                self.name = n
                self.content_tag = None
                self.content_tag_source = None
                self.content_tag_confidence = None
        return L(name)

    def _manifest(self, *names):
        class M:
            layers = [self._layer(n) for n in names]
            project_info = type("P", (), {"width": 1920, "height": 1080})()
        return M()

    def test_default_resolves_canonical_prefixes(self, temp_registry):
        from core.surveyor import survey_manifest
        d = temp_registry.get("default")
        # v6.0 — Layer names match `default`'s prefix rules
        # (HERO_, BOXART_, ARTWORK_, LOGO_, BRAND_, TITLE_, LEGAL_, BG_).
        # Profile emits v6.0 canonical tags (CENTER, TOP, BOTTOM, FILL).
        m = self._manifest(
            "TITLE_MAIN", "HERO_PRODUCT", "LOGO_LOCKUP",
            "BOXART_PACK", "ARTWORK_TILE", "LEGAL_RATING",
            "BG_GRADIENT",
        )
        counts = survey_manifest(m, profile=d)
        assert counts["profile"] == 7
        assert counts["heuristic"] == 0
        breakdown = counts["tag_breakdown"]
        # v6.0: all graphic content → CENTER, all text → TOP/BOTTOM, bg → FILL
        assert breakdown == {
            "TOP": 1, "CENTER": 4, "BOTTOM": 1, "FILL": 1,
        }

    def test_social_overrides_default_title_gravity(self, temp_registry):
        """social ships TITLE_ → TOP/top (vs default's TITLE_ →
        TOP/center). Same tag, different gravity by profile —
        exercises the profile-driven gravity-override path.
        v6.0: TT→TOP, BG→FILL."""
        from core.surveyor import survey_manifest
        s = temp_registry.get("social")
        m = self._manifest("TITLE_HERO", "BG_PLATE")
        counts = survey_manifest(m, profile=s)
        breakdown = counts["tag_breakdown"]
        assert breakdown["TOP"] == 1
        assert breakdown["FILL"] == 1

    def test_no_profile_falls_back_to_legacy(self, temp_registry):
        """When profile=None, behaviour matches v5.1 — heuristic only."""
        from core.surveyor import survey_manifest
        m = self._manifest("TITLE_MAIN", "HERO_PRODUCT")
        counts = survey_manifest(m, profile=None)
        assert counts["profile"] == 0
        # Layers with no manual tag and no profile go to heuristic /
        # unclassified depending on the heuristic scorer.
        assert counts["profile_id"] is None

    def test_unmatched_layer_falls_through(self, temp_registry):
        """A layer name that doesn't match any prefix should NOT come
        back as `profile` source — heuristic / unclassified."""
        from core.surveyor import survey_manifest
        d = temp_registry.get("default")
        m = self._manifest("ZZ_RANDOM_NAME")
        counts = survey_manifest(m, profile=d)
        assert counts["profile"] == 0

    def test_profile_id_recorded(self, temp_registry):
        from core.surveyor import survey_manifest
        s = temp_registry.get("social")
        m = self._manifest("TITLE_HERO", "BG_PLATE")
        counts = survey_manifest(m, profile=s)
        assert counts["profile_id"] == "social"


class TestSchemaGuards:
    def test_scale_out_of_range_rejected(self):
        with pytest.raises(Exception):
            ProfileRule(match="X_", tag="TT", gravity="top",
                        scale=99.0, weight="normal")

    def test_safe_area_edge_max(self):
        with pytest.raises(Exception):
            SafeArea(top=0.6)  # >= 0.5 leaves no content room

    def test_empty_prefix_rejected(self):
        with pytest.raises(Exception):
            ProfileRule(match="", tag="TT", gravity="top",
                        scale=1.0, weight="normal")
