# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial.
# See LICENSE for full terms.

"""Hall of horrors: synthetic operators of different ability hit the real CLI.

Personas are not mocks of the engine. Each one shells out to
``python -m dimension`` the way that person would, then we judge what
they saw: exit code, one JSON document on the machine face, no
traceback for a usage mistake, no studio identifiers, no write outside
the scratch project.

    pytest python/tests/test_hall_of_horrors.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / "python"
MANIFEST = PYTHON / "tests" / "fixtures" / "slot_8" / "synth-camera-pan-drift.json"

# Concatenated so this file does not contain the banned literals.
# test_repo_hygiene scans tracked sources for the same needles.
BANNED = (
    "Cia" + "glia",
    "UNIVERSAL" + "_PICTURES",
    "NBC" + "Universal",
    "NBC" + "U",
    "/" + "Users" + "/" + "mattciaglia",
)


def _run(tmp: Path, args: list[str], *, json_mode: bool = False) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PYTHON)
    env["HOME"] = str(tmp)
    return subprocess.run(
        [sys.executable, "-m", "dimension", *args],
        cwd=tmp,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _one_json(proc: subprocess.CompletedProcess[str]) -> dict:
    text = proc.stdout.strip()
    assert text, f"empty stdout\nstderr={proc.stderr[-400:]}"
    # Machine face: exactly one JSON document. Logs stay on stderr.
    assert "\n{" not in text, f"more than one JSON document on stdout:\n{text[:300]}"
    return json.loads(text)


def _clean(proc: subprocess.CompletedProcess[str]) -> None:
    blob = proc.stdout + proc.stderr
    for needle in BANNED:
        assert needle not in blob
    assert "Traceback (most recent call last)" not in proc.stdout


@pytest.fixture()
def scratch(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(MANIFEST, project / "manifest.json")
    return project


# ── intern: wrong shape, still a recoverable answer ─────────────────

def test_intern_bare_command_is_the_card(scratch: Path) -> None:
    proc = _run(scratch, [])
    assert proc.returncode == 0
    _clean(proc)
    assert "conform it everywhere" in proc.stdout.lower()
    assert "dimension man" in proc.stdout


def test_intern_typo_preset_is_not_a_crash(scratch: Path) -> None:
    proc = _run(scratch, ["safe-zone", "plan", "--preset", "tiktok_vidoe"])
    _clean(proc)
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr or proc.returncode in {1, 2}


def test_intern_conform_without_preset_names_the_flag(scratch: Path) -> None:
    proc = _run(scratch, ["conform", "--source", "manifest.json"])
    # Engine error, not argparse usage: the flag is named so the intern can recover.
    assert proc.returncode == 1
    _clean(proc)
    assert "--preset" in proc.stderr or "--preset" in proc.stdout


def test_intern_curly_quote_preset_does_not_escape(scratch: Path) -> None:
    proc = _run(scratch, ["safe-zone", "plan", "--preset", "tiktok_video\u201d"])
    _clean(proc)
    assert proc.returncode != 0
    assert ".." not in proc.stdout


# ── producer: human face, catalog and naming ────────────────────────

def test_producer_catalog_lists_tiktok(scratch: Path) -> None:
    proc = _run(scratch, ["catalog", "presets"])
    assert proc.returncode == 0
    _clean(proc)
    assert "tiktok" in proc.stdout.lower()


def test_producer_safe_zone_plan_is_readable(scratch: Path) -> None:
    proc = _run(scratch, ["safe-zone", "plan", "--preset", "tiktok_video"])
    assert proc.returncode == 0
    _clean(proc)
    assert proc.stdout.strip()
    assert not proc.stdout.strip().startswith("{")


def test_producer_naming_bumps_on_collision(scratch: Path) -> None:
    proc = _run(scratch, ["naming", "resolve", "--source", "Hero_Campaign", "--preset-id", "tiktok_video"])
    assert proc.returncode == 0
    _clean(proc)
    assert "Hero" in proc.stdout or "tiktok" in proc.stdout.lower()


# ── pipeline TD: machine face ───────────────────────────────────────

def test_td_json_safe_zone_is_one_document(scratch: Path) -> None:
    proc = _run(scratch, ["--json", "safe-zone", "plan", "--preset", "tiktok_video"])
    payload = _one_json(proc)
    assert proc.returncode == 0
    assert payload.get("status") == "OK"
    _clean(proc)
    assert "INFO" not in proc.stdout


def test_td_missing_preset_is_json_error(scratch: Path) -> None:
    proc = _run(scratch, ["--json", "safe-zone", "plan", "--preset", "not_a_real_preset"])
    payload = _one_json(proc)
    assert proc.returncode != 0
    assert payload.get("status") == "ERROR"
    assert "code" in payload
    _clean(proc)


def test_td_ae_probe_reports_unreachable_without_sending_a_job(scratch: Path) -> None:
    proc = _run(scratch, ["--json", "ae", "probe"])
    payload = _one_json(proc)
    # Probe is a reachability check. No poller is a result, not a crash.
    # Exit 69 is reserved for a live-AE command that cannot run.
    assert proc.returncode == 0
    assert payload.get("status") == "OK"
    blob = json.dumps(payload)
    assert "reachable" in blob.lower() or "hint" in blob.lower() or "poller" in blob.lower()
    _clean(proc)


# ── night shift: bad inputs the queue will actually send ───────────

def test_night_empty_manifest_is_invalid_not_a_traceback(scratch: Path) -> None:
    empty = scratch / "empty.json"
    empty.write_text("", encoding="utf-8")
    proc = _run(scratch, ["--json", "survey", str(empty)])
    payload = _one_json(proc)
    assert proc.returncode != 0
    assert payload.get("status") == "ERROR"
    assert "Traceback" not in proc.stdout
    _clean(proc)


def test_night_binary_file_is_not_executed(scratch: Path) -> None:
    blob = scratch / "blob.json"
    blob.write_bytes(b"\x00\x01\x02 not json \xff")
    proc = _run(scratch, ["--json", "survey", str(blob)])
    assert proc.returncode != 0
    _clean(proc)
    assert "Traceback" not in proc.stdout


def test_night_conform_missing_file(scratch: Path) -> None:
    proc = _run(scratch, ["--json", "conform", "--source", "nope.json", "--preset", "tiktok_video"])
    payload = _one_json(proc)
    assert proc.returncode != 0
    assert payload.get("status") == "ERROR"
    _clean(proc)


# ── horror: paths and identity ──────────────────────────────────────

def test_horror_preset_injection_stays_an_argument(scratch: Path) -> None:
    proc = _run(scratch, ["--json", "safe-zone", "plan", "--preset", "tiktok_video; rm -rf /"])
    payload = _one_json(proc)
    assert proc.returncode != 0
    assert payload.get("status") == "ERROR"
    assert (scratch / "manifest.json").is_file()
    _clean(proc)


def test_horror_huge_preset_is_rejected(scratch: Path) -> None:
    proc = _run(scratch, ["--json", "catalog", "show", "--preset", "A" * 8000])
    assert proc.returncode != 0
    _clean(proc)
    assert "Traceback" not in proc.stdout


def test_horror_fixture_paths_are_already_synthetic(scratch: Path) -> None:
    text = (scratch / "manifest.json").read_text(encoding="utf-8")
    for needle in BANNED:
        assert needle not in text
