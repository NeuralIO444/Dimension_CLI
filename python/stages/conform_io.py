# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Conform stage I/O — manifest load, integrity check, target resolution.

Pure Python helpers used by `stages.conform.run_conform` before math runs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

from core.logger import log
from logic.preset_manager import PresetManager
from models.bridge_contract import is_fresh_jsx_manifest, parse_jsx_wire_manifest
from models.scrape_manifest import ScrapeManifest
from pydantic import ValidationError

from stages.conform import ConformConfig, ConformError, IntegrityCheckError, SourceNotFoundError


@dataclass(frozen=True)
class ConformTarget:
    width: int
    height: int
    scale_mode: str
    bleed_pct: float
    duration: Optional[float] = None
    fps: Optional[float] = None
    # Track B / B2 (2026-08-26) — the active preset's naming template
    # (Preset.output_name_template), threaded through to
    # PayloadSlicer.slice_and_export. None when conforming via raw
    # --width/--height (no preset, nothing to pull a template from);
    # slice_and_export falls back to DEFAULT_OUTPUT_NAME_TEMPLATE.
    output_name_template: Optional[str] = None


def load_scrape_manifest(source: str) -> tuple[ScrapeManifest, dict[str, Any]]:
    """Load and validate scrape_manifest.json.  Raises ConformError subclasses."""
    path = os.path.abspath(source)
    if not os.path.exists(path):
        log.error("Source manifest not found", extra={"path": path})
        raise SourceNotFoundError(f"Source manifest not found: {path}")

    verify_manifest_integrity(path)

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except json.JSONDecodeError as e:
        log.error("Manifest JSON is malformed", extra={"error": str(e), "path": path})
        raise ConformError(f"Manifest JSON is malformed: {e}") from e

    if is_fresh_jsx_manifest(raw_data):
        try:
            parse_jsx_wire_manifest(raw_data)
            log.info(
                "JSX wire manifest validation passed",
                extra={"path": path, "layers": len(raw_data.get("layers", []))},
            )
        except ValidationError as e:
            log.error(
                "JSX wire manifest validation failed",
                extra={
                    "path": path,
                    "errors": e.error_count(),
                    "detail": e.errors()[:5],
                },
            )
            raise ConformError(
                f"JSX wire manifest validation failed "
                f"({e.error_count()} error(s)): "
                f"{e.errors()[0]['msg'] if e.errors() else 'unknown'}"
            ) from e

    try:
        manifest = ScrapeManifest.model_validate(raw_data)
    except Exception as e:
        log.error("Manifest validation failed", extra={"error": str(e)})
        raise ConformError(f"Manifest validation failed: {e}") from e

    return manifest, raw_data


def verify_manifest_integrity(source: str) -> None:
    """Sidecar hash check — missing sidecar is OK (AE-panel scrape)."""
    from core.io_utils import verify_manifest_hash

    try:
        verify_manifest_hash(source)
        log.info("Manifest integrity", extra={"path": source, "note": "verified"})
    except FileNotFoundError:
        log.info(
            "Manifest integrity",
            extra={"path": source, "note": "no sidecar (AE-panel scrape — expected)"},
        )
    except ValueError as ve:
        log.error(
            "Manifest integrity check failed",
            extra={"path": source, "note": str(ve).splitlines()[0]},
        )
        raise IntegrityCheckError(str(ve)) from ve


def resolve_conform_target(config: ConformConfig) -> ConformTarget:
    """Map CLI/config fields to conform dimensions and mode."""
    if config.preset:
        preset = PresetManager().presets.get(config.preset)
        if not preset:
            log.error(
                "Unknown preset",
                extra={"id": config.preset, "available": list(PresetManager().presets.keys())},
            )
            raise ConformError(f"Unknown preset: {config.preset}")
        return ConformTarget(
            width=preset.width,
            height=preset.height,
            scale_mode=config.mode,
            bleed_pct=config.bleed,
            duration=getattr(preset, "duration", None) or config.duration,
            fps=getattr(preset, "fps", None) or config.fps,
            output_name_template=getattr(preset, "output_name_template", None),
        )

    if not config.width or not config.height:
        log.error("Must supply --preset OR both --width and --height")
        raise ConformError("Must supply --preset OR both --width and --height")

    return ConformTarget(
        width=config.width,
        height=config.height,
        scale_mode=config.mode,
        bleed_pct=config.bleed,
        duration=config.duration,
        fps=config.fps,
    )