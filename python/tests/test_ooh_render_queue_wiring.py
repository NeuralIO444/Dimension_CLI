# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_ooh_render_queue_wiring.py — #417.

Three independently-built pieces existed with nothing connecting them:
core.render_queue.RenderQueuePlanner (tested, zero production callers),
Babysitter_src/95_render_queue.jsx::configureRenderQueueItem (fully
built, never called except by a bundle-presence test), and
data.target_catalog's OOH metadata (already the right shape for
plan_for_spec()). exporter.py's `render_queue_options` param has
existed since #348's narrow slice with no caller that ever set it.

This proves `stages.conform.plan_ooh_render_queue_options` — the new
caller — is strictly gated: only OOH-channel targets whose metadata
actually carries a codec/audio spec produce a non-None result, and the
output keys match Babysitter_src/50_workspace.jsx's manifest reader
(`template_name`/`file_extension`/`disable_audio`, snake_case — NOT
RenderQueueConfig.to_extendscript_options()'s camelCase, which is
JSX-facing, not manifest-facing).
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from logic.preset_manager import PresetManager
from stages.conform import plan_ooh_render_queue_options
from models.target import Target


def _target_ns(channel=None, metadata=None):
    return types.SimpleNamespace(channel=channel, metadata=metadata or {})


class TestGating:
    def test_non_ooh_channel_returns_none(self):
        t = _target_ns(channel="social", metadata={"video_codec": "H.264", "audio": False})
        assert plan_ooh_render_queue_options(t) is None

    def test_none_channel_returns_none(self):
        t = _target_ns(channel=None, metadata={"video_codec": "H.264"})
        assert plan_ooh_render_queue_options(t) is None

    def test_ooh_channel_with_no_metadata_returns_none(self):
        t = _target_ns(channel="ooh", metadata={})
        assert plan_ooh_render_queue_options(t) is None

    def test_ooh_channel_with_unrelated_metadata_only_returns_none(self):
        """metadata present but neither video_codec nor audio set -- still gated off."""
        t = _target_ns(channel="ooh", metadata={"market_id": "NYC-001", "still": True})
        assert plan_ooh_render_queue_options(t) is None

    def test_ooh_channel_with_metadata_none_returns_none(self):
        t = _target_ns(channel="ooh", metadata=None)
        assert plan_ooh_render_queue_options(t) is None


class TestPlanning:
    def test_ooh_with_video_codec_produces_options(self):
        t = _target_ns(channel="ooh", metadata={"video_codec": "H.264", "audio": False})
        result = plan_ooh_render_queue_options(t)
        assert result is not None
        assert result["template_name"] == "H.264"
        assert result["file_extension"] == ".mp4"
        assert result["disable_audio"] is True

    def test_ooh_with_audio_false_explicit_disables_audio(self):
        t = _target_ns(channel="ooh", metadata={"audio": False})
        result = plan_ooh_render_queue_options(t)
        assert result is not None
        assert result["disable_audio"] is True

    def test_ooh_with_audio_true_allows_audio(self):
        t = _target_ns(channel="ooh", metadata={"video_codec": "ProRes 422 HQ", "audio": True})
        result = plan_ooh_render_queue_options(t)
        assert result is not None
        assert result["disable_audio"] is False

    def test_output_keys_are_snake_case_manifest_shape(self):
        """Must match Babysitter_src/50_workspace.jsx's manifest reader
        (template_name/file_extension/disable_audio), not
        RenderQueueConfig.to_extendscript_options()'s camelCase shape."""
        t = _target_ns(channel="ooh", metadata={"video_codec": "ProRes 4444", "audio": False})
        result = plan_ooh_render_queue_options(t)
        assert set(result.keys()) == {"template_name", "file_extension", "disable_audio"}

    def test_unknown_codec_falls_back_to_lossless_template(self):
        t = _target_ns(channel="ooh", metadata={"video_codec": "Some Weird Codec", "audio": False})
        result = plan_ooh_render_queue_options(t)
        assert result["template_name"] == "Lossless"


class TestRealTargetModel:
    """Same assertions via a real pydantic Target (not just SimpleNamespace)
    to catch schema drift a duck-typed test would miss."""

    def test_real_ooh_target_with_metadata(self):
        t = Target.make(
            id="builtin:ooh_test_001",
            label="Test DOOH (1920x1080)",
            category="custom_signage",
            subcategory="dooh",
            width=1920,
            height=1080,
            aspect_label="16:9",
            channel="ooh",
            metadata={"video_codec": "H.264", "audio": False, "bitrate": "10mbps"},
        )
        result = plan_ooh_render_queue_options(t)
        assert result == {
            "template_name": "H.264",
            "file_extension": ".mp4",
            "disable_audio": True,
        }

    def test_real_social_target_returns_none(self):
        t = Target.make(
            id="custom:vertical_test",
            label="Test Vertical",
            category="social",
            subcategory="tiktok",
            width=1080,
            height=1920,
            aspect_label="9:16",
            source="custom",
            channel="social",
        )
        assert plan_ooh_render_queue_options(t) is None


class TestPresetManagerResolutionPath:
    """Proves the REAL path run_conform takes: stages.conform_io's
    resolve_conform_target() returns the slim ConformTarget (no
    channel/metadata at all -- confirmed by reading
    stages/conform_io.py's ConformTarget dataclass), so run_conform
    resolves the full catalog Target separately via
    PresetManager().get_target(config.preset), the same pattern
    stages.conform_passes.apply_soe_pass already uses for its own
    Target-only field lookup. This is the actual production catalog
    (data.target_catalog._load_ooh_targets), not a hand-built fixture."""

    def test_real_ooh_catalog_target_resolves_and_plans(self):
        from data.target_catalog import OOH
        if not OOH:
            import pytest
            pytest.skip("No OOH targets loaded from production specs JSON")

        real_ooh_id = OOH[0].id  # e.g. "builtin:ooh_ooh_001"
        resolved = PresetManager().get_target(real_ooh_id)
        assert resolved is not None
        assert resolved.channel == "ooh"

        result = plan_ooh_render_queue_options(resolved)
        # Every seeded OOH spec has audio=False (ADR-03 default) --
        # this must always come out muted, whatever the codec.
        assert result is not None
        assert result["disable_audio"] is True

    def test_get_target_returns_none_for_unknown_preset(self):
        assert PresetManager().get_target("no_such_preset_xyz") is None
        assert plan_ooh_render_queue_options(None) is None
