# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_prefs_cli.py — the `set-many` batch-write op (issue #474).

license.js's activation flow used to chain 9 sequential `prefs_cli.py
set` spawns (plus a 10th for License.init()) to write the license
fields after a successful Gumroad activation — ~53s of dead air
against the frozen binary's ~5.3s/spawn cost (#408), the first thing a
paying customer waits through. `set-many` collapses that to one spawn,
one `preferences.save()`.

Run as real subprocesses (matches how `BB.runPrefsCli` actually
invokes this file via `child_process.execFile`) rather than importing
`prefs_cli.main()` in-process — the CLI's argv/stdout/exit-code
contract is the thing under test, and it is small enough that
subprocess overhead is not a concern here (unlike the pipeline
engine's own perf-sensitive paths).

`$HOME` is redirected to a temp dir for every subprocess so this test
never touches the real ~/Library/Application Support/NeuralIO_Dimension/state.json
(`state_json_path()` resolves via `Path.home()`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREFS_CLI = _REPO_ROOT / "python" / "prefs_cli.py"


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _run(*args: str, home: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, str(_PREFS_CLI), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
    )


def _get(home: Path) -> dict:
    r = _run("get", home=home)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class TestSetMany:
    def test_writes_multiple_keys_in_one_call(self, isolated_home: Path) -> None:
        payload = {
            "license_key": "ABC123",
            "license_status": "trial",
            "license_product": "trial",
            "license_activated_at": "2026-09-06T00:00:00.000Z",
        }
        r = _run("set-many", json.dumps(payload), home=isolated_home)
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout) == payload

        state = _get(isolated_home)
        for key, value in payload.items():
            assert state[key] == value

    def test_one_spawn_one_save_call(self, isolated_home: Path, monkeypatch) -> None:
        """The actual perf fix: N keys must cost exactly one save(), not
        one per key. Proven by counting save() calls inside the single
        process this test spawns."""
        import logic.preferences_state as ps

        calls: List[int] = []
        original_save = ps._Preferences.save

        def counting_save(self):
            calls.append(1)
            return original_save(self)

        monkeypatch.setattr(ps._Preferences, "save", counting_save)
        monkeypatch.setattr(ps, "state_json_path", lambda: isolated_home / "state.json")
        monkeypatch.setattr(ps.preferences, "_loaded", False)

        import prefs_cli

        payload = {"license_key": "X", "license_status": "trial", "license_email": "a@b.com"}
        monkeypatch.setattr(sys, "argv", ["prefs_cli.py", "set-many", json.dumps(payload)])
        prefs_cli.main()

        assert len(calls) == 1, f"expected exactly one save() for a 3-key batch, got {len(calls)}"

    def test_coerces_values_like_single_set(self, isolated_home: Path) -> None:
        payload = {"license_dev_bypass": "true", "onboarding_complete": "false"}
        r = _run("set-many", json.dumps(payload), home=isolated_home)
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["license_dev_bypass"] is True
        assert out["onboarding_complete"] is False

        state = _get(isolated_home)
        assert state["license_dev_bypass"] is True
        assert state["onboarding_complete"] is False

    def test_unknown_key_rejects_the_whole_batch(self, isolated_home: Path) -> None:
        payload = {"license_key": "ABC", "not_a_real_pref": "x"}
        r = _run("set-many", json.dumps(payload), home=isolated_home)
        assert r.returncode != 0
        assert "not_a_real_pref" in r.stderr

        # Nothing from the batch was persisted — a partially-applied
        # batch would be worse than the sequential writes it replaces.
        state = _get(isolated_home)
        assert state["license_key"] == ""

    def test_invalid_json_rejected(self, isolated_home: Path) -> None:
        r = _run("set-many", "{not valid json", home=isolated_home)
        assert r.returncode != 0
        assert "invalid JSON" in r.stderr

    def test_non_object_json_rejected(self, isolated_home: Path) -> None:
        r = _run("set-many", json.dumps(["a", "list", "not", "a", "dict"]), home=isolated_home)
        assert r.returncode != 0
        assert "JSON object" in r.stderr

    def test_missing_argument(self, isolated_home: Path) -> None:
        r = _run("set-many", home=isolated_home)
        assert r.returncode != 0
        assert "Usage" in r.stderr

    def test_does_not_disturb_keys_outside_the_batch(self, isolated_home: Path) -> None:
        """Mirrors save()'s read-modify-write contract: a batch touching
        3 keys must not reset the other ~10 to their defaults."""
        _run("set", "enable_soe", "false", home=isolated_home)
        r = _run("set-many", json.dumps({"license_key": "XYZ"}), home=isolated_home)
        assert r.returncode == 0, r.stderr

        state = _get(isolated_home)
        assert state["license_key"] == "XYZ"
        assert state["enable_soe"] is False
