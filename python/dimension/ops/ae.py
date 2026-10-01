# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Live-AE ops. Everything here needs After Effects running with the
Dimension poller (`Scripts/Dimension_Launcher.jsx`, File -> Scripts).

`probe` is the readiness check — it never sends a job, so it always
exits 0 and reports `ready: true/false`. The `mask` ops send real
bridge jobs and raise AE_UNAVAILABLE (exit 69) when no AE is
reachable, so scripts can tell "launch AE" apart from "bug".
"""

from __future__ import annotations

import os
from typing import Any, Optional

from dimension.common import EXIT_AE_UNAVAILABLE, DimensionError
from bridge.sovereign_bridge import ScrapeEngineError, SovereignBridge


def _bridge(project_root: Optional[str] = None) -> SovereignBridge:
    return SovereignBridge(project_root=project_root or os.path.abspath(os.getcwd()))


def probe_op(*, project_root: Optional[str] = None) -> dict[str, Any]:
    """AE reachability probe. Never sends a job; always exits 0."""
    bridge = _bridge(project_root)
    socket_ok = bridge._socket_is_listening()
    hb_ok, hb_reason, hb_age = bridge._heartbeat_status()
    alive, alive_reason = bridge._poller_is_alive()
    return {
        "status": "OK",
        "live_ae": True,
        "ready": alive,
        "socket_listening": socket_ok,
        "heartbeat": {
            "fresh": hb_ok,
            "reason": hb_reason or None,
            "age_s": hb_age,
        },
        "reason": None if alive else alive_reason,
        "hint": (
            None
            if alive
            else "Launch After Effects and run Scripts/Dimension_Launcher.jsx (File → Scripts)."
        ),
    }


def _require_ae(bridge: SovereignBridge) -> None:
    alive, reason = bridge._poller_is_alive()
    if not alive:
        raise DimensionError(
            f"After Effects is not reachable: {reason}",
            code="AE_UNAVAILABLE",
            exit_code=EXIT_AE_UNAVAILABLE,
        )


def mask_show_op(*, preset: str, project_root: Optional[str] = None) -> dict[str, Any]:
    """Import the preset's safe-zone mask PNG as a debug overlay in the
    active AE comp. LIVE AE — requires the poller."""
    from dimension.ops.safe_zone import resolve_target
    from logic.safe_zone_resolver import resolve_mask_for_target

    target = resolve_target(preset)
    resolved = resolve_mask_for_target(target)
    if resolved is None or not os.path.isfile(str(resolved)):
        raise DimensionError(
            f"MASK_MISSING — no safe-zone mask PNG for preset {preset!r}",
            code="MASK_MISSING",
        )
    bridge = _bridge(project_root)
    _require_ae(bridge)
    try:
        result = bridge.toggle_safe_zone_mask(action="import", mask_path=str(resolved))
    except ScrapeEngineError as e:
        raise DimensionError(str(e), code="AE_JOB_FAILED") from e
    return {
        "status": "OK",
        "live_ae": True,
        "action": "show",
        "preset": preset,
        "mask_path": str(resolved),
        "bridge_result": result,
    }


def mask_hide_op(*, project_root: Optional[str] = None) -> dict[str, Any]:
    """Remove the safe-zone debug overlay from the active AE comp.
    LIVE AE — requires the poller."""
    bridge = _bridge(project_root)
    _require_ae(bridge)
    try:
        result = bridge.toggle_safe_zone_mask(action="remove")
    except ScrapeEngineError as e:
        raise DimensionError(str(e), code="AE_JOB_FAILED") from e
    return {
        "status": "OK",
        "live_ae": True,
        "action": "hide",
        "bridge_result": result,
    }
