# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_cep_js_syntax.py — panel JS syntax gate.

Runs `node --check` over every cep/js/*.js file. CEP panel JavaScript
can't run under pytest, but a parse error in any of these files kills
every panel button silently at load time — a stray `};` in
backend_bridge.js did exactly that on 2026-07-06 and nothing caught it.
This gate makes that class of breakage a test failure.

Skips gracefully when `node` is not on PATH (CI images without Node).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_CEP_JS_DIR = _REPO / "cep" / "js"

_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    _NODE is None, reason="node is not on PATH — cannot syntax-check panel JS"
)


def _js_files() -> list[Path]:
    if not _CEP_JS_DIR.is_dir():
        return []
    return sorted(_CEP_JS_DIR.glob("*.js"))


def test_cep_js_dir_has_files():
    """Guard against the glob silently matching nothing (renamed dir etc.)."""
    assert _js_files(), f"no .js files found under {_CEP_JS_DIR}"


@pytest.mark.parametrize(
    "js_file",
    _js_files(),
    ids=lambda p: p.name,
)
def test_panel_js_parses(js_file: Path):
    proc = subprocess.run(
        [_NODE, "--check", str(js_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"node --check failed for {js_file.name}:\n{proc.stderr}"
    )
