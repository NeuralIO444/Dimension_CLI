# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Headless conform pipeline op: scrape_manifest.json -> chunk JSON + report.

Pure math + file writes — no After Effects needed. Applying the
conformed chunks back into AE ("inject") is a live-AE step owned by
the bridge (`dimension ae ...`), deliberately separate.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from typing import Any, Optional

from dimension.common import DimensionError
from stages.conform import ConformConfig, run_conform
from stages.conform_io import ConformError, SourceNotFoundError


def run_conform_op(
    *,
    source: str,
    preset: Optional[str] = None,
    profile: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    duration: Optional[float] = None,
    fps: Optional[float] = None,
    mode: str = "Fit",
    layout: str = "tags",
    bleed: float = 0.0,
    output: str = "Chunks",
    no_report: bool = False,
    allow_state_hash_bypass: bool = False,
) -> dict[str, Any]:
    """Execute the conform pipeline. Returns paths + summary.

    Raises DimensionError on missing source or engine failure.
    """
    if not os.path.isfile(source):
        raise DimensionError(
            f"scrape manifest not found: {source}",
            code="SOURCE_NOT_FOUND",
        )
    config = ConformConfig(
        source=source,
        preset=preset,
        profile=profile,
        width=width,
        height=height,
        duration=duration,
        fps=fps,
        mode=mode,
        layout=layout,
        bleed=bleed,
        output=output,
        no_report=no_report,
        allow_state_hash_bypass=allow_state_hash_bypass,
    )
    try:
        # The engine streams progress JSON to stdout; the CLI's machine
        # face owns stdout, so progress is rerouted to stderr (logs).
        # contextlib.redirect_stdout cannot target sys.stderr directly
        # (it needs a .write), so capture-then-forward it is.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = run_conform(config)
        progress = buf.getvalue()
        if progress:
            sys.stderr.write(progress)
            if not progress.endswith("\n"):
                sys.stderr.write("\n")
    except SourceNotFoundError as e:
        raise DimensionError(str(e), code="SOURCE_NOT_FOUND") from e
    except ConformError as e:
        raise DimensionError(str(e), code="CONFORM_FAILED") from e
    return {
        "status": "OK",
        "headless": True,
        "chunk_manifest_path": result.chunk_manifest_path,
        "conformed_path": result.conformed_path,
        "report_path": result.report_path,
        "chunk_count": result.chunk_count,
        "run_warnings": list(result.run_warnings),
    }
