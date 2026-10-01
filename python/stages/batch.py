# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Multi-target conform batch — runs `stages.conform.run_conform` in-process.

Replaces subprocess `cli execute` hops from dimension_server so batch
conform shares the same code path as the headless CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from stages.conform import ConformConfig, ConformError, run_conform


@dataclass(frozen=True)
class BatchTarget:
    preset: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    name: Optional[str] = None


def _target_label(target: BatchTarget | dict[str, Any]) -> str:
    if isinstance(target, dict):
        return (
            target.get("name")
            or target.get("preset")
            or f"{target.get('width')}x{target.get('height')}"
        )
    return target.name or target.preset or f"{target.width}x{target.height}"


def _to_batch_target(raw: dict[str, Any]) -> BatchTarget:
    return BatchTarget(
        preset=raw.get("preset"),
        width=raw.get("width"),
        height=raw.get("height"),
        name=raw.get("name"),
    )


def run_batch_conform(
    manifest_path: str,
    targets: list[dict[str, Any] | BatchTarget],
    *,
    profile: str = "default",
    mode: str = "Fit",
    bleed: float = 0.0,
    layout: str = "auto",
    on_log: Optional[Callable[[str], None]] = None,
    on_engine_event: Optional[Callable[[dict[str, Any]], None]] = None,
    on_target_start: Optional[Callable[[int, int, str], None]] = None,
) -> tuple[int, list[str]]:
    """Conform each target.  Returns (success_count, log_lines)."""
    logs: list[str] = []
    success_count = 0
    total = len(targets)

    def _log(msg: str) -> None:
        logs.append(msg)
        if on_log:
            on_log(msg)

    for i, raw in enumerate(targets):
        target = raw if isinstance(raw, BatchTarget) else _to_batch_target(raw)
        label = _target_label(target)
        if on_target_start:
            on_target_start(i, total, label)
        _log(f"--- Conforming Target {i + 1}/{total}: {label} ---")

        cfg = ConformConfig(
            source=manifest_path,
            preset=target.preset,
            profile=profile,
            width=target.width,
            height=target.height,
            mode=mode,
            bleed=bleed,
            # ConformConfig.layout defaults to "tags" (legacy, no precomp
            # sealing) for CLI backward-compat, which is why this
            # function's own default is "auto" instead — matches the CEP
            # panel's Execute button default (main.js: `const layout =
            # (layoutSel && layoutSel.value) ? ... : 'auto'`) so nested
            # precomps stay sealed unless a caller explicitly opts into
            # something else. A caller (e.g. the Dashboard's ported
            # Layout select) may still pass "scene"/"tags" deliberately.
            layout=layout,
            allow_state_hash_bypass=(i > 0),
        )
        try:
            result = run_conform(cfg, on_engine_event=on_engine_event)
            success_count += 1
            _log(f"Target {label} conformed successfully.")
            _log(result.chunk_manifest_path)
        except ConformError as e:
            _log(f"Target {label} conform failed: {e}")
        except Exception as e:
            _log(f"Error running conform for {label}: {e}")

    return success_count, logs