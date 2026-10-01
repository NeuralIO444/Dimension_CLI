# (c) 2026 NeuralIO 444
"""M5 — artist customs on disk so Execute can see Edit-dialog edges."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional

from pydantic import ValidationError

from models.target import Channel, Target, TargetCategory

_DEFAULT = Path.home() / ".dimension" / "preset_customs.json"

_VALID_CATEGORIES = set(TargetCategory.__args__)  # type: ignore[attr-defined]
_VALID_CHANNELS = set(Channel.__args__)  # type: ignore[attr-defined]


def customs_path() -> Path:
    return _DEFAULT


def load_customs(path: Optional[Path] = None) -> list:
    p = path or _DEFAULT
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        data = data.get("customs") or data.get("rows") or []
    return data if isinstance(data, list) else []


def save_customs(rows: list, path: Optional[Path] = None) -> Path:
    p = path or _DEFAULT
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(list(rows), indent=2))
    return p


def custom_for_target(target: Any, path: Optional[Path] = None) -> Optional[dict]:
    tid = str(getattr(target, "id", "") or "")
    rows = load_customs(path)
    for row in rows:
        if str(row.get("id") or "") == tid:
            return row
        if str(row.get("parent") or "") == tid:
            return row
    return None


def safe_zone_from_custom(row: dict) -> Optional[dict]:
    if not row:
        return None
    if isinstance(row.get("safe_zone"), dict):
        sz = dict(row["safe_zone"])
    elif isinstance(row.get("edges"), dict):
        sz = dict(row["edges"])
        sz["unit"] = sz.get("unit") or "percent"
    else:
        return None
    if row.get("pack"):
        sz.setdefault("pack", row["pack"])
    return sz


def _as_positive_int(value: Any) -> Optional[int]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _as_optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def targets_from_customs(rows: Optional[list] = None) -> List[Target]:
    """CEP / ~/.dimension/preset_customs.json rows → Target objects.

    Factory builtins are never produced here (ids must start with
    ``custom:``). Invalid rows are skipped, not raised — a bad artist
    file must not take down dump-data or Studio search.
    """
    out: List[Target] = []
    seen = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        tid = str(row.get("id") or "").strip()
        if not tid.startswith("custom:") or tid in seen:
            continue
        w = _as_positive_int(row.get("w") if row.get("w") is not None else row.get("width"))
        h = _as_positive_int(row.get("h") if row.get("h") is not None else row.get("height"))
        if w is None or h is None:
            continue
        label = str(row.get("label") or row.get("name") or tid).strip() or tid
        cat = row.get("category") or "custom_signage"
        if cat not in _VALID_CATEGORIES:
            cat = "custom_signage"
        sub = str(row.get("subcategory") or "user").strip() or "user"
        channel = row.get("channel")
        if channel not in _VALID_CHANNELS:
            channel = None
        ratio = w / h
        aspect_label = str(row.get("aspect_label") or "").strip() or (
            f"{ratio:.2f}:1" if ratio >= 1 else f"1:{(1 / ratio):.2f}"
        )
        metadata = dict(row.get("metadata") or {})
        if row.get("pack"):
            metadata.setdefault("pack", row["pack"])
        if row.get("parent"):
            metadata.setdefault("parent", row["parent"])
        sz = safe_zone_from_custom(row)
        if sz:
            metadata.setdefault("safe_zone", sz)
        try:
            target = Target.make(
                id=tid,
                label=label,
                category=cat,
                subcategory=sub,
                width=w,
                height=h,
                aspect_label=aspect_label,
                source="custom",
                channel=channel,
                duration=_as_optional_float(row.get("duration")),
                fps=_as_optional_float(row.get("fps")),
                safe_zones=sz,
                metadata=metadata,
            )
        except (ValidationError, ValueError, TypeError):
            continue
        seen.add(tid)
        out.append(target)
    return out
