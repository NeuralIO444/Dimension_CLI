# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_verify_safe_zone_compliance.py — tests for the manifest-level
safe-zone verifier (partial #420).

Built on a REAL conform of the 87N fixture through the real ScaleEngine and
the real shipped TikTok mask — not synthetic layer dicts. CLAUDE.md is
explicit that synthetic fixtures have masked contract bugs in this repo for
a year at a time, and this tool's entire job is to re-derive geometry, so
testing it against hand-made geometry would test nothing.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.occlusion.mask_solver import OcclusionMask  # noqa: E402
from core.scale_engine import ScaleEngine  # noqa: E402
from models.scrape_manifest import ScrapeManifest  # noqa: E402
from tools import verify_safe_zone_compliance as vz  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "python/tests/fixtures/bug_l/87n_source_manifest.json"
MASK = REPO_ROOT / "config/safe_zones/tiktok.png"

TARGET_W, TARGET_H = 1080, 1920


@pytest.fixture(scope="module")
def conformed():
    src = json.loads(FIXTURE.read_text(encoding="utf-8"))
    manifest = ScrapeManifest.model_validate(src)
    engine = ScaleEngine(manifest, TARGET_W, TARGET_H, "fit", 0.0)
    return engine.conform()


@pytest.fixture(scope="module")
def mask():
    return OcclusionMask(MASK, TARGET_W, TARGET_H)


def _records(conformed, *, zone="CLEAR", strategy="TRANSLATE"):
    return [
        {
            "layer_index": lyr["index"],
            "layer_uid": lyr.get("uid"),
            "layer_name": lyr.get("name"),
            "content_tag": lyr.get("content_tag"),
            "original_position": [0, 0],
            "corrected_position": [0, 0],
            "zone_hit": zone,
            "strategy": strategy,
            "move_distance_px": 0.0,
        }
        for lyr in conformed["layers"]
    ]


class TestReDerivesRatherThanTrusting:
    def test_detects_a_false_clear_claim(self, conformed, mask):
        """The load-bearing behaviour.

        Every record claims CLEAR. If the tool merely echoed the recorded
        zone it would report full compliance. It must instead re-derive each
        layer's final bounds and disagree where reality is worse.
        """
        report = vz.verify(conformed, _records(conformed, zone="CLEAR"), mask)
        assert not report["compliant"]
        assert report["disagreements"], "verifier trusted the record instead of re-deriving"
        for d in report["disagreements"]:
            assert d["planned_zone"] == "CLEAR"
            assert d["actual_zone"] in ("CUTOFF", "NUDGE")

    def test_reality_better_than_plan_is_not_a_failure(self, conformed, mask):
        """A layer recorded as CUTOFF that actually lands CLEAR is a bonus,
        not a defect. Only worse-than-planned counts."""
        report = vz.verify(conformed, _records(conformed, zone="CUTOFF"), mask)
        assert report["compliant"], report["disagreements"]

    def test_actually_checks_a_meaningful_number_of_layers(self, conformed, mask):
        report = vz.verify(conformed, _records(conformed), mask)
        assert report["layers_checked"] >= 10, (
            "too few layers re-checked — bounds derivation is probably failing "
            "silently and the pass result would be meaningless"
        )


class TestSkipSemantics:
    @pytest.mark.parametrize("strategy", [
        "SKIPPED_KEYED", "SKIPPED_NO_BOUNDS", "SKIPPED_STRUCTURAL",
        "SKIPPED_UNIT_KEYED", "SOE_FAILED",
    ])
    def test_soe_declined_layers_are_never_reported_as_violations(
        self, conformed, mask, strategy
    ):
        """SOE promised nothing for these, so it cannot have broken a promise.
        Reporting them would be blaming the engine for a placement it
        explicitly declined to make."""
        report = vz.verify(conformed, _records(conformed, zone="CLEAR", strategy=strategy), mask)
        assert report["compliant"]
        assert len(report["layers_skipped"]) >= 1

    def test_layers_with_no_soe_record_are_ignored_entirely(self, conformed, mask):
        report = vz.verify(conformed, [], mask)
        assert report["compliant"]
        assert report["layers_checked"] == 0


class TestReadsTheCorrectView:
    def test_uses_conformed_transforms_not_source_carryover(self, conformed, mask):
        """CLAUDE.md's 'parallel views' sharp edge: a conformed manifest keeps
        source values beside conformed ones, and reading the wrong view has
        already produced two false-alarm bug reports in this repo.

        Corrupting only `conformed_transforms.position` must change the
        result. If it doesn't, the tool is reading the source view.
        """
        import copy
        mutated = copy.deepcopy(conformed)
        # Drive every layer into TikTok's bottom caption bar (the mask's
        # cutoff band sits in the lowest ~22% of a 1920px frame). NOT
        # off-canvas: classify_aabb clamps out-of-bounds boxes and returns
        # GO, so an off-canvas mutation would change nothing and the test
        # would pass for the wrong reason.
        for lyr in mutated["layers"]:
            ct = lyr.get("conformed_transforms") or {}
            if ct.get("position"):
                ct["position"] = [TARGET_W / 2.0, TARGET_H - 60.0]

        clean = vz.verify(conformed, _records(conformed, zone="CLEAR"), mask)
        dirty = vz.verify(mutated, _records(mutated, zone="CLEAR"), mask)

        assert len(dirty["disagreements"]) > len(clean["disagreements"]), (
            "shoving every layer into the caption bar produced no additional "
            "violations — the tool is not reading conformed_transforms"
        )


class TestTargetDimensionResolution:
    def test_target_specific_keys_win_over_project_info(self):
        """project_info can legitimately hold the SOURCE dims on a conformed
        manifest, so it must be the last resort, never the first."""
        doc = {
            "target_width": 1080, "target_height": 1920,
            "project_info": {"width": 1920, "height": 1080},
        }
        assert vz._resolve_target_dims(doc) == (1080, 1920)

    def test_falls_back_to_project_info_when_nothing_better(self):
        assert vz._resolve_target_dims(
            {"project_info": {"width": 1920, "height": 1080}}
        ) == (1920, 1080)

    def test_returns_zeros_when_undeterminable(self):
        assert vz._resolve_target_dims({}) == (0, 0)


class TestCli:
    def test_exit_code_signals_compliance(self, tmp_path, conformed, capsys):
        cpath = tmp_path / "conf.json"
        rpath = tmp_path / "corr.json"
        cpath.write_text(json.dumps(conformed), encoding="utf-8")
        rpath.write_text(
            json.dumps({"corrections": _records(conformed, zone="CUTOFF")}),
            encoding="utf-8",
        )
        rc = vz.main([
            "--conformed", str(cpath), "--corrections", str(rpath),
            "--mask", str(MASK),
            "--comp-width", str(TARGET_W), "--comp-height", str(TARGET_H),
        ])
        assert rc == 0
        assert "PASS" in capsys.readouterr().out

    def test_missing_dimensions_errors_clearly_rather_than_crashing(
        self, tmp_path, capsys
    ):
        cpath = tmp_path / "conf.json"
        rpath = tmp_path / "corr.json"
        cpath.write_text(json.dumps({"layers": []}), encoding="utf-8")
        rpath.write_text(json.dumps([]), encoding="utf-8")
        rc = vz.main([
            "--conformed", str(cpath), "--corrections", str(rpath),
            "--mask", str(MASK),
        ])
        assert rc == 2
        assert "could not determine target dimensions" in capsys.readouterr().err
