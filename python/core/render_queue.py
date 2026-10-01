# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/render_queue.py
TASK-P2-03 (Issue #251) — Silent Audio Stripping & Output Module Presetting in Render Queue.

Implements ADR 03 (Silent Audio Invariant) and Pre-Mortem Scenario 4 (Defensive Template Fallback).
Maps OOH delivery specifications to After Effects Render Queue output module configurations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


# Standard After Effects Output Module Template Mappings
DEFAULT_TEMPLATE_MAP: Dict[str, str] = {
    "MP4": "H.264",
    "H.264": "H.264",
    "H.265": "H.265",
    "ProRes 4444": "ProRes 4444 with Alpha",
    "ProRes 422 HQ": "ProRes 422 HQ",
    "ProRes 422": "ProRes 422",
    "ProRes 422 LT": "ProRes 422 LT",
    "Targa Sequence": "Targa",
    "PNG Sequence": "PNG",
    "TIFF Sequence": "TIFF",
}

DEFAULT_CONTAINER_EXTENSIONS: Dict[str, str] = {
    "MP4": ".mp4",
    "H.264": ".mp4",
    "H.265": ".mp4",
    "ProRes 4444": ".mov",
    "ProRes 422 HQ": ".mov",
    "ProRes 422": ".mov",
    "ProRes 422 LT": ".mov",
    "Targa Sequence": ".tga",
    "PNG Sequence": ".png",
    "TIFF Sequence": ".tif",
}


@dataclass(frozen=True)
class RenderQueueConfig:
    template_name: str
    file_extension: str
    disable_audio: bool
    render_alpha: bool
    delivery_codec: str

    def to_extendscript_options(self, output_path: Optional[str] = None) -> Dict[str, Any]:
        """Formats config into the options dict consumed by Babysitter.configureRenderQueueItem."""
        return {
            "templateName": self.template_name,
            "fileExtension": self.file_extension,
            "disableAudio": self.disable_audio,
            "renderAlpha": self.render_alpha,
            "outputPath": output_path,
        }


class RenderQueuePlanner:
    """Plans Render Queue item configuration from OOH or broadcast delivery specifications."""

    @classmethod
    def plan_for_spec(
        cls,
        spec: Dict[str, Any],
        custom_template_map: Optional[Dict[str, str]] = None,
    ) -> RenderQueueConfig:
        """Generates a RenderQueueConfig from an OOH delivery specification dictionary."""
        template_map = {**DEFAULT_TEMPLATE_MAP, **(custom_template_map or {})}

        video_codec = str(spec.get("video_codec") or spec.get("codec") or "MP4").strip()
        template_name = template_map.get(video_codec, "Lossless")
        file_ext = DEFAULT_CONTAINER_EXTENSIONS.get(video_codec, ".mov")

        # ADR 03: Silent Audio Invariant.
        # If audio is explicitly false or omitted in OOH specs, enforce silent audio.
        audio_allowed = bool(spec.get("audio", False))
        disable_audio = not audio_allowed

        render_alpha = bool(
            spec.get("alpha", False)
            or "Alpha" in template_name
            or video_codec in ("ProRes 4444", "PNG Sequence", "Targa Sequence")
        )

        return RenderQueueConfig(
            template_name=template_name,
            file_extension=file_ext,
            disable_audio=disable_audio,
            render_alpha=render_alpha,
            delivery_codec=video_codec,
        )
