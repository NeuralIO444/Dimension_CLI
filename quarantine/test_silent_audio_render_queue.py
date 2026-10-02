# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_silent_audio_render_queue.py
Unit tests for TASK-P2-03 (#251) — Silent Audio Stripping & Output Module Presetting in Render Queue.
"""

import json
from pathlib import Path
import pytest
from core.render_queue import RenderQueuePlanner, RenderQueueConfig


@pytest.fixture(scope="module")
def ooh_specs() -> list:
    specs_path = Path(__file__).resolve().parents[2] / "config" / "ooh_specs.json"
    with open(specs_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("production_specs", []) if isinstance(data, dict) else data


class TestRenderQueuePlanner:
    def test_silent_audio_invariant_across_all_ooh_specs(self, ooh_specs):
        """ADR 03 Invariant: Every OOH spec where audio == False must plan disable_audio=True."""
        assert len(ooh_specs) >= 50
        for spec in ooh_specs:
            if not spec.get("audio", False):
                cfg = RenderQueuePlanner.plan_for_spec(spec)
                assert cfg.disable_audio is True, f"Spec {spec.get('spec_id')} failed silent audio invariant"

    def test_codec_template_and_alpha_mapping(self):
        """Codec strings map to standard AE Output Module templates and alpha settings."""
        # MP4 -> H.264, no alpha
        cfg_mp4 = RenderQueuePlanner.plan_for_spec({"video_codec": "MP4", "audio": False})
        assert isinstance(cfg_mp4, RenderQueueConfig)
        assert cfg_mp4.template_name == "H.264"
        assert cfg_mp4.file_extension == ".mp4"
        assert cfg_mp4.disable_audio is True
        assert cfg_mp4.render_alpha is False

        # ProRes 4444 -> ProRes 4444 with Alpha, alpha enabled
        cfg_pr4444 = RenderQueuePlanner.plan_for_spec({"video_codec": "ProRes 4444", "audio": False})
        assert cfg_pr4444.template_name == "ProRes 4444 with Alpha"
        assert cfg_pr4444.file_extension == ".mov"
        assert cfg_pr4444.disable_audio is True
        assert cfg_pr4444.render_alpha is True

        # Targa Sequence -> Targa, alpha enabled
        cfg_tga = RenderQueuePlanner.plan_for_spec({"video_codec": "Targa Sequence", "audio": False})
        assert cfg_tga.template_name == "Targa"
        assert cfg_tga.file_extension == ".tga"
        assert cfg_tga.render_alpha is True

    def test_extendscript_options_serialization(self):
        """Verifies ExtendScript options dictionary contract."""
        cfg = RenderQueuePlanner.plan_for_spec(
            {"video_codec": "ProRes 422 HQ", "audio": False},
        )
        opts = cfg.to_extendscript_options(output_path="/Volumes/SAN/Deliverables/Spot_01.mov")
        assert opts["templateName"] == "ProRes 422 HQ"
        assert opts["fileExtension"] == ".mov"
        assert opts["disableAudio"] is True
        assert opts["renderAlpha"] is False
        assert opts["outputPath"] == "/Volumes/SAN/Deliverables/Spot_01.mov"


class TestBabysitterRenderQueueModule:
    def test_babysitter_jsx_contains_silent_audio_and_render_queue_methods(self):
        """Babysitter.jsx bundle must expose stripAudioFromComp and configureRenderQueueItem."""
        jsx_path = Path(__file__).resolve().parents[2] / "Scripts" / "Dimension_Assets" / "Babysitter.jsx"
        content = jsx_path.read_text(encoding="utf-8")

        assert "Babysitter.stripAudioFromComp" in content
        assert "Babysitter.configureRenderQueueItem" in content
        assert "audioEnabled = false" in content
        assert "includeAudio = false" in content
        assert "applyTemplate" in content
