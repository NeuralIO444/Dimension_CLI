# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/bridge/color_match_bridge.py
Color Match (Track D, CM2) — reference-frame render bridge.

Dispatches a `color-match-render` job through the existing
`SovereignBridge` dual-transport pipe (TCP socket on port 45445,
falling back to the `.dimension_inbox/` file bridge) via the
`SovereignBridge.execute_bridge_job()` façade. Handled on the AE side
by `socket_server.jsx`'s `dispatchRequest` and
`Dimension_Launcher.jsx`'s `_handleColorMatchRenderJob`, both of which
call `export_frame.jsx`'s `D.core.exportFrameById()`.

This module owns render dispatch only. It does not touch the scrape
manifest, the conform pipeline, or the AE project beyond requesting a
read-only `saveFrameToPng` call — see `export_frame.jsx` for the
Anti-Shatter accounting on the JSX side.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from bridge.sovereign_bridge import SovereignBridge
from core.logger import log

# Reference-frame renders can be slow on heavy 4K/3D comps — 120s is a
# generous ceiling, matched to the other bridge jobs' pattern of a
# timeout sized to the slowest realistic case rather than the typical
# one (c.f. DEFAULT_SCRAPE_TIMEOUT_S in sovereign_bridge.py).
DEFAULT_RENDER_TIMEOUT_S = 120.0


class ColorMatchError(RuntimeError):
    """Raised when a Color Match bridge job fails on the AE side."""


class ColorMatchBridge:
    """Python ↔ After Effects Color Match IPC layer.

    Thin wrapper around a `SovereignBridge` instance, scoped to the
    Color Match job family. One bridge per project root, same
    convention as `SovereignBridge` itself.
    """

    def __init__(self, project_root: Optional[str] = None):
        self._bridge = SovereignBridge(project_root)

    def render_reference_frame(
        self,
        comp_id: str,
        comp_name: str,
        output_path: Path,
        *,
        project_path: Optional[str] = None,
        timeout_s: float = DEFAULT_RENDER_TIMEOUT_S,
    ) -> dict:
        """Request AE to render a reference-frame PNG of `comp_id`.

        `comp_name` is a fallback lookup key if the id lookup misses
        JSX-side (see `ColorMatchRenderJob` docstring). `project_path`,
        if given, is checked against the currently open AE project so
        a render can't silently target the wrong project.

        Returns the JSX-side result payload (dict) on success. Raises
        `ColorMatchError` on AE-side failure or on a bridge dispatch
        exception.
        """
        if not comp_id:
            raise ValueError("comp_id is required")
        if not comp_name:
            raise ValueError("comp_name is required")
        if not output_path:
            raise ValueError("output_path is required")

        job = {
            "type": "color-match-render",
            "comp_id": str(comp_id),
            "comp_name": str(comp_name),
            "output_path": str(Path(output_path)),
        }
        if project_path:
            job["project_path"] = str(project_path)

        try:
            payload = self._bridge.execute_bridge_job(job, timeout_s)
        except Exception as e:
            log.error(
                "Color Match render dispatch failed",
                extra={"comp_id": comp_id, "error": str(e)},
            )
            raise ColorMatchError(f"Reference frame render failed: {e}") from e

        if (payload.get("status") or "").upper() != "OK":
            err = payload.get("error") or f"color-match-render failed ({payload!r})"
            log.error("Color Match render reported error", extra={"error": err})
            raise ColorMatchError(err)

        log.info(
            "Color Match reference frame rendered",
            extra={"comp_id": comp_id, "path": payload.get("path")},
        )
        return payload

    def inject_lut(
        self,
        parent_comp_id: str,
        precomp_comp_id: str,
        lut_path: Path,
        *,
        target_layer_uid: Optional[str] = None,
        project_path: Optional[str] = None,
        timeout_s: float = DEFAULT_RENDER_TIMEOUT_S,
    ) -> dict:
        """Request AE to inject a 3D LUT adjustment layer above a precomp wrapper.

        `parent_comp_id` identifies the master containing composition.
        `precomp_comp_id` identifies the source precomp being graded.
        `lut_path` is the absolute path to the `.cube` or `.3dl` file.
        `target_layer_uid`, if given, is the UID token stamped into the
        precomp wrapper layer's comment field for precise target resolution.

        Returns the JSX-side result payload (dict) on success. Raises
        `ColorMatchError` on AE-side failure or on a bridge dispatch
        exception.
        """
        if not parent_comp_id:
            raise ValueError("parent_comp_id is required")
        if not precomp_comp_id:
            raise ValueError("precomp_comp_id is required")
        if not lut_path:
            raise ValueError("lut_path is required")

        job = {
            "type": "color-match-inject",
            "parent_comp_id": str(parent_comp_id),
            "precomp_comp_id": str(precomp_comp_id),
            "lut_path": str(Path(lut_path)),
        }
        if target_layer_uid:
            job["target_layer_uid"] = str(target_layer_uid)
        if project_path:
            job["project_path"] = str(project_path)

        try:
            payload = self._bridge.execute_bridge_job(job, timeout_s)
        except Exception as e:
            log.error(
                "Color Match inject dispatch failed",
                extra={"parent_comp_id": parent_comp_id, "precomp_comp_id": precomp_comp_id, "error": str(e)},
            )
            raise ColorMatchError(f"LUT injection failed: {e}") from e

        if (payload.get("status") or "").upper() != "OK":
            err = payload.get("error") or f"color-match-inject failed ({payload!r})"
            log.error("Color Match inject reported error", extra={"error": err})
            raise ColorMatchError(err)

        log.info(
            "Color Match LUT injected",
            extra={"parent_comp_id": parent_comp_id, "precomp_comp_id": precomp_comp_id, "lut_path": str(lut_path)},
        )
        return payload
