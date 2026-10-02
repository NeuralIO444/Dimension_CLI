# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_babysitter_pure_helpers.py — Track A Phase A1 unit tests.

Verifies pure helpers extracted into `Scripts/Dimension_Assets/Babysitter_src/`
(`10_validation.jsx`, `40_io.jsx`, `65_abort.jsx`) by running them directly in Node.js.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BABYSITTER_PATH = _REPO_ROOT / "Scripts" / "Dimension_Assets" / "Babysitter.jsx"

NODE_BIN = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE_BIN is None, reason="node not found on PATH")


def _eval_babysitter_js(expression: str) -> any:
    """Execute an expression in Node with Babysitter loaded and return parsed JSON."""
    src = _BABYSITTER_PATH.read_text(encoding="utf-8")
    script = f"""
    var $ = {{ global: {{}} }};
    var app = {{ project: {{ items: [], numItems: 0 }} }};
    var CompItem = function() {{}};
    var FolderItem = function() {{}};

    {src}

    var Babysitter = $.global.Babysitter;
    var result = ({expression});
    console.log(JSON.stringify(result));
    """
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tmp:
        tmp.write(script)
        tmp_name = tmp.name

    try:
        proc = subprocess.run(
            [NODE_BIN, tmp_name],
            capture_output=True,
            text=True,
            check=True,
        )
        output = proc.stdout.strip()
        return json.loads(output)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


class TestValidationHelpers:
    def test_is_finite_val_scalars(self):
        assert _eval_babysitter_js("Babysitter._isFiniteVal(42)") is True
        assert _eval_babysitter_js("Babysitter._isFiniteVal(0.001)") is True
        assert _eval_babysitter_js("Babysitter._isFiniteVal(-999.5)") is True
        assert _eval_babysitter_js("Babysitter._isFiniteVal(NaN)") is False
        assert _eval_babysitter_js("Babysitter._isFiniteVal(Infinity)") is False
        assert _eval_babysitter_js("Babysitter._isFiniteVal(-Infinity)") is False
        assert _eval_babysitter_js("Babysitter._isFiniteVal(null)") is False
        assert _eval_babysitter_js("Babysitter._isFiniteVal(undefined)") is False

    def test_is_finite_val_arrays(self):
        assert _eval_babysitter_js("Babysitter._isFiniteVal([100, 200, 300])") is True
        assert _eval_babysitter_js("Babysitter._isFiniteVal([100, NaN, 300])") is False
        assert _eval_babysitter_js("Babysitter._isFiniteVal([Infinity, 200])") is False
        assert _eval_babysitter_js("Babysitter._isFiniteVal([])") is True

    def test_copy_array(self):
        res = _eval_babysitter_js("Babysitter._copyArray([1, 2, 3])")
        assert res == [1, 2, 3]

    def test_arrays_equal(self):
        assert _eval_babysitter_js("Babysitter._arraysEqual([1, 2, 3], [1, 2, 3], 0.01)") is True
        assert _eval_babysitter_js("Babysitter._arraysEqual([1, 2], [1, 2, 3], 0.01)") is False
        assert _eval_babysitter_js("Babysitter._arraysEqual([1, 2, 3], [1, 4, 3], 0.01)") is False

    def test_sanitize_value_clean(self):
        res = _eval_babysitter_js("Babysitter._sanitizeValue([100, 200], 'position', 1920, 1080)")
        assert res == [100, 200]

    def test_sanitize_value_rejects_nan(self):
        # NaN in value is firewalled: returns null so bad data never reaches AE
        res = _eval_babysitter_js("Babysitter._sanitizeValue([NaN, 200], 'position', 1920, 1080)")
        assert res is None

    def test_sanitize_value_clamps_scale_and_opacity(self):
        scale_res = _eval_babysitter_js("Babysitter._sanitizeValue([0.0001, 20000], 'scale')")
        assert scale_res == [0.01, 10000.0]

        opacity_neg = _eval_babysitter_js("Babysitter._sanitizeValue(-10, 'opacity')")
        assert opacity_neg == 0
        opacity_over = _eval_babysitter_js("Babysitter._sanitizeValue(150, 'opacity')")
        assert opacity_over == 100


class TestSessionAndPathHelpers:
    def test_generate_session_uuid_shape(self):
        uuid_val = _eval_babysitter_js("Babysitter._generateSessionUUID()")
        uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        assert re.match(uuid_pattern, uuid_val), f"Generated UUID '{uuid_val}' does not match RFC 4122 v4"

    def test_abort_request_path_resolution(self):
        path = _eval_babysitter_js("Babysitter._abortRequestPath('/path/to/project/.dimension/transfer_status.log')")
        assert path.endswith("inject_abort_request.json")
        assert ".dimension/inject_abort_request.json" in path.replace("\\", "/")

    def test_abort_request_path_null_guard(self):
        assert _eval_babysitter_js("Babysitter._abortRequestPath(null)") is None
        assert _eval_babysitter_js("Babysitter._abortRequestPath('')") is None
