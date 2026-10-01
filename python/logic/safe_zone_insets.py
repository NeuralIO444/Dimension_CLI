# (c) 2026 NeuralIO 444
"""Numeric safe-zone insets + M0 pack fallback + M6 custom bind."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from models.target import Target

_REPO_YAML = Path(__file__).resolve().parents[2] / "config" / "safe_zone_insets.yaml"


def _repo_yaml_path() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "config" / "safe_zone_insets.yaml"
    return _REPO_YAML


@lru_cache(maxsize=1)
def load_inset_catalog(path: Optional[Path] = None) -> dict:
    p = path or _repo_yaml_path()
    if not p.is_file():
        return {"version": 1, "profile": "ui_chrome", "presets": {}, "aliases": {}}
    try:
        import yaml
    except ImportError:
        return {"version": 1, "profile": "ui_chrome", "presets": {}, "aliases": {}}
    data = yaml.safe_load(p.read_text()) or {}
    if not isinstance(data, dict):
        return {"version": 1, "profile": "ui_chrome", "presets": {}, "aliases": {}}
    data.setdefault("profile", "ui_chrome")
    data.setdefault("presets", {})
    data.setdefault("aliases", {})
    return data


def reset_inset_catalog_cache() -> None:
    load_inset_catalog.cache_clear()


def _bare_id(target: Any) -> str:
    tid = str(getattr(target, "id", "") or "")
    if ":" in tid:
        tid = tid.split(":", 1)[1]
    return tid


def _instagram_shape_key(bare: str) -> Optional[str]:
    if not bare.startswith("instagram"):
        return None
    if "story" in bare:
        return "instagram_story"
    if "reel" in bare:
        return "instagram_reels"
    return "instagram_post"


def _candidate_keys(target: Any) -> list:
    keys = []
    bare = _bare_id(target)
    sub = str(getattr(target, "subcategory", "") or "")
    if bare:
        keys.append(bare)
        shape = _instagram_shape_key(bare)
        if shape:
            keys.append(shape)
        if bare.startswith("youtube") and "short" in bare:
            keys.append("youtube_shorts")
        if bare.startswith("tiktok"):
            keys.append("tiktok")
    if sub:
        keys.append(sub)
    out, seen = [], set()
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def spec_for_target(target: Any, catalog: Optional[Mapping] = None) -> Optional[dict]:
    meta = getattr(target, "metadata", None) or {}
    override = meta.get("safe_zone") if isinstance(meta, dict) else None
    if isinstance(override, dict) and (
        "top" in override or "ui_chrome" in override or "title_safe" in override
        or override.get("pack") in ("broadcast", "chrome", "none", "ooh")
    ):
        return override
    try:
        from logic.preset_customs import custom_for_target, safe_zone_from_custom
        bound = safe_zone_from_custom(custom_for_target(target) or {})
        if bound:
            return bound
    except Exception:
        pass
    field = getattr(target, "safe_zones", None)
    if isinstance(field, dict) and (
        "top" in field or "ui_chrome" in field or "title_safe" in field
    ):
        return field
    cat = catalog or load_inset_catalog()
    presets = cat.get("presets") or {}
    aliases = cat.get("aliases") or {}
    for key in _candidate_keys(target):
        if key in presets:
            return presets[key]
        alias = aliases.get(key)
        if alias and alias in presets:
            return presets[alias]
    return None


def _edges_from_spec(spec: Mapping, profile: str) -> Optional[dict]:
    if "top" in spec and "bottom" in spec:
        return spec
    nested = spec.get(profile) or spec.get("ui_chrome") or spec.get("title_safe")
    if isinstance(nested, dict) and "top" in nested:
        return nested
    return None


def synthesize_inset_mask(width: int, height: int, edges: Mapping[str, Any]):
    import numpy as np
    unit = str(edges.get("unit") or "percent").lower()
    top = float(edges.get("top") or 0)
    bottom = float(edges.get("bottom") or 0)
    left = float(edges.get("left") or 0)
    right = float(edges.get("right") or 0)
    if unit != "px":
        top = height * top / 100.0
        bottom = height * bottom / 100.0
        left = width * left / 100.0
        right = width * right / 100.0
    t = max(0, min(height, int(round(top))))
    b = max(0, min(height, int(round(bottom))))
    l = max(0, min(width, int(round(left))))
    r = max(0, min(width, int(round(right))))
    mask = np.full((height, width), 255, dtype=np.uint8)
    if t:
        mask[:t, :] = 0
    if b:
        mask[height - b :, :] = 0
    if l:
        mask[:, :l] = 0
    if r:
        mask[:, width - r :] = 0
    return mask


def resolve_inset_mask(target: "Target", profile: Optional[str] = None):
    cat = load_inset_catalog()
    spec = spec_for_target(target, cat)
    w = int(getattr(target, "width", 0) or 0)
    h = int(getattr(target, "height", 0) or 0)
    if w <= 0 or h <= 0:
        return None
    if spec:
        if spec.get("pack") == "none":
            return None
        if spec.get("pack") == "broadcast" and "top" not in spec:
            from logic.safe_zone_packs import synthesize_broadcast_mask
            return synthesize_broadcast_mask(
                w, h,
                action_pct=float(spec.get("action_pct") or 10),
                title_pct=float(spec.get("title_pct") or 20),
            )
        use = profile or cat.get("profile") or "ui_chrome"
        edges = _edges_from_spec(spec, use)
        if edges:
            return synthesize_inset_mask(w, h, edges)
        if spec.get("pack") == "broadcast":
            from logic.safe_zone_packs import synthesize_broadcast_mask
            return synthesize_broadcast_mask(w, h)
    from logic.safe_zone_packs import resolve_pack_mask
    return resolve_pack_mask(target)
