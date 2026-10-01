# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_soe_repulsion.py — TASK-ENG-03 / TASK-ENG-QA-01 (#258, #262).

Inter-layer spring repulsion: the pass that stops safe-zone-nudged text
layers from landing on top of each other.

The acceptance bar from #262 is "two vertically colliding text layers
maintain >= 16px clearance and zero overlap". That is necessary but not
sufficient, so this file also pins the three ways the pass could be
"correct" while still being wrong in a client deliverable:

  * Skyward Ejection (Pre-Mortem Scenario 1) — an unbounded upward
    repulsion chain walks the top layer off the canvas. Two-sided
    clamping must prevent it.
  * Moving what it must not move — a keyframed position (forbidden by
    the SOE pipeline contract) or one member of a rigid unit (shatters
    the placement invariant). Both must act as obstacles yet never move.
  * Fixing collisions that do not exist — layers whose x-spans never
    meet are not colliding, and shoving them apart destroys the
    designer's layout to solve nothing.

Synthetic masks/layers, same helper shape as test_occlusion_engine.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from core.occlusion_engine import (  # noqa: E402
    REPULSION_MARGIN_FLOOR_PX,
    REPULSION_MARGIN_PX,
    SOE_OVERFLOW_WARNING,
    OcclusionEngine,
    OcclusionMask,
)

COMP_W = 400
COMP_H = 800


def _mask(tmp_path: Path, go_box, w: int = COMP_W, h: int = COMP_H) -> Path:
    """CUTOFF everywhere, GO only inside `go_box` (l, t, r, b)."""
    img = np.zeros((h, w), dtype=np.uint8)
    l, t, r, b = go_box
    img[t:b, l:r] = 255
    out = tmp_path / "mask.png"
    cv2.imwrite(str(out), img)
    return out


def _layer(name: str, *, index: int, cy: float,
           height: float = 40.0, width: float = 200.0,
           cx: float = COMP_W / 2, tag: str = "TYPE",
           comp_id: Optional[int] = 1,
           keyed: bool = False) -> dict:
    """A conformed layer centred on (cx, cy) with the given box size.

    anchor is the rect centre so `position` IS the visual centre, which
    keeps the assertions below readable as geometry rather than as
    anchor arithmetic.
    """
    rect = [0.0, 0.0, width, height]
    anchor = [width / 2.0, height / 2.0, 0.0]
    pos = [float(cx), float(cy), 0.0]
    scale = [100.0, 100.0, 100.0]
    return {
        "index": index,
        "uid": f"u-{index}",
        "name": name,
        "layer_kind": "av",
        "content_tag": tag,
        "content_tag_source": "manual_comment",
        "containing_comp_id": comp_id,
        "position": list(pos),
        "scale": list(scale),
        "anchor": list(anchor),
        "source_rect": rect,
        "world_bounds": None,
        "hero_time": 5.0,
        "is_keyed": {"position": keyed, "scale": False, "anchor": False},
        "conformed_transforms": {
            "is_root": True,
            "position": list(pos),
            "scale": list(scale),
            "rotation": 0.0,
            "anchor": list(anchor),
        },
    }


def _run(mask_path: Path, layers: list, resolution=None):
    eng = OcclusionEngine(OcclusionMask(mask_path, COMP_W, COMP_H),
                          "test_preset", resolution=resolution)
    return eng.run({"status": "SAFE", "layers": layers, "warnings": {}})


def _box(layer: dict):
    """(top, bottom) of a layer in the ENGINE OUTPUT dict."""
    pos = layer["conformed_transforms"]["position"]
    anchor = layer["conformed_transforms"]["anchor"]
    rect = layer["source_rect"]
    top = pos[1] - anchor[1] + rect[1]
    return top, top + rect[3]


def _gap(upper: dict, lower: dict) -> float:
    return _box(lower)[0] - _box(upper)[1]


def _out_layers(out: dict):
    return {l["name"]: l for l in out["layers"]}


# ── the #262 acceptance criterion ──────────────────────────────────

class TestTwoCollidingLayers:
    def test_overlapping_layers_end_up_at_least_16px_apart(self, tmp_path):
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        # Deliberately overlapping: 40px tall boxes only 10px apart.
        layers = [_layer("Title", index=1, cy=400.0),
                  _layer("Legal", index=2, cy=410.0)]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        gap = _gap(got["Title"], got["Legal"])
        assert gap >= REPULSION_MARGIN_PX - 0.5, f"gap was {gap}"

    def test_zero_overlap_after_separation(self, tmp_path):
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("A", index=1, cy=400.0),
                  _layer("B", index=2, cy=405.0)]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        assert _gap(got["A"], got["B"]) > 0

    def test_relative_order_is_preserved(self, tmp_path):
        """The upper layer must stay upper. Swapping them would be a
        'separation' that reorders the designer's reading order."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("Upper", index=1, cy=400.0),
                  _layer("Lower", index=2, cy=408.0)]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        assert _box(got["Upper"])[0] < _box(got["Lower"])[0]

    def test_already_separated_layers_are_left_alone(self, tmp_path):
        """No collision means no correction. A pass that 'tidies' spacing
        it was not asked to touch is destroying the layout."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("A", index=1, cy=300.0),
                  _layer("B", index=2, cy=500.0)]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        assert got["A"]["conformed_transforms"]["position"][1] == 300.0
        assert got["B"]["conformed_transforms"]["position"][1] == 500.0

    def test_symmetric_push_splits_the_correction(self, tmp_path):
        """Two equally movable layers each travel half the overlap, so the
        pair's centre of mass stays put."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("A", index=1, cy=400.0),
                  _layer("B", index=2, cy=410.0)]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        ay = got["A"]["conformed_transforms"]["position"][1]
        by = got["B"]["conformed_transforms"]["position"][1]
        assert ay < 400.0 and by > 410.0
        assert abs(((ay + by) / 2.0) - 405.0) < 1.0

    def test_title_yields_less_than_legal_disclaimer(self, tmp_path):
        """A headline is more important than legals, so the legal absorbs
        more of their shared correction while the pair remains clear."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [
            _layer("Headline", index=1, cy=400.0, tag="TOP"),
            _layer("Legal", index=2, cy=410.0, tag="LGL"),
        ]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        title_move = 400.0 - got["Headline"]["conformed_transforms"]["position"][1]
        legal_move = got["Legal"]["conformed_transforms"]["position"][1] - 410.0

        assert title_move > 0 and legal_move > 0
        assert legal_move == pytest.approx(title_move * 3.0)
        assert _gap(got["Headline"], got["Legal"]) >= REPULSION_MARGIN_PX - 0.5


# ── Pre-Mortem Scenario 1: Skyward Ejection ────────────────────────

class TestSkywardEjection:
    def test_a_stack_of_five_never_leaves_the_canvas(self, tmp_path):
        """The headline failure: legal pushes title 3, which pushes 2,
        which pushes 1 clean off the top edge."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer(f"L{i}", index=i, cy=650.0 + i * 4.0)
                  for i in range(1, 6)]
        out, _ = _run(m, layers)
        for layer in out["layers"]:
            top, bottom = _box(layer)
            assert top >= 0, f"{layer['name']} ejected above the canvas"
            assert bottom <= COMP_H, f"{layer['name']} pushed below the canvas"

    def test_stack_stays_inside_the_go_band(self, tmp_path):
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer(f"L{i}", index=i, cy=660.0 + i * 3.0)
                  for i in range(1, 6)]
        out, _ = _run(m, layers)
        for layer in out["layers"]:
            top, bottom = _box(layer)
            assert top >= 100 - 0.5, f"{layer['name']} above the GO ceiling"
            assert bottom <= 700 + 0.5, f"{layer['name']} below the GO floor"

    def test_five_stacked_layers_are_all_mutually_clear(self, tmp_path):
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer(f"L{i}", index=i, cy=400.0 + i * 5.0)
                  for i in range(1, 6)]
        out, _ = _run(m, layers)
        ordered = sorted(out["layers"], key=lambda l: _box(l)[0])
        for upper, lower in zip(ordered, ordered[1:]):
            assert _gap(upper, lower) >= REPULSION_MARGIN_FLOOR_PX - 0.5


# ── adaptive margin compression + overflow ─────────────────────────

class TestMarginCompressionAndOverflow:
    def test_margins_compress_rather_than_ejecting(self, tmp_path):
        """A band too tight for 16px margins must tighten the spacing, not
        push a layer out of the safe area."""
        # 4 x 40px layers = 160px of content; band is 200px, so 16px
        # margins (48px) do not fit but 4px margins (12px) do.
        m = _mask(tmp_path, (0, 300, COMP_W, 500))
        layers = [_layer(f"L{i}", index=i, cy=395.0 + i * 3.0)
                  for i in range(1, 5)]
        out, _ = _run(m, layers)
        for layer in out["layers"]:
            top, bottom = _box(layer)
            assert top >= 300 - 0.5 and bottom <= 500 + 0.5
        ordered = sorted(out["layers"], key=lambda l: _box(l)[0])
        for upper, lower in zip(ordered, ordered[1:]):
            assert _gap(upper, lower) >= REPULSION_MARGIN_FLOOR_PX - 0.5

    def test_impossible_stack_raises_the_overflow_warning(self, tmp_path):
        """Honest failure: when it genuinely cannot fit, say so rather than
        shipping a quietly-wrong layout."""
        # 6 x 40px = 240px of content into a 150px band — impossible.
        m = _mask(tmp_path, (0, 300, COMP_W, 450))
        layers = [_layer(f"L{i}", index=i, cy=370.0 + i * 2.0)
                  for i in range(1, 7)]
        out, corrections = _run(m, layers)
        assert SOE_OVERFLOW_WARNING in str(out["warnings"].get("soe_overflow"))
        assert any(c.strategy == "REPULSION_OVERFLOW" for c in corrections)

    def test_overflow_correction_says_it_needs_human_review(self, tmp_path):
        m = _mask(tmp_path, (0, 300, COMP_W, 450))
        layers = [_layer(f"L{i}", index=i, cy=370.0 + i * 2.0)
                  for i in range(1, 7)]
        _, corrections = _run(m, layers)
        overflow = [c for c in corrections if c.strategy == "REPULSION_OVERFLOW"]
        assert overflow
        assert any("human review" in c.notes for c in overflow)

    def test_no_overflow_warning_when_the_stack_fits(self, tmp_path):
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("A", index=1, cy=400.0),
                  _layer("B", index=2, cy=410.0)]
        out, _ = _run(m, layers)
        assert "soe_overflow" not in out["warnings"]


# ── what the pass must NOT move ────────────────────────────────────

class TestImmovableParticipants:
    def test_a_keyed_layer_is_never_repositioned(self, tmp_path):
        """SOE's pipeline contract forbids modifying keyed positions —
        rewriting one would silently break the layer's animation."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("Animated", index=1, cy=400.0, keyed=True),
                  _layer("Static", index=2, cy=408.0)]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        assert got["Animated"]["conformed_transforms"]["position"][1] == 400.0

    def test_a_keyed_layer_still_pushes_its_neighbour_away(self, tmp_path):
        """Immovable is not the same as invisible. If a keyed legal line
        did not act as an obstacle, titles would stack straight onto it —
        the exact bug this pass exists to fix."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("Animated", index=1, cy=400.0, keyed=True),
                  _layer("Static", index=2, cy=408.0)]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        assert got["Static"]["conformed_transforms"]["position"][1] > 408.0
        assert _gap(got["Animated"], got["Static"]) >= REPULSION_MARGIN_PX - 0.5

    def test_two_keyed_layers_are_both_left_alone(self, tmp_path):
        """Nothing legal to do — report it, do not invent a move."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("A", index=1, cy=400.0, keyed=True),
                  _layer("B", index=2, cy=405.0, keyed=True)]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        assert got["A"]["conformed_transforms"]["position"][1] == 400.0
        assert got["B"]["conformed_transforms"]["position"][1] == 405.0

    def test_structural_tags_do_not_participate(self, tmp_path):
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("Guide", index=1, cy=400.0, tag="GUIDE"),
                  _layer("Guide2", index=2, cy=405.0, tag="PROTECT")]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        assert got["Guide"]["conformed_transforms"]["position"][1] == 400.0
        assert got["Guide2"]["conformed_transforms"]["position"][1] == 405.0

    def test_non_translatable_tags_are_not_repelled(self, tmp_path):
        """BG/HERO are placed by the scale engine; SOE does not
        second-guess them, and must not start doing so here."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("Hero", index=1, cy=400.0, tag="HERO"),
                  _layer("Bg", index=2, cy=405.0, tag="BG")]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        assert got["Hero"]["conformed_transforms"]["position"][1] == 400.0
        assert got["Bg"]["conformed_transforms"]["position"][1] == 405.0


# ── collision geometry ─────────────────────────────────────────────

class TestCollisionGeometry:
    def test_horizontally_disjoint_layers_are_not_collisions(self, tmp_path):
        """A left caption and a right badge at the same height never touch.
        Separating them would 'fix' a collision that does not exist."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("Left", index=1, cy=400.0, cx=60.0, width=100.0),
                  _layer("Right", index=2, cy=404.0, cx=340.0, width=100.0)]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        assert got["Left"]["conformed_transforms"]["position"][1] == 400.0
        assert got["Right"]["conformed_transforms"]["position"][1] == 404.0

    def test_partially_overlapping_x_spans_do_collide(self, tmp_path):
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("A", index=1, cy=400.0, cx=180.0, width=200.0),
                  _layer("B", index=2, cy=406.0, cx=260.0, width=200.0)]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        assert _gap(got["A"], got["B"]) >= REPULSION_MARGIN_PX - 0.5

    def test_non_adjacent_layers_still_get_separated(self, tmp_path):
        """Regression: adjacent-pair-only checking is not enough.

        A_left and C_left overlap each other, but B_right sits between
        them in y and is horizontally disjoint from BOTH — so an
        adjacent-only sweep skips A-B and B-C, never compares A-C, and
        leaves them overlapping. Measured 20px of overlap on this exact
        arrangement before the sweep was widened to all pairs.
        """
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [
            _layer("A_left", index=1, cy=400.0, cx=100.0, width=150.0),
            _layer("B_right", index=2, cy=410.0, cx=320.0, width=150.0),
            _layer("C_left", index=3, cy=420.0, cx=100.0, width=150.0),
        ]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        gap = _gap(got["A_left"], got["C_left"])
        assert gap >= REPULSION_MARGIN_PX - 0.5, (
            f"A_left/C_left overlap by {-gap:.1f}px — the interleaved "
            f"right-aligned layer broke the adjacency chain"
        )

    def test_the_interleaved_layer_itself_is_not_disturbed(self, tmp_path):
        """B_right collides with nobody; separating A and C must not drag
        it along."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [
            _layer("A_left", index=1, cy=400.0, cx=100.0, width=150.0),
            _layer("B_right", index=2, cy=410.0, cx=320.0, width=150.0),
            _layer("C_left", index=3, cy=420.0, cx=100.0, width=150.0),
        ]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        assert got["B_right"]["conformed_transforms"]["position"][1] == 410.0

    def test_layers_in_different_comps_never_interact(self, tmp_path):
        """Comp-scoped grouping. `index` is not unique across a recursive
        manifest, so grouping on it would 'separate' layers living in
        entirely different precomps — the Slot 13 trap."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("InCompA", index=1, cy=400.0, comp_id=1),
                  _layer("InCompB", index=1, cy=404.0, comp_id=2)]
        out, _ = _run(m, layers)
        got = _out_layers(out)
        assert got["InCompA"]["conformed_transforms"]["position"][1] == 400.0
        assert got["InCompB"]["conformed_transforms"]["position"][1] == 404.0


# ── engine contract ────────────────────────────────────────────────

class TestEngineContract:
    def test_input_is_never_mutated(self, tmp_path):
        """SOE's pipeline contract: 'Must not mutate input.'"""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("A", index=1, cy=400.0),
                  _layer("B", index=2, cy=408.0)]
        source = {"status": "SAFE", "layers": layers, "warnings": {}}
        before = [list(l["conformed_transforms"]["position"]) for l in layers]
        eng = OcclusionEngine(OcclusionMask(m, COMP_W, COMP_H), "p")
        eng.run(source)
        after = [list(l["conformed_transforms"]["position"]) for l in layers]
        assert before == after

    def test_legacy_position_mirror_stays_in_sync(self, tmp_path):
        """`_set_position` mirrors into the top-level `position`; a reader
        of the legacy field must not see a stale pre-repulsion value."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("A", index=1, cy=400.0),
                  _layer("B", index=2, cy=408.0)]
        out, _ = _run(m, layers)
        for layer in out["layers"]:
            assert layer["position"] == layer["conformed_transforms"]["position"]

    def test_one_correction_row_per_moved_layer(self, tmp_path):
        """A layer moved by both the ladder and repulsion reports ONE row;
        two rows would read as two independent moves in the report."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("A", index=1, cy=400.0),
                  _layer("B", index=2, cy=408.0)]
        _, corrections = _run(m, layers)
        keys = [(c.layer_uid, c.layer_index) for c in corrections]
        assert len(keys) == len(set(keys)), f"duplicate correction rows: {keys}"

    def test_corrections_record_the_true_final_position(self, tmp_path):
        """The report must show where the layer actually ended up."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        layers = [_layer("A", index=1, cy=400.0),
                  _layer("B", index=2, cy=408.0)]
        out, corrections = _run(m, layers)
        got = _out_layers(out)
        for c in corrections:
            if c.strategy.startswith("REPULSION"):
                actual = got[c.layer_name]["conformed_transforms"]["position"]
                assert c.corrected_position == actual

    def test_single_layer_is_a_no_op(self, tmp_path):
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        out, _ = _run(m, [_layer("Only", index=1, cy=400.0)])
        assert out["layers"][0]["conformed_transforms"]["position"][1] == 400.0

    def test_mask_with_no_go_pixels_skips_repulsion_gracefully(self, tmp_path):
        """No safe band is knowable, so the pass declines rather than
        inventing one. SOE never blocks the pipeline."""
        img = np.zeros((COMP_H, COMP_W), dtype=np.uint8)
        p = tmp_path / "nogo.png"
        cv2.imwrite(str(p), img)
        layers = [_layer("A", index=1, cy=400.0),
                  _layer("B", index=2, cy=405.0)]
        out, _ = _run(p, layers)   # must not raise
        got = _out_layers(out)
        assert got["A"]["conformed_transforms"]["position"][1] == 400.0

    def test_repulsion_is_deterministic(self, tmp_path):
        """Same input, same output — a conform run twice must not drift."""
        m = _mask(tmp_path, (0, 100, COMP_W, 700))
        mk = lambda: [_layer(f"L{i}", index=i, cy=400.0 + i * 4.0)
                      for i in range(1, 5)]
        first, _ = _run(m, mk())
        second, _ = _run(m, mk())
        assert ([l["conformed_transforms"]["position"] for l in first["layers"]]
                == [l["conformed_transforms"]["position"] for l in second["layers"]])


# ── real client geometry, not synthetic boxes ──────────────────────

FIXTURE = (Path(__file__).parent / "fixtures" / "session_2026_07_02"
           / "87n_fresh_manifest.json")


@pytest.mark.skipif(not FIXTURE.is_file(), reason="87N fixture not present")
class TestAgainstRealClientManifest:
    """The 87N comp is the canonical 'real compositional complexity'
    fixture, and it happens to contain the exact shape this pass targets:
    FIVE layers tagged TOP, all in the same comp, all sitting at the
    IDENTICAL position — four of them position-keyed, one not.

    CLAUDE.md is explicit that synthetic SOE fixtures have masked a real
    bug for a year (the v5.2.5 no-op), so the contract assertions below
    run against real anchors, real source_rects and real keyed flags
    rather than the clean boxes the rest of this file uses.
    """

    @staticmethod
    def _real_top_layers():
        import json
        data = json.loads(FIXTURE.read_text())
        return [l for l in data["layers"]
                if (l.get("content_tag") or "").upper() in ("TOP", "TT", "TYPE")]

    @staticmethod
    def _pos(layer: dict):
        """These are SCRAPE-shaped layers — `conformed_transforms` only
        exists once the conform stage has run, or once SOE writes one.
        `_set_position` mirrors into both, so reading conformed-first and
        falling back to the legacy field covers moved and unmoved alike."""
        cf = layer.get("conformed_transforms") or {}
        return cf.get("position") or layer.get("position")

    def test_the_fixture_still_has_the_stacked_shape(self):
        """Guards the two tests below from silently going vacuous if the
        fixture is ever re-captured with different content."""
        tops = self._real_top_layers()
        assert len(tops) >= 2
        keyed = [l for l in tops if (l.get("is_keyed") or {}).get("position")]
        assert keyed, "fixture no longer contains keyed TOP layers"
        assert len(keyed) < len(tops), "fixture has no movable TOP layer left"

    def test_no_keyed_layer_is_ever_moved_on_real_geometry(self, tmp_path):
        """The SOE pipeline contract, asserted on real client data: SOE
        must not modify keyed (animated) layer positions. These five
        layers are stacked on the same pixel, so a pass that ignored the
        keyed flag would certainly move them."""
        tops = self._real_top_layers()
        m = _mask(tmp_path, (0, 200, COMP_W, 600))
        before = {l["index"]: list(l["position"]) for l in tops}
        out, _ = _run(m, [dict(l) for l in tops])
        for layer in out["layers"]:
            if not (layer.get("is_keyed") or {}).get("position"):
                continue
            got = self._pos(layer)
            assert got == before[layer["index"]], (
                f"keyed layer {layer['name']!r} was moved to {got}"
            )

    def test_real_stack_does_not_crash_and_stays_on_canvas(self, tmp_path):
        """Five full-frame (1920x1080) layers in a 400x800 band is a
        genuinely over-constrained solve — it must degrade to an overflow
        warning, not an exception or an off-canvas ejection."""
        tops = self._real_top_layers()
        m = _mask(tmp_path, (0, 200, COMP_W, 600))
        out, corrections = _run(m, [dict(l) for l in tops])
        assert out["layers"], "engine returned no layers"
        for layer in out["layers"]:
            pos = self._pos(layer)
            assert all(isinstance(v, (int, float)) for v in pos)
            assert not any(v != v for v in pos), "NaN leaked into a position"
