# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial.
# See LICENSE for full terms.

"""Output-naming op: resolve a conformed comp's output name. Headless.

Same resolver the duplication planner uses (`core.output_naming`),
so the CLI preview matches what EXECUTE would name the comp —
including the `_vN` collision bump.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from dimension.common import DimensionError
from core.output_naming import (
    DEFAULT_OUTPUT_NAME_TEMPLATE,
    resolve_output_name,
)


def _load_existing(existing: Optional[str], existing_file: Optional[str]) -> set[str]:
    names: set[str] = set()
    if existing:
        names.update(n.strip() for n in existing.split(",") if n.strip())
    if existing_file:
        if not os.path.isfile(existing_file):
            raise DimensionError(
                f"existing-names file not found: {existing_file}",
                code="SOURCE_NOT_FOUND",
            )
        with open(existing_file, "r", encoding="utf-8") as f:
            raw = f.read()
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                names.update(str(n) for n in parsed)
            else:
                raise ValueError("expected a JSON list")
        except ValueError:
            # Fall back to one name per line.
            names.update(
                line.strip() for line in raw.splitlines() if line.strip()
            )
    return names


def resolve_name_op(
    *,
    source: str,
    existing: Optional[str] = None,
    existing_file: Optional[str] = None,
    template: Optional[str] = None,
    width: int = 0,
    height: int = 0,
    preset_id: str = "",
) -> dict[str, Any]:
    """Resolve the output comp name for `source`, bumping on collision.

    `template` defaults to the engine's own DEFAULT_OUTPUT_NAME_TEMPLATE
    (`{source}_{preset}`) — the CLI never invents its own default.
    """
    if not source:
        raise DimensionError("source comp name is required", code="USAGE")
    resolved = resolve_output_name(
        source,
        _load_existing(existing, existing_file),
        template=template or DEFAULT_OUTPUT_NAME_TEMPLATE,
        target_width=width,
        target_height=height,
        preset_id=preset_id,
    )
    return {
        "status": "OK",
        "headless": True,
        "source": source,
        "name": resolved.name,
        "version": resolved.version,
        "template": template or DEFAULT_OUTPUT_NAME_TEMPLATE,
    }
