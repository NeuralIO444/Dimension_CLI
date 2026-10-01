"""
Bug I — Camera scrape determinism contract test.

`Sovereign_Core.jsx::scrape` previously read camera transforms via
`prop.value` (playhead-time evaluation). The fix anchors at first
keyframe time when keyed, else t=0, with `preExpression=true`. The
contract: two scrapes of the same comp at different playhead
positions must produce byte-identical camera fields.

Real-data fixtures only — synthetic dicts bypass the JSX serializer
where the bug actually lived. See `fixtures/bug_i/README.md` for
the capture procedure.

Test activates when both fixtures are present:
    fixtures/bug_i/87n_playhead_at_zero.json
    fixtures/bug_i/87n_playhead_at_162.json

Skips with a fixture-missing message until Matt captures the pair
during the PR-W deferral window. Same Phase-3-deferred pattern as
Bug L's manual-reference comp.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
))

from models.scrape_manifest import ScrapeManifest


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "bug_i"
FIXTURE_AT_ZERO = FIXTURE_DIR / "87n_playhead_at_zero.json"
FIXTURE_AT_162 = FIXTURE_DIR / "87n_playhead_at_162.json"


def _have_fixtures() -> bool:
    return FIXTURE_AT_ZERO.exists() and FIXTURE_AT_162.exists()


_SKIP_REASON = (
    "Bug I fixtures not yet captured. See "
    "python/tests/fixtures/bug_i/README.md for the capture procedure. "
    "Deferred to Phase 3 (post-PR-W merge) — same window as Bug L's "
    "live-AE deferral."
)


def _load(path: Path) -> ScrapeManifest:
    with open(path) as f:
        return ScrapeManifest.model_validate(json.load(f))


def _camera(manifest: ScrapeManifest):
    cams = [l for l in manifest.layers if l.layer_kind == "camera"]
    assert len(cams) == 1, (
        f"Expected exactly 1 camera in 87N, got {len(cams)} from {manifest}"
    )
    return cams[0]


@pytest.mark.skipif(not _have_fixtures(), reason=_SKIP_REASON)
class TestBugIRestPoseDeterminism:
    """Two scrapes at different playheads must produce identical camera fields."""

    @classmethod
    def setup_class(cls):
        cls.m_zero = _load(FIXTURE_AT_ZERO)
        cls.m_162 = _load(FIXTURE_AT_162)
        cls.cam_zero = _camera(cls.m_zero)
        cls.cam_162 = _camera(cls.m_162)

    def test_camera_position_identical_across_playheads(self):
        assert self.cam_zero.position == self.cam_162.position, (
            f"BUG_I_REGRESSION position drift: "
            f"frame 0 = {self.cam_zero.position}, "
            f"frame 162 = {self.cam_162.position}. "
            f"Camera scrape is reading at playhead instead of rest pose."
        )

    def test_camera_zoom_identical_across_playheads(self):
        z_zero = self.cam_zero.camera.zoom if self.cam_zero.camera else None
        z_162 = self.cam_162.camera.zoom if self.cam_162.camera else None
        assert z_zero == z_162, (
            f"BUG_I_REGRESSION zoom drift: "
            f"frame 0 = {z_zero}, frame 162 = {z_162}."
        )

    def test_camera_focus_distance_identical_across_playheads(self):
        f_zero = self.cam_zero.camera.focusDistance if self.cam_zero.camera else None
        f_162 = self.cam_162.camera.focusDistance if self.cam_162.camera else None
        assert f_zero == f_162, (
            f"BUG_I_REGRESSION focusDistance drift: "
            f"frame 0 = {f_zero}, frame 162 = {f_162}."
        )

    def test_camera_poi_identical_across_playheads(self):
        p_zero = self.cam_zero.camera.pointOfInterest if self.cam_zero.camera else None
        p_162 = self.cam_162.camera.pointOfInterest if self.cam_162.camera else None
        assert p_zero == p_162, (
            f"BUG_I_REGRESSION pointOfInterest drift: "
            f"frame 0 = {p_zero}, frame 162 = {p_162}."
        )

    def test_static_position_matches_first_keyframe_when_keyed(self):
        """Per Lock 1: when the camera has keyframed position, the static
        `position` field must equal the value at the first keyframe time
        (`prop.keyTime(1)`). For separated dimensions, that's the first
        value of each per-axis stream."""
        td = self.cam_zero.temporal_data
        if td is None:
            pytest.skip("camera has no temporal_data — unkeyed case unverifiable here")

        td_dict = td.model_dump(exclude_none=True) if hasattr(td, "model_dump") else dict(td)
        is_separated = any(
            k in td_dict for k in ("position_x", "position_y", "position_z")
        )

        if is_separated:
            for axis_idx, axis_key in enumerate(("position_x", "position_y", "position_z")):
                stream = td_dict.get(axis_key)
                if not stream or not stream.get("values"):
                    continue
                first_key_value = stream["values"][0]
                static_axis = self.cam_zero.position[axis_idx]
                assert static_axis == pytest.approx(first_key_value, rel=1e-9), (
                    f"BUG_I_REGRESSION {axis_key}: static[{axis_idx}] = "
                    f"{static_axis}, first keyframe value = {first_key_value}. "
                    f"Static is not anchored at first keyframe."
                )
        else:
            unified = td_dict.get("position")
            if not unified or not unified.get("values"):
                pytest.skip("unified position has no keys — nothing to verify")
            first_key_value = unified["values"][0]
            assert self.cam_zero.position == pytest.approx(first_key_value, rel=1e-9), (
                f"BUG_I_REGRESSION unified position: static = "
                f"{self.cam_zero.position}, first keyframe = {first_key_value}."
            )
