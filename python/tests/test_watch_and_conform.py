# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_watch_and_conform.py
Unit tests for the watch_and_conform file watcher sentinel.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

from tools.watch_and_conform import (
    is_ignored,
    get_file_snapshot,
    resolve_targeted_tests,
    trigger_cef_reload,
    run_watcher_cycle,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]

# One representative changed-file path per resolve_targeted_tests() branch,
# so every glob-free (concrete) target the table can produce gets exercised.
_REPRESENTATIVE_CHANGED_FILES = [
    "Scripts/Dimension_Assets/Babysitter_src/70_pump.jsx",
    "Scripts/Dimension_Assets/Auditor.jsx",
    "python/core/telemetry.py",
    "python/core/gravity.py",
    "python/core/occlusion_engine.py",
    "python/core/kinematics.py",
    "python/core/surveyor.py",
    "python/core/dag_duplication.py",
    "python/tools/ae_eval.py",
    "python/tools/verify_math_and_pillars.py",
    "cep/js/main.js",
    "python/models/scrape_manifest.py",
    "README.md",  # the fallback branch
]


def test_resolve_targeted_tests_never_references_a_missing_test_file():
    """Every concrete (non-glob) pytest target this table can produce must
    exist on disk. A stale filename here doesn't just fail to run the
    tests it names -- pytest exits 4 ("no tests ran") for the WHOLE
    invocation when any given path doesn't exist, so one stale entry
    silently kills every other real test in the same targeted slice."""
    all_targets: set[str] = set()
    for changed_file in _REPRESENTATIVE_CHANGED_FILES:
        targets, _run_node, _run_baby = resolve_targeted_tests({changed_file})
        all_targets.update(targets)

    missing = [
        t for t in sorted(all_targets)
        if "*" not in t and not (_REPO_ROOT / t).is_file()
    ]
    assert missing == [], (
        f"resolve_targeted_tests() references test file(s) that don't exist: {missing}"
    )


def test_is_ignored():
    assert is_ignored("python/core/__pycache__/scale.cpython-313.pyc") is True
    assert is_ignored(".pytest_cache/v/cache") is True
    assert is_ignored(".git/HEAD") is True
    assert is_ignored(".dimension_inbox/job.json") is True
    assert is_ignored("python/core/scale_engine.py") is False
    assert is_ignored("cep/js/main.js") is False
    assert is_ignored("Scripts/Dimension_Assets/Babysitter.jsx") is False


def test_get_file_snapshot():
    snapshot = get_file_snapshot()
    assert isinstance(snapshot, dict)
    assert len(snapshot) > 0
    # verify scale_engine.py is captured
    matches = [k for k in snapshot.keys() if "scale_engine.py" in k]
    assert len(matches) > 0


def test_resolve_targeted_tests():
    # 1. ScaleEngine change
    targets, run_node, run_baby = resolve_targeted_tests({"python/core/scale_engine.py"})
    assert any("test_scale_engine" in t for t in targets)
    assert run_node is False
    assert run_baby is False

    # 2. Babysitter_src change
    targets, run_node, run_baby = resolve_targeted_tests({"Scripts/Dimension_Assets/Babysitter_src/70_pump.jsx"})
    assert "python/tests/test_cep_jsx_bundle_sync.py" in targets
    assert run_baby is True

    # 3. CEP change
    targets, run_node, run_baby = resolve_targeted_tests({"cep/js/fast_tag_strip.js"})
    assert run_node is True

    # 4. Telemetry change
    targets, run_node, run_baby = resolve_targeted_tests({"python/core/telemetry.py"})
    assert "python/tests/test_diagnostic_telemetry_and_visualizer.py" in targets


@patch("urllib.request.urlopen")
def test_trigger_cef_reload_success(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'[{"title": "Dimension", "webSocketDebuggerUrl": "ws://..."}]'
    mock_urlopen.return_value.__enter__.return_value = mock_resp
    assert trigger_cef_reload(port=8088) is True


@patch("subprocess.run")
def test_run_watcher_cycle(mock_subproc):
    mock_subproc.return_value.returncode = 0
    res = run_watcher_cycle(
        changed_files={"python/core/scale_engine.py"},
        venv_python="python",
        enable_cef=False,
    )
    assert res is True
    mock_subproc.assert_called()
