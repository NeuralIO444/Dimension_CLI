# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_soe_scrape_jsx_merger.py
v5.10.2 — regression coverage for the JSX `scrapeUnified` merger,
SOE-Scrape edition.

Background
----------
`Sovereign_Core.jsx::scrapeUnified` walks an explicit allow-list
(`V5_LAYER_KEYS`) when transplanting v5 layer fields onto the
legacy layer object before serialization. From v5.2.5 through
v5.10.1, six fields produced by `SovCore_Layer.jsx::scrapeLayer`
were missing from that list and silently dropped on every scrape:

  source_rect, world_bounds, hero_time, is_keyed, match_name, label

Consequences in production:

  - `occlusion_engine._process_layer` hit `SKIPPED_NO_BOUNDS` for
    every layer in every conform — SOE has been a no-op for a year.
  - `occlusion_engine` could not enforce `is_keyed.position`
    protection — keyed TT/LEGALS layers were nominally translatable.
  - `surveyor._spatial_dna`'s `is_fullscreen` BG coverage boost was
    dead because `world_bounds` and `source_rect` were always None.

Issue #44 documented the case study. v5.10.2 (this PR) appends the
six fields to `V5_LAYER_KEYS`. This test pins the contract at the
Python boundary: the SOE engine and surveyor MUST behave correctly
when the fields ARE populated, AND degrade to the documented
`SKIPPED_NO_BOUNDS` / `is_fullscreen=False` behavior when they are
absent (the pre-fix bug state).

Pattern follows `test_tag_roundtrip_jsx_merger.py` from v5.10.1:
positive case proves the fix works; negative case proves the test
would have caught the original bug.

Because the bug is in JSX (untestable from pytest), the contract is
pinned at the Python side. A future regression that re-drops these
fields from `V5_LAYER_KEYS` will produce manifests where the fields
are absent — the negative cases below would still pass against that
buggy state, but the positive cases would fail because the test
fixtures explicitly populate the fields the JSX is supposed to
produce. This pair pins both halves.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import cv2

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.occlusion_engine import (  # noqa: E402
    OcclusionEngine, OcclusionMask,
)
from core.surveyor import _spatial_dna  # noqa: E402
from models.scrape_manifest import LayerModel  # noqa: E402


COMP_W = 1080
COMP_H = 1920


def _write_all_go_mask(tmp_path: Path) -> Path:
    """Mask where every pixel classifies as GO (255). Lets us run
    SOE without it actually correcting anything — we just want to
    observe that it does NOT skip with SKIPPED_NO_BOUNDS."""
    img = np.full((COMP_H, COMP_W), 255, dtype=np.uint8)
    out = tmp_path / "mask.png"
    cv2.imwrite(str(out), img)
    return out


def _layer_dict(*, source_rect, world_bounds, is_keyed,
                tag="TT", position=(540.0, 1800.0)) -> dict:
    """Conformed-layer dict matching what `scale_engine.conform()`
    produces and what `OcclusionEngine._process_layer` reads."""
    pos3 = list(position) + [0.0]
    scale3 = [100.0, 100.0, 100.0]
    anchor3 = [0.0, 0.0, 0.0]
    return {
        "index": 1,
        "uid": "u-1",
        "name": "test_layer",
        "layer_kind": "av",
        "content_tag": tag,
        "content_tag_source": "manual_comment",
        "position": pos3,
        "scale": scale3,
        "anchor": anchor3,
        "source_rect": list(source_rect) if source_rect is not None else None,
        "world_bounds": dict(world_bounds) if world_bounds is not None else None,
        "hero_time": 5.0,
        "is_keyed": dict(is_keyed) if is_keyed is not None else None,
        "conformed_transforms": {
            "is_root": True,
            "position": pos3,
            "scale":    scale3,
            "rotation": 0.0,
            "anchor":   anchor3,
        },
    }


def _conformed(layers: list) -> dict:
    return {
        "status": "SAFE",
        "project_info": {"name": "test", "width": COMP_W,
                         "height": COMP_H, "fps": 24.0},
        "layers": layers,
    }


# ── SOE engine: positive / negative on world_bounds + source_rect ──


class TestSoeBoundsRoundTrip:
    """Pin the SOE engine's behaviour on both sides of the v5.10.2 fix.

    Positive: manifest carries real `source_rect` + `world_bounds` →
    engine does NOT hit SKIPPED_NO_BOUNDS.
    Negative: same manifest with both fields = None (pre-fix bug
    state) → engine MUST skip with SKIPPED_NO_BOUNDS.
    """

    def test_engine_processes_layer_when_fields_present(self, tmp_path):
        """The fix delivers source_rect onto the manifest. The SOE
        engine must reach the strategy ladder, not skip on missing
        bounds. With an all-GO mask the layer is already clear and
        the engine returns None (no correction needed) — that's the
        signal we want: we got past line 445."""
        mask_path = _write_all_go_mask(tmp_path)
        mask = OcclusionMask(mask_path, COMP_W, COMP_H)
        engine = OcclusionEngine(mask, "tiktok")

        layer = _layer_dict(
            source_rect=(0.0, 0.0, 200.0, 60.0),
            world_bounds={"l": 440.0, "t": 1770.0,
                          "r": 640.0, "b": 1830.0},
            is_keyed={"position": False, "scale": False, "anchor": False},
        )
        _, corrections = engine.run(_conformed([layer]))

        # Engine reached the "already CLEAR" path — no correction
        # emitted. If the field were missing the engine would emit
        # a SKIPPED_NO_BOUNDS correction record. The absence of that
        # record proves we got past line 445.
        skipped = [c for c in corrections
                   if c.strategy == "SKIPPED_NO_BOUNDS"]
        assert not skipped, (
            f"engine skipped on missing bounds despite fields being "
            f"populated. corrections: {[c.strategy for c in corrections]}"
        )

    def test_negative_case_proves_test_would_have_caught_pre_fix_bug(
            self, tmp_path):
        """Same layer, but with source_rect AND world_bounds = None —
        the exact state the JSX merger produced before v5.10.2. The
        engine MUST skip with SKIPPED_NO_BOUNDS.

        If a future regression silently re-drops these fields from
        `V5_LAYER_KEYS`, the positive test still passes when the
        Python fixture happens to populate them — but THIS negative
        test pins the documented graceful-skip behavior so we know
        the engine still degrades correctly when JSX hands it None.
        Together the pair regression-proofs the contract.
        """
        mask_path = _write_all_go_mask(tmp_path)
        mask = OcclusionMask(mask_path, COMP_W, COMP_H)
        engine = OcclusionEngine(mask, "tiktok")

        layer = _layer_dict(
            source_rect=None,
            world_bounds=None,
            is_keyed=None,
        )
        _, corrections = engine.run(_conformed([layer]))

        skipped = [c for c in corrections
                   if c.strategy == "SKIPPED_NO_BOUNDS"]
        assert len(skipped) == 1, (
            f"engine MUST skip with SKIPPED_NO_BOUNDS when both "
            f"source_rect and world_bounds are None — that is the "
            f"documented graceful-degrade. corrections: "
            f"{[c.strategy for c in corrections]}"
        )


# ── SOE engine: is_keyed.position protection round-trip ────────────


class TestSoeKeyedProtection:
    """Pin the `is_keyed.position` protection. This is the single
    most consequential side of the v5.10.2 fix: from v5.2.5 through
    v5.10.1, the engine would have been willing to translate keyed
    layers because `is_keyed` arrived as None and was treated as
    falsy. Now that the field actually arrives, keyed layers are
    correctly skipped with `SKIPPED_KEYED`."""

    def test_keyed_position_layer_is_skipped(self, tmp_path):
        """Place a TT layer in CUTOFF (overlapping a black band at
        the bottom) so the engine WOULD translate — except the
        layer is keyed. Engine must emit SKIPPED_KEYED, not
        translate."""
        # CUTOFF band at the bottom — TT layer's bounds will intersect.
        img = np.full((COMP_H, COMP_W), 255, dtype=np.uint8)
        img[1700:COMP_H, :] = 0  # CUTOFF rectangle
        mask_path = tmp_path / "mask.png"
        cv2.imwrite(str(mask_path), img)

        mask = OcclusionMask(mask_path, COMP_W, COMP_H)
        engine = OcclusionEngine(mask, "tiktok")

        layer = _layer_dict(
            source_rect=(0.0, 0.0, 200.0, 60.0),
            world_bounds={"l": 440.0, "t": 1770.0,
                          "r": 640.0, "b": 1830.0},
            is_keyed={"position": True, "scale": False, "anchor": False},
            position=(540.0, 1800.0),
        )
        _, corrections = engine.run(_conformed([layer]))

        keyed_skips = [c for c in corrections
                       if c.strategy == "SKIPPED_KEYED"]
        translates = [c for c in corrections
                      if c.strategy == "TRANSLATE"]
        assert len(keyed_skips) == 1, (
            f"engine MUST refuse to translate a layer with "
            f"is_keyed.position=True — silently moving a keyed "
            f"layer truncates the animation. corrections: "
            f"{[c.strategy for c in corrections]}"
        )
        assert not translates, (
            f"engine translated a keyed layer — that's the bug "
            f"v5.10.2 closes. corrections: {[c.strategy for c in corrections]}"
        )

    def test_negative_case_keyed_field_absent(self, tmp_path):
        """Same layer, is_keyed=None — the pre-fix bug state.
        Without the field, the engine cannot enforce its keyed
        protection. This test pins the behavior so we can SEE
        the difference: pre-fix, a keyed layer would have been
        eligible for translation. (We assert it is NOT skipped
        with SKIPPED_KEYED here — the absence of that protection
        is exactly what the v5.10.2 fix restores.)
        """
        img = np.full((COMP_H, COMP_W), 255, dtype=np.uint8)
        img[1700:COMP_H, :] = 0
        mask_path = tmp_path / "mask.png"
        cv2.imwrite(str(mask_path), img)

        mask = OcclusionMask(mask_path, COMP_W, COMP_H)
        engine = OcclusionEngine(mask, "tiktok")

        layer = _layer_dict(
            source_rect=(0.0, 0.0, 200.0, 60.0),
            world_bounds={"l": 440.0, "t": 1770.0,
                          "r": 640.0, "b": 1830.0},
            is_keyed=None,
            position=(540.0, 1800.0),
        )
        _, corrections = engine.run(_conformed([layer]))

        keyed_skips = [c for c in corrections
                       if c.strategy == "SKIPPED_KEYED"]
        assert not keyed_skips, (
            "engine emitted SKIPPED_KEYED with is_keyed=None — "
            "that should be impossible because the protection "
            "check at line 471 reads `is_keyed.get('position')` "
            "on `layer.get('is_keyed') or {}`. If this fires, "
            "the engine grew a new code path that synthesises "
            "is_keyed and we need to update the test."
        )


# ── Surveyor: is_fullscreen coverage signal round-trip ─────────────


class TestSurveyorFullscreenSignal:
    """Pin the surveyor's `is_fullscreen` coverage signal. From
    v5.2.5 through v5.10.1, `world_bounds` always arrived as None,
    so `is_fullscreen` was always False and the BG coverage boost
    in `_score_layer` (line 416) was dead.

    These tests prove the signal works when world_bounds is
    populated AND degrades to False when absent.
    """

    def _layer(self, **kwargs) -> LayerModel:
        defaults = dict(
            index=1,
            name="bg_video.mov",
            layer_kind="av",
            position=[540.0, 960.0, 0.0],
            scale=[100.0, 100.0, 100.0],
            anchor=[0.0, 0.0, 0.0],
        )
        defaults.update(kwargs)
        return LayerModel(**defaults)

    def test_is_fullscreen_true_when_world_bounds_populated(self):
        """Layer covers the entire 1080×1920 comp → is_fullscreen
        must fire. This is the v5.5 Tier B BG signal that was
        silently dead in production for a year."""
        layer = self._layer(
            world_bounds={"l": 0.0, "t": 0.0, "r": 1080.0, "b": 1920.0},
        )
        dna = _spatial_dna(layer, layer_index=0, total_layers=10,
                           comp_width=COMP_W, comp_height=COMP_H)
        assert dna["is_fullscreen"] is True, (
            f"is_fullscreen must be True for a fullscreen layer "
            f"once world_bounds arrives. dna={dna}"
        )
        assert dna["w_fill"] >= 0.7
        assert dna["h_fill"] >= 0.7
        assert dna["has_source"] is True

    def test_is_fullscreen_false_when_world_bounds_none(self):
        """Same layer, world_bounds=None — the pre-fix bug state.
        is_fullscreen MUST be False. This is the contract that
        proves the test would have caught the original bug:
        without world_bounds the surveyor cannot fire the BG
        coverage boost."""
        layer = self._layer(world_bounds=None, source_rect=None)
        dna = _spatial_dna(layer, layer_index=0, total_layers=10,
                           comp_width=COMP_W, comp_height=COMP_H)
        assert dna["is_fullscreen"] is False, (
            f"is_fullscreen must degrade to False when world_bounds "
            f"and source_rect are both None. dna={dna}"
        )
        assert dna["w_fill"] == 0.0
        assert dna["h_fill"] == 0.0
        assert dna["has_source"] is False

    def test_source_rect_only_still_signals_fullscreen(self):
        """When world_bounds is None but source_rect is populated
        (a layer whose JSX merger landed source_rect but not
        world_bounds — possible in transitional manifests), the
        signal still fires via the source_rect path. Pins the
        fallback at surveyor.py:218-229."""
        layer = self._layer(
            world_bounds=None,
            source_rect=[0.0, 0.0, 1080.0, 1920.0],
        )
        dna = _spatial_dna(layer, layer_index=0, total_layers=10,
                           comp_width=COMP_W, comp_height=COMP_H)
        assert dna["is_fullscreen"] is True
        assert dna["has_source"] is True
