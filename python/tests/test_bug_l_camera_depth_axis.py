"""
Bug L — Camera depth-axis conform math contract test.

Real-data contract test: loads a saved 87N source manifest and
asserts ScaleEngine.conform produces camera depth-axis values
matching the K formula.

    K = max(target_W / source_W, target_H / source_H)

Locked tolerances per Matt 2026-05-08:
    - zoom × K within 0.5%
    - position[2] × K within 0.5%
    - camera.focusDistance × K within 0.5%
    - camera.pointOfInterest[2] × K within 0.5% (3D camera scene)
    - camera.pointOfInterest[0/1] follow S formula (unchanged from C2)

Manual reference deviations on Z (33%) and focus (100%) are
deferred to Bug N (HERO framing fraction). NOT asserted here.

Synthetic-fixture anti-pattern guard: this test loads a real
saved 87N scrape manifest. Do NOT replace the fixture with an
in-memory dict — the bug class hid for weeks behind synthetic
fixtures that bypassed the actual conform path.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
))

from core.scale_engine import ScaleEngine
from models.scrape_manifest import ScrapeManifest


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "bug_l" / "87n_source_manifest.json"
)
TOLERANCE = 0.005  # 0.5%

# 87N's expected target conform: HD (1920×1080) → TIKTOK (1080×1920)
TARGET_W = 1080
TARGET_H = 1920


def _load_manifest() -> ScrapeManifest:
    with open(FIXTURE_PATH) as f:
        return ScrapeManifest.model_validate(json.load(f))


def _conform(manifest: ScrapeManifest) -> dict:
    engine = ScaleEngine(
        manifest=manifest,
        target_width=TARGET_W,
        target_height=TARGET_H,
        scale_mode="Fit",
        bleed_pct=0.05,
    )
    return engine.conform()


def _camera_layer(result: dict) -> dict:
    cams = [l for l in result["layers"] if l.get("layer_kind") == "camera"]
    assert len(cams) == 1, f"Expected exactly 1 camera in 87N, got {len(cams)}"
    return cams[0]


def _within_tol(actual: float, expected: float, label: str, tol: float = TOLERANCE) -> None:
    if expected == 0:
        assert actual == 0, (
            f"CAMERA_DEPTH_AXIS_MISMATCH {label}: expected 0, got {actual}"
        )
        return
    rel = abs(actual - expected) / abs(expected)
    assert rel <= tol, (
        f"CAMERA_DEPTH_AXIS_MISMATCH {label}: "
        f"expected {expected:.6f}, got {actual:.6f}, "
        f"relative error {rel * 100:.3f}% (tolerance {tol * 100:.1f}%)"
    )


class TestBugLCameraDepthAxis:
    """Bug L — assert K formula on camera depth-axis fields."""

    @classmethod
    def setup_class(cls):
        cls.manifest = _load_manifest()
        cls.src_cam = next(l for l in cls.manifest.layers if l.layer_kind == "camera")
        cls.result = _conform(cls.manifest)
        cls.conformed = _camera_layer(cls.result)
        cls.tf = cls.conformed["conformed_transforms"]
        src_w = cls.manifest.project_info.width
        src_h = cls.manifest.project_info.height
        cls.K = max(TARGET_W / src_w, TARGET_H / src_h)
        cls.S = cls.result["scale"]["S"]

    def test_K_value_for_HD_to_TIKTOK(self):
        """Sanity: K = 1.778 for HD (1920×1080) → TIKTOK (1080×1920)."""
        assert abs(self.K - 1920 / 1080) < 1e-9
        # K must materially diverge from S — that's the whole point of Bug L.
        assert abs(self.K - self.S) / self.S > 0.5

    def test_zoom_scales_by_K(self):
        expected = self.src_cam.camera.zoom * self.K
        _within_tol(self.tf["camera"]["zoom"], expected, "zoom")

    def test_position_z_scales_by_K(self):
        expected = self.src_cam.position[2] * self.K
        _within_tol(self.tf["position"][2], expected, "position[2]")

    def test_focus_distance_scales_by_K(self):
        expected = self.src_cam.camera.focusDistance * self.K
        _within_tol(self.tf["camera"]["focusDistance"], expected, "focusDistance")

    def test_poi_z_scales_by_K(self):
        expected = self.src_cam.camera.pointOfInterest[2] * self.K
        _within_tol(self.tf["camera"]["pointOfInterest"][2], expected, "pointOfInterest[2]")

    def test_poi_xy_uses_S_formula_not_K(self):
        """POI X/Y are world-space remaps (S formula) — NOT depth-axis (K).
        Verifies the C2 distinction between camera lateral and depth fields.
        Catches accidental K leak into POI X/Y in future edits."""
        src_w = self.manifest.project_info.width
        src_h = self.manifest.project_info.height
        src_poi = self.src_cam.camera.pointOfInterest
        expected_x = ((src_poi[0] - src_w / 2.0) * self.S) + (TARGET_W / 2.0)
        expected_y = ((src_poi[1] - src_h / 2.0) * self.S) + (TARGET_H / 2.0)
        actual = self.tf["camera"]["pointOfInterest"]
        _within_tol(actual[0], expected_x, "pointOfInterest[0] (S formula)")
        _within_tol(actual[1], expected_y, "pointOfInterest[1] (S formula)")

    def test_zoom_does_not_equal_source_times_S(self):
        """Regression net: pre-Bug-L code used zoom × S. A future revert
        would re-introduce that. Assert zoom is NOT close to source × S."""
        wrong = self.src_cam.camera.zoom * self.S
        actual = self.tf["camera"]["zoom"]
        rel = abs(actual - wrong) / abs(wrong)
        assert rel > 0.1, (
            f"REGRESSION ALERT: zoom = {actual:.2f} resembles source × S "
            f"({wrong:.2f}). Expected source × K ({self.src_cam.camera.zoom * self.K:.2f}). "
            f"Bug L regression — verify scale_engine._conform_camera uses K."
        )

    def test_position_z_does_not_equal_source_times_S(self):
        """Companion regression net for position Z."""
        wrong = self.src_cam.position[2] * self.S
        actual = self.tf["position"][2]
        rel = abs(actual - wrong) / abs(wrong)
        assert rel > 0.1, (
            f"REGRESSION ALERT: position[2] = {actual:.2f} resembles source × S "
            f"({wrong:.2f}). Expected source × K ({self.src_cam.position[2] * self.K:.2f}). "
            f"Bug L regression — verify scale_engine z_scale gating uses K for cameras."
        )

    def test_av_layer_z_still_uses_S(self):
        """Negative regression: AV layers in 3D camera scenes must still
        scale Z by S, NOT K. Bug L is camera-only; AV behavior unchanged."""
        # Find an AV layer with non-zero source Z (the TT 'Outlines' siblings
        # in 87N are at Z=800/721/407/-1764/470).
        av_with_z = next(
            l for l in self.manifest.layers
            if l.layer_kind == "av"
            and l.position
            and len(l.position) > 2
            and l.position[2] != 0
        )
        conformed_av = next(
            l for l in self.result["layers"]
            if l.get("index") == av_with_z.index
        )
        out_z = conformed_av["conformed_transforms"]["position"][2]
        expected_via_S = av_with_z.position[2] * self.S
        # Tolerate a wider 1% on AV (gravity rules + bleed factors may apply
        # depending on tag); the key assertion is "not K-direction".
        _within_tol(out_z, expected_via_S, f"AV layer '{av_with_z.name}' Z (S formula)", tol=0.01)
