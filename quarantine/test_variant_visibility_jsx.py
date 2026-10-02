# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_variant_visibility_jsx.py — PR-V3.

Exercises `Babysitter._processLayerVisibility` in Node with a fake
`aeLayer`, following `test_babysitter_pure_helpers.py`'s harness.

JSX cannot run under pytest against real After Effects, so this covers
what is testable without AE — the decision logic — and the manual AE QA
checklist in the PR body covers what is not. The distinction matters
here: CLAUDE.md's anti-pattern list is explicit that synthetic fixtures
must not be trusted to prove a JSX↔Python contract, and this file does
not claim to. It proves that GIVEN a chunk layer shape, the writer sets
the switch it should and leaves alone the one it shouldn't.

The behaviour most worth pinning is the third test: a layer with no
`conformed_enabled` field must have its video switch left completely
untouched. Every layer in a comp with no `variant:` directives is in
that state, so a writer that defaulted to `true` would silently re-enable
every layer an artist had manually switched off — turning a conform into
a destructive edit of their comp.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BABYSITTER_PATH = _REPO_ROOT / "Scripts" / "Dimension_Assets" / "Babysitter.jsx"

NODE_BIN = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE_BIN is None, reason="node not found on PATH")


def _run_visibility(c_layer: dict, layer_initial: dict) -> dict:
    """Call _processLayerVisibility against a fake AE layer.

    Returns the fake layer's observable state afterwards, plus whether
    the `enabled` setter was invoked at all — "left untouched" and "set
    to the value it already had" are different outcomes and only the
    setter count distinguishes them.
    """
    src = _BABYSITTER_PATH.read_text(encoding="utf-8")
    script = f"""
    var $ = {{ global: {{}} }};
    var app = {{ project: {{ items: [], numItems: 0 }} }};
    var CompItem = function() {{}};
    var FolderItem = function() {{}};

    {src}

    var Babysitter = $.global.Babysitter;

    var initial = {json.dumps(layer_initial)};
    var setCount = 0;
    var enabledValue = initial.enabled;
    var lockedValue = initial.locked;

    var aeLayer = {{
        get enabled() {{ return enabledValue; }},
        set enabled(v) {{ setCount++; enabledValue = v; }},
        get locked() {{ return lockedValue; }},
        set locked(v) {{ lockedValue = v; }}
    }};

    Babysitter._state = {{ ctx: {{ logPath: null }} }};
    Babysitter._writeLog = function() {{}};

    Babysitter._processLayerVisibility({json.dumps(c_layer)}, aeLayer);

    console.log(JSON.stringify({{
        enabled: enabledValue,
        locked: lockedValue,
        setCount: setCount,
        hiddenCount: Babysitter._state.variantHiddenCount || 0,
        shownCount: Babysitter._state.variantShownCount || 0
    }}));
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(script)
        tmp_name = tmp.name
    try:
        proc = subprocess.run(
            [NODE_BIN, tmp_name], capture_output=True, text=True, check=True
        )
        return json.loads(proc.stdout.strip())
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


class TestVisibilityWriter:
    def test_hidden_layer_is_switched_off(self):
        out = _run_visibility(
            {"name": "YOU WANT", "conformed_enabled": False},
            {"enabled": True, "locked": False},
        )
        assert out["enabled"] is False
        assert out["setCount"] == 1
        assert out["hiddenCount"] == 1

    def test_shown_layer_is_switched_on(self):
        out = _run_visibility(
            {"name": "YOU WANT", "conformed_enabled": True},
            {"enabled": False, "locked": False},
        )
        assert out["enabled"] is True
        assert out["shownCount"] == 1

    def test_layer_without_the_field_is_never_touched(self):
        """The behaviour that keeps a conform non-destructive.

        Every layer in a comp with no `variant:` directives arrives here
        with no `conformed_enabled`. A writer that defaulted to `true`
        would re-enable every layer the artist had manually switched off
        — silently editing their comp rather than conforming it.
        """
        out = _run_visibility(
            {"name": "plain layer"}, {"enabled": False, "locked": False}
        )
        assert out["setCount"] == 0
        assert out["enabled"] is False

    @pytest.mark.parametrize("junk", [None, "true", 1, 0, "", "false"])
    def test_non_boolean_values_are_ignored(self, junk):
        """Only a real boolean counts. A truthy string from a
        hand-edited manifest must not flip a switch."""
        out = _run_visibility(
            {"name": "x", "conformed_enabled": junk},
            {"enabled": True, "locked": False},
        )
        assert out["setCount"] == 0

    def test_skip_inject_layers_are_left_alone(self):
        out = _run_visibility(
            {
                "name": "x",
                "conformed_enabled": False,
                "conformed_transforms": {"skip_inject": True},
            },
            {"enabled": True, "locked": False},
        )
        assert out["setCount"] == 0
        assert out["enabled"] is True

    def test_locked_layer_is_unlocked_written_and_relocked(self):
        """Mirrors `_processLayerTransforms`' lock dance. Leaving a layer
        unlocked afterwards would be a silent, permanent change to the
        artist's comp."""
        out = _run_visibility(
            {"name": "x", "conformed_enabled": False},
            {"enabled": True, "locked": True},
        )
        assert out["enabled"] is False
        assert out["locked"] is True

    def test_counters_accumulate_for_the_run_summary(self):
        out = _run_visibility(
            {"name": "x", "conformed_enabled": False},
            {"enabled": True, "locked": False},
        )
        assert out["hiddenCount"] == 1
        assert out["shownCount"] == 0


class TestWiredIntoThePump:
    """A writer nobody calls is the failure mode this project has hit
    twice — effect params computed for a year with no Babysitter writer,
    and camera fields unwritten for 11 days. Assert the call site exists
    in the generated monolith, not just the source part."""

    def test_pump_calls_the_visibility_writer(self):
        src = _BABYSITTER_PATH.read_text(encoding="utf-8")
        assert "this._processLayerVisibility(cLayer, aeLayer);" in src, (
            "_processLayerVisibility is defined but never called from the "
            "pump — the variant feature would silently no-op in AE."
        )

    def test_writer_is_defined_in_the_monolith(self):
        src = _BABYSITTER_PATH.read_text(encoding="utf-8")
        assert "_processLayerVisibility: function(cLayer, aeLayer)" in src


class TestAuditorParity:
    """The Auditor's visibility check is the regression net for the
    silent-drop class. Assert it exists and is wired into the audit
    run."""

    _AUDITOR = _REPO_ROOT / "Scripts" / "Dimension_Assets" / "Auditor.jsx"

    def test_check_is_defined(self):
        src = self._AUDITOR.read_text(encoding="utf-8")
        assert "_auditVariantVisibility: function" in src

    def test_check_is_called_and_its_result_collected(self):
        src = self._AUDITOR.read_text(encoding="utf-8")
        assert "this._auditVariantVisibility(" in src
        assert "auditFailures.push(variantDrift)" in src

    def test_check_is_a_warning_not_a_critical_failure(self):
        """A wrong video switch is a one-click fix for the artist. Only
        structural failures (layer count, hierarchy) may halt."""
        src = self._AUDITOR.read_text(encoding="utf-8")
        critical_block = src[src.index("var criticalFailures = [];"):]
        critical_block = critical_block[: critical_block.index("if (criticalFailures.length")]
        assert "VARIANT_VISIBILITY_MISMATCH" not in critical_block

    def test_check_resolves_layers_by_uid_not_bare_index(self):
        """Mirror-tree inject introduces per-comp index collisions; a
        visibility check that audited the WRONG layer would be worse
        than no check."""
        src = self._AUDITOR.read_text(encoding="utf-8")
        block = src[src.index("_auditVariantVisibility: function"):]
        block = block[: block.index("VARIANT_VISIBILITY_MISMATCH")]
        assert "caller.findLayerByUID(" in block
