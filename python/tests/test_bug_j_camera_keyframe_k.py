"""
Bug J — Camera keyframe K-scaling contract test.

Real-data contract test: loads the saved 87N source manifest
(reused from Bug L's fixtures since 87N has a 3-keyframe camera
position_z stream) and asserts that the lerp engine scales camera
depth-axis keyframes by K, not S.

    K = max(target_W / source_W, target_H / source_H)   (no bleed)
    S = uniform_scale × (1.0 + bleed_pct)                (with bleed)

Bug L shipped the parallel fix on the static path. Bug J ships
the keyframe parallel.

Synthetic-fixture anti-pattern guard: this test loads a real saved
manifest. The Phase 1 grep audit confirmed the `_dead_zone_filter`
collapses constant hold-keys before they reach `applyKeys`, so
position_x / position_y assertions on 87N (which has constant X/Y
hold-keys at 960/540) test that the *single* surviving keyframe
holds the S-formula result, not K. position_z carries 3 distinct
values and is the primary K-vs-S contract.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
))

from core.lerp_engine import LerpEngine
from core.classify import detect_3d_camera_scene, should_scale_z
from core.scale_engine import ScaleEngine
from models.scrape_manifest import ScrapeManifest


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "bug_l" / "87n_source_manifest.json"
)
TOLERANCE = 0.005  # 0.5%

# 87N HD → TIKTOK: K materially diverges from S.
HD_TARGET_W = 1080
HD_TARGET_H = 1920


def _load() -> ScrapeManifest:
    with open(FIXTURE_PATH) as f:
        return ScrapeManifest.model_validate(json.load(f))


def _conform_camera_keys(manifest: ScrapeManifest, target_w: int, target_h: int):
    """Run the conform + lerp pipeline and return the camera's conformed_keys."""
    engine = ScaleEngine(
        manifest=manifest,
        target_width=target_w,
        target_height=target_h,
        scale_mode="Fit",
        bleed_pct=0.05,
    )
    result = engine.conform()
    s_factor = result["scale"]["S"]
    is_3d_camera_scene = detect_3d_camera_scene(manifest.layers)

    src_w = manifest.project_info.width
    src_h = manifest.project_info.height
    K = max(target_w / src_w, target_h / src_h)

    src_center = [src_w / 2.0, src_h / 2.0]
    tgt_center = [target_w / 2.0, target_h / 2.0]

    lerp = LerpEngine()
    cam_layer = next(l for l in manifest.layers if l.layer_kind == "camera")
    td = cam_layer.temporal_data.model_dump(exclude_none=True) if cam_layer.temporal_data else {}

    keys = lerp.package_conformed_keys(
        td, s_factor,
        is_root=True,
        src_center=src_center, tgt_center=tgt_center,
        scale_z=should_scale_z(cam_layer, is_3d_camera_scene),
        K=K if is_3d_camera_scene else None,
        layer_kind="camera",
    )
    return keys, K, s_factor, src_center, tgt_center, cam_layer


def _within_tol(actual: float, expected: float, label: str, tol: float = TOLERANCE) -> None:
    if expected == 0:
        assert actual == 0, f"BUG_J {label}: expected 0, got {actual}"
        return
    rel = abs(actual - expected) / abs(expected)
    assert rel <= tol, (
        f"BUG_J {label}: expected {expected:.4f}, got {actual:.4f}, "
        f"relative error {rel * 100:.3f}% (tolerance {tol * 100:.1f}%)"
    )


class TestBugJCameraKeyframeK:
    """Primary contract: K≠S case (HD→TIKTOK)."""

    @classmethod
    def setup_class(cls):
        cls.manifest = _load()
        (cls.keys, cls.K, cls.S, cls.src_center, cls.tgt_center, cls.cam_layer) = (
            _conform_camera_keys(cls.manifest, HD_TARGET_W, HD_TARGET_H)
        )
        cls.src_td = cls.cam_layer.temporal_data.model_dump(exclude_none=True)

    def test_K_materially_diverges_from_S(self):
        """Sanity: HD→TIKTOK K must differ from S by >50%, otherwise
        the assertions below don't actually distinguish K from S."""
        assert abs(self.K - self.S) / self.S > 0.5, (
            f"K={self.K:.4f}, S={self.S:.4f}: too similar to validate Bug J"
        )

    def test_position_z_keys_scale_by_K(self):
        """Camera position_z keyframes must scale by K (depth-axis),
        not S. This is the primary Bug J contract."""
        src_values = self.src_td["position_z"]["values"]
        out = self.keys.position_z
        assert out is not None, "position_z conformed_keys missing"
        out_values = out.values
        # Dead-zone filter may collapse, but 87N's z is 3 distinct values
        assert len(out_values) == len(src_values), (
            f"position_z: expected {len(src_values)} keys, got {len(out_values)} "
            f"(dead-zone unexpectedly collapsed; src={src_values})"
        )
        for i, src_v in enumerate(src_values):
            expected = src_v * self.K
            _within_tol(out_values[i], expected, f"position_z[{i}]")

    def test_position_z_keys_NOT_scaled_by_S(self):
        """Negative regression: position_z keys must NOT match source × S
        (the pre-Bug-J behavior). Catches an accidental revert."""
        src_z0 = self.src_td["position_z"]["values"][0]
        out_z0 = self.keys.position_z.values[0]
        wrong = src_z0 * self.S
        rel = abs(out_z0 - wrong) / abs(wrong) if wrong != 0 else abs(out_z0)
        assert rel > 0.1, (
            f"BUG_J REGRESSION: position_z[0]={out_z0:.2f} resembles "
            f"source × S ({wrong:.2f}). Expected source × K "
            f"({src_z0 * self.K:.2f}). Verify apply_rule's K branch fires "
            f"for ROOT_SCALE_AXIS_Z when layer_kind=='camera'."
        )

    def test_position_x_keys_still_use_S_formula(self):
        """Negative regression: camera position_x keys are world-space
        lateral, not depth-axis. Bug L kept these on S; Bug J inherits
        that. Source x = [960, 960, 960] hold-keys → after S center-
        remap, all three collapse to tgt_cx (540) and the dead-zone
        filter reduces to 1 key. The single surviving value must equal
        the S-formula result, not the K-formula."""
        src_x0 = self.src_td["position_x"]["values"][0]
        out = self.keys.position_x
        assert out is not None, "position_x conformed_keys missing"
        out_x0 = out.values[0]
        expected_via_S = ((src_x0 - self.src_center[0]) * self.S) + self.tgt_center[0]
        _within_tol(out_x0, expected_via_S, "position_x[0] (S formula)")

    def test_av_layer_position_keys_still_use_S(self):
        """Negative regression: AV-layer position keys (any non-camera
        layer with keyed position) must continue scaling by S. Bug J
        is camera-only; AV path unchanged."""
        av_with_keys = next(
            l for l in self.manifest.layers
            if l.layer_kind != "camera"
            and l.temporal_data
            and (l.temporal_data.position or l.temporal_data.position_x)
        )
        is_3d_camera_scene = detect_3d_camera_scene(self.manifest.layers)
        K = max(HD_TARGET_W / self.manifest.project_info.width,
                HD_TARGET_H / self.manifest.project_info.height)
        td = av_with_keys.temporal_data.model_dump(exclude_none=True)
        lerp = LerpEngine()
        keys = lerp.package_conformed_keys(
            td, self.S,
            is_root=True,
            src_center=self.src_center, tgt_center=self.tgt_center,
            scale_z=should_scale_z(av_with_keys, is_3d_camera_scene),
            K=K if is_3d_camera_scene else None,
            layer_kind=av_with_keys.layer_kind or "av",
        )
        # Pick the unified position stream if present, else position_x
        unified = keys.position
        if unified is not None and unified.values:
            src_v = td.get("position", {}).get("values", [])[0]
            if isinstance(src_v, list) and len(src_v) >= 2:
                expected_x = ((src_v[0] - self.src_center[0]) * self.S) + self.tgt_center[0]
                _within_tol(unified.values[0][0], expected_x,
                            f"AV layer '{av_with_keys.name}' position[0] (S formula)")
        else:
            # Separated case
            ax = keys.position_x
            assert ax is not None, "AV layer has no position_x and no unified position"
            src_x0 = td["position_x"]["values"][0]
            expected_x = ((src_x0 - self.src_center[0]) * self.S) + self.tgt_center[0]
            _within_tol(ax.values[0], expected_x,
                        f"AV layer '{av_with_keys.name}' position_x[0] (S formula)")


class TestBugJKEqualsSNoOp:
    """Secondary contract: K=S case (no aspect change, no bleed).

    When K equals S, the camera branch in apply_rule must produce
    identical output to the AV branch — verifies Bug J doesn't leak
    into trivial-target-aspect cases.

    Skipped pending a same-aspect-zero-bleed fixture. K=S strictly
    requires bleed=0 AND target aspect = source aspect (87N HD source
    + HD target with bleed=0 would qualify, but no such fixture is
    captured today). Don't synthesize fixture data per Bug J locked
    decisions; defer until a real capture lands.
    """

    @pytest.mark.skip(
        reason="K=S identity case requires bleed=0 + same-aspect target "
        "fixture; not yet captured. Add fixture and remove this skip."
    )
    def test_camera_position_z_equals_S_path_when_K_equals_S(self):
        # Placeholder for when a fixture lands.
        pass
