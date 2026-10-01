import json
from types import SimpleNamespace
from pathlib import Path

from logic.preset_customs import (
    custom_for_target,
    load_customs,
    save_customs,
    safe_zone_from_custom,
    targets_from_customs,
)
from logic.target_store import TargetStoreManager
from models.target import Target


def test_load_and_bind(tmp_path: Path):
    p = tmp_path / "preset_customs.json"
    p.write_text(json.dumps([
        {
            "id": "custom:dcp1",
            "parent": "builtin:dcp_4k_flat",
            "pack": "broadcast",
            "edges": {"top": 12, "bottom": 12, "left": 12, "right": 12},
        }
    ]))
    row = custom_for_target(SimpleNamespace(id="custom:dcp1"), path=p)
    assert row["pack"] == "broadcast"
    sz = safe_zone_from_custom(row)
    assert sz["top"] == 12
    assert sz["pack"] == "broadcast"


def test_targets_from_customs_skips_factory_and_bad_rows():
    rows = [
        {"id": "builtin:tiktok_video", "label": "Hijack", "w": 1, "h": 1, "category": "social"},
        {"id": "custom:ok", "label": "OK", "w": 1080, "h": 1920, "category": "social", "fps": 29.97},
        {"id": "custom:nosize", "label": "No size", "category": "social"},
        {"not": "a target"},
        None,
    ]
    targets = targets_from_customs(rows)
    assert [t.id for t in targets] == ["custom:ok"]
    t = targets[0]
    assert t.source == "custom"
    assert t.width == 1080
    assert t.height == 1920
    assert t.fps == 29.97
    assert t.category == "social"


def test_all_targets_ingests_preset_customs_json_without_writing_store(tmp_path, monkeypatch):
    customs = tmp_path / "preset_customs.json"
    save_customs([
        {
            "id": "custom:studio_search",
            "label": "Studio Search Custom",
            "category": "social",
            "w": 1080,
            "h": 1920,
            "pack": "chrome",
            "edges": {"top": 13, "bottom": 21.9, "left": 5, "right": 13},
        }
    ], path=customs)
    monkeypatch.setattr("logic.preset_customs._DEFAULT", customs)
    store_path = str(tmp_path / "dimension_targets.json")
    tsm = TargetStoreManager(store_path=store_path)
    hit = tsm.by_id("custom:studio_search")
    assert isinstance(hit, Target)
    assert hit.width == 1080
    assert hit.source == "custom"
    assert hit.metadata.get("pack") == "chrome"
    dumped = json.loads(Path(store_path).read_text())
    assert all(c.get("id") != "custom:studio_search" for c in dumped.get("customs", []))
    # factory still present
    assert tsm.by_id("builtin:tiktok_video") is not None or any(
        t.id.endswith("tiktok_video") or "tiktok" in t.id for t in tsm.all_targets()
    )


def test_all_targets_does_not_let_disk_customs_overwrite_factory(tmp_path, monkeypatch):
    builtins = TargetStoreManager(store_path=str(tmp_path / "empty.json")).all_targets()
    factory = builtins[0]
    customs = tmp_path / "preset_customs.json"
    save_customs([
        {
            "id": factory.id,
            "label": "Hijacked factory",
            "w": 16,
            "h": 16,
            "category": "custom_signage",
        }
    ], path=customs)
    monkeypatch.setattr("logic.preset_customs._DEFAULT", customs)
    tsm = TargetStoreManager(store_path=str(tmp_path / "store.json"))
    resolved = tsm.by_id(factory.id)
    assert resolved is not None
    assert resolved.label == factory.label
    assert resolved.width == factory.width
    assert resolved.source == "builtin"
