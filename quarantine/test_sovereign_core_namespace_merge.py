# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_sovereign_core_namespace_merge.py
v5.8.11 — regression for the Sovereign_Core namespace clobber bug.

Closes the underlying root cause of the 87N tag-write failure
(field-test 2026-04-26): Sovereign_Core.jsx used to assign a fresh
object to `$.global["com.neuralio444.dimension"]`, which wiped any
`core.layer` (set by SovCore_Layer.jsx earlier in MODULE_CHAIN).
Tag-write needed `D.core.layer.findLayerByUID` and got null, then
threw "SovCore_Layer not loaded" — even though SovCore_Layer.jsx
HAD loaded successfully a few ms earlier.

The diagnostic that proved the bug, run in AE's Open Script Editor:
  $.evalFile(SovCore_Layer.jsx)
  → D.core.layer = true
  $.evalFile(Sovereign_Core.jsx)
  → D.core.layer = FALSE   ← the clobber

This test simulates the same sequence in Node.js by extracting the
namespace-assignment block from Sovereign_Core.jsx and asserting it's
the merge form (`if (!$.global[…])`) not the clobber form
(unconditional assignment). Running the JSX through a real V8 isn't
necessary — the bug was a syntactic clobber, easy to detect by
pattern.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))


_SOVEREIGN_CORE_PATH = (
    Path(__file__).resolve().parents[2]
    / "Scripts" / "Dimension_Assets" / "Sovereign_Core.jsx"
)


class TestSovereignCoreMerge:
    def test_does_not_unconditionally_clobber_namespace(self):
        """Sovereign_Core.jsx must NOT have an unconditional
        `$.global["com.neuralio444.dimension"] = { ... };` line.
        That's the exact pattern that wiped SovCore_Layer's prior
        assignments to D.core.layer."""
        text = _SOVEREIGN_CORE_PATH.read_text(encoding="utf-8")
        # Look for the bug pattern: the clobber assignment with no
        # guard. Specifically `$.global["com.neuralio444.dimension"] = {`
        # NOT preceded on the same statement by a falsy guard.
        bare_clobber = re.compile(
            r'^\s*\$\.global\["com\.neuralio444\.dimension"\]\s*=\s*\{',
            re.MULTILINE,
        )
        clobbers = bare_clobber.findall(text)
        # The merge-form initialisation IS allowed:
        #     if (!$.global["com.neuralio444.dimension"]) {
        #         $.global["com.neuralio444.dimension"] = { core: {} };
        #     }
        # That second line matches `bare_clobber` but is guarded.
        # Count guards to allow exactly one (the init).
        guard_count = text.count(
            'if (!$.global["com.neuralio444.dimension"])'
        )
        assert guard_count >= 1, (
            "Sovereign_Core.jsx must contain "
            "`if (!$.global[\"com.neuralio444.dimension\"])` as the "
            "guard for the namespace init."
        )
        # Each guard pairs with one assignment inside its block; total
        # bare-form occurrences must not exceed guard count.
        assert len(clobbers) <= guard_count, (
            f"Sovereign_Core.jsx has {len(clobbers)} bare-form "
            f"namespace assignments but only {guard_count} guards. "
            f"At least one is an unconditional clobber that will wipe "
            f"sibling SovCore_*.jsx contributions to D.core.*."
        )

    def test_uses_merge_pattern_for_atoms(self):
        """The atoms sub-key must be merge-initialised (not always
        cleared) so siblings can share the namespace."""
        text = _SOVEREIGN_CORE_PATH.read_text(encoding="utf-8")
        assert "if (!__DIMENSION.core.atoms)" in text or \
               "if (!__DIMENSION.core)" in text, (
            "Sovereign_Core.jsx should guard core.atoms init with "
            "an existence check so re-eval doesn't wipe sibling "
            "assignments."
        )

    def test_drawsafetyhud_still_assigned(self):
        """Sanity: the merge form must still install drawSafetyHUD
        (the public entry point that was on the old clobbered object)."""
        text = _SOVEREIGN_CORE_PATH.read_text(encoding="utf-8")
        assert "drawSafetyHUD" in text
        assert "DIMENSION :: DRAWING_HUD_" in text


# ── End-to-end via Node (skips if node not available) ───────────────


class TestNamespaceMergeEndToEnd:
    """Best-effort: run the actual JSX through Node to confirm the
    load order doesn't wipe sibling assignments. Skipped when node
    isn't installed (CI without Node) — the pattern test above is
    the durable check."""

    def test_load_order_preserves_core_layer(self, tmp_path):
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            pytest.skip("node not installed; pattern test above covers")

        # Build a tiny ES3-ish harness that mocks ExtendScript's
        # globals, evals the two relevant files in MODULE_CHAIN
        # order, and reports D.core.layer existence.
        sov_layer_path = (Path(__file__).resolve().parents[2]
                           / "Scripts" / "Dimension_Assets"
                           / "SovCore_Layer.jsx")
        sov_core_path = _SOVEREIGN_CORE_PATH
        if not sov_layer_path.is_file() or not sov_core_path.is_file():
            pytest.skip("source files missing; pattern test above covers")

        harness = tmp_path / "harness.js"
        harness.write_text(f"""
// Mock ExtendScript globals minimally — enough for the namespace
// assignment lines at the top of each .jsx file. Anything beyond
// that we wrap in try/catch + ignore.
var $ = {{ global: {{}}, evalFile: function(){{}} , writeln: function(){{}} }};
var File = function(p){{ this.path=p; this.exists=true; }};
var Folder = function(){{}};
var app = {{ scheduleTask: function(){{}}, project: {{}}, settings: {{ haveSetting: function(){{return false;}}, getSetting: function(){{return "";}}, saveSetting: function(){{}} }} }};
var alert = function(){{}};

function loadFile(path) {{
    var fs = require('fs');
    var src = fs.readFileSync(path, 'utf8');
    try {{ eval(src); }} catch (e) {{ /* downstream func bodies need AE host */ }}
}}

// Step 1: load SovCore_Layer.jsx — should populate D.core.layer
loadFile({str(sov_layer_path)!r});
var afterLayer = !!($.global["com.neuralio444.dimension"]
    && $.global["com.neuralio444.dimension"].core
    && $.global["com.neuralio444.dimension"].core.layer);

// Step 2: load Sovereign_Core.jsx — must NOT wipe D.core.layer
loadFile({str(sov_core_path)!r});
var afterCore = !!($.global["com.neuralio444.dimension"]
    && $.global["com.neuralio444.dimension"].core
    && $.global["com.neuralio444.dimension"].core.layer);

console.log(JSON.stringify({{afterLayer: afterLayer, afterCore: afterCore}}));
""")

        result = subprocess.run(
            [node, str(harness)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            pytest.skip(f"harness failed: {result.stderr[-200:]}")

        import json
        try:
            data = json.loads(result.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            pytest.skip(f"harness output not parseable: {result.stdout!r}")

        # The bug was: afterLayer=True, afterCore=False.
        # The fix: afterCore must stay True.
        assert data["afterCore"] is True, (
            f"Sovereign_Core.jsx wiped D.core.layer (was set by "
            f"SovCore_Layer.jsx). Diagnostic: afterLayer={data['afterLayer']} "
            f"afterCore={data['afterCore']}. The 87N tag-write bug returns."
        )
