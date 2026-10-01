# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Single replace-map for output name / bin templates.

Unknown `{tokens}` stay in the string (forward-compat).
OOH-only keys that are unset become "" so `{panel}` does not leak
on a TikTok render.
`{date}` defaults to today's ISO date when the caller omits it.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Mapping, Optional

_TOKEN_RE = re.compile(r"\{([a-z0-9_]+)\}", re.IGNORECASE)

# Blank these when missing (do not leave `{panel}` in a social name).
_FORCE_EMPTY = frozenset({"panel", "panel_n", "gap", "chrome"})

_PREFIXES = ("builtin:", "custom:", "user:")


def slug_preset(preset_id: str) -> str:
    s = str(preset_id or "")
    for p in _PREFIXES:
        if s.startswith(p):
            s = s[len(p):]
            break
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s.lower() or "conform"


def slug_aspect(label: str) -> str:
    s = str(label or "").strip().lower()
    s = s.replace(":", "x").replace(".", "")
    s = re.sub(r"[^a-z0-9x]+", "", s)
    return s or ""


def build_naming_context(
    *,
    source: str = "",
    preset_id: str = "",
    width: int = 0,
    height: int = 0,
    preset_label: str = "",
    platform: str = "",
    aspect: str = "",
    category: str = "",
    pack: str = "",
    date: Optional[str] = None,
    time: str = "",
    panel: str = "",
    panel_n: Any = "",
    gap: Any = "",
    chrome: str = "",
    extra: Optional[Mapping[str, Any]] = None,
) -> dict:
    preset = slug_preset(preset_id)
    if date is None:
        date = _dt.date.today().isoformat()
    ctx = {
        "source": source or "",
        "preset": preset,
        "width": str(width or ""),
        "height": str(height or ""),
        "wxh": f"{width}x{height}" if width and height else "",
        "preset_label": preset_label or preset.replace("_", " ").title(),
        "platform": platform or "",
        "aspect": slug_aspect(aspect) if aspect else "",
        "category": category or "",
        "pack": pack or "",
        "date": date,
        "time": time or "",
        "panel": "" if panel is None else str(panel),
        "panel_n": "" if panel_n in (None, "") else str(panel_n),
        "gap": "" if gap in (None, "") else str(gap),
        "chrome": chrome or "",
    }
    if extra:
        for k, v in extra.items():
            ctx[str(k)] = "" if v is None else str(v)
    return ctx


def context_from_target(
    target: Any,
    *,
    source: str = "",
    extra: Optional[Mapping[str, Any]] = None,
) -> dict:
    meta = getattr(target, "metadata", None) or {}
    pack = ""
    if isinstance(meta, dict):
        sz = meta.get("safe_zone") or {}
        if isinstance(sz, dict):
            pack = str(sz.get("pack") or "")
    return build_naming_context(
        source=source,
        preset_id=getattr(target, "id", "") or "",
        width=int(getattr(target, "width", 0) or 0),
        height=int(getattr(target, "height", 0) or 0),
        preset_label=getattr(target, "label", "") or "",
        platform=getattr(target, "subcategory", "") or "",
        aspect=getattr(target, "aspect_label", "") or "",
        category=getattr(target, "category", "") or "",
        pack=pack,
        extra=extra,
    )


def apply_template(template: str, ctx: Mapping[str, Any]) -> str:
    def _sub(m: re.Match) -> str:
        key = m.group(1).lower()
        if key in _FORCE_EMPTY:
            return str(ctx.get(key, ""))
        if key in ctx:
            val = str(ctx[key])
            if val != "":
                return val
            # Known but unset (e.g. {aspect} with no target) — keep token
            # so older tests and forward-compat still see it.
            if key in ("source", "preset", "width", "height", "date", "wxh"):
                return val
            return m.group(0)
        return m.group(0)
    return _TOKEN_RE.sub(_sub, template or "")
