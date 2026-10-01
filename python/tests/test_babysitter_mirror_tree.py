# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_babysitter_mirror_tree.py — Slot 12.5 Stage D / Item 2.

Static contract tests for the mirror-tree path in
`Scripts/Dimension_Assets/Babysitter.jsx`:

  - setupWorkspace's two-path branch (legacy single-comp vs Stage D
    N-comp mirror tree)
  - _findOrCreateFolder helper for nested target-bin paths
  - _findCompByName lookup helper
  - _createMirrorComp per-comp creation extracted from setupWorkspace
  - _state captures `mirrorTree` + `targetBinPath` from chunk_manifest
  - _resetState includes the new mirror-tree fields

Real AE round-trip verification is Matt's smoke step (Stage D exit
criterion). These tests pin the JSX shape so a future regression
that drops one of the new helpers or breaks the branch fails CI
without needing AE.
"""

from __future__ import annotations

import re
from pathlib import Path


_BABYSITTER_PATH = (
    Path(__file__).resolve().parents[2]
    / "Scripts" / "Dimension_Assets" / "Babysitter.jsx"
)


def _read() -> str:
    return _BABYSITTER_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. New helpers exist
# ---------------------------------------------------------------------------

class TestNewHelpers:
    def test_find_or_create_folder_exists(self):
        src = _read()
        assert "_findOrCreateFolder: function(folderPath)" in src, (
            "_findOrCreateFolder helper must exist — used by the "
            "mirror-tree path to resolve nested target bins like "
            "`From Dimensions/TIKTOK_<ts>/`."
        )

    def test_find_comp_by_name_exists(self):
        src = _read()
        assert "_findCompByName: function(compName)" in src, (
            "_findCompByName helper must exist — used by both legacy "
            "and mirror-tree paths to resolve source comps by name."
        )

    def test_create_mirror_comp_exists(self):
        src = _read()
        assert "_createMirrorComp: function(srcComp, outputName, outW, outH, parentFolder" in src, (
            "_createMirrorComp helper must exist — encapsulates the "
            "per-comp duplicate-or-addComp+copyToComp logic so both "
            "legacy and mirror-tree paths share one implementation."
        )


# ---------------------------------------------------------------------------
# 2. _findOrCreateFolder behavior
# ---------------------------------------------------------------------------

class TestFindOrCreateFolderBehavior:
    def _body(self):
        src = _read()
        start = src.index("_findOrCreateFolder: function")
        end = src.index("_findCompByName: function")
        return src[start:end]

    def test_splits_path_segments(self):
        body = self._body()
        # Splits the slash-joined path so nested target bins
        # ("From Dimensions/TIKTOK_<ts>") create both segments.
        assert "split(\"/\")" in body, (
            "_findOrCreateFolder must split the path on '/' so nested "
            "target-bin paths resolve correctly."
        )

    def test_trims_leading_and_trailing_slashes(self):
        body = self._body()
        # Defensive: paths like "/From Dimensions/X/" shouldn't produce
        # an empty leading segment.
        assert "while" in body and "charAt(0)" in body, (
            "_findOrCreateFolder must trim leading slashes before splitting."
        )

    def test_falls_back_to_legacy_flat_folder_when_path_empty(self):
        body = self._body()
        # Empty / missing path → "From Dimensions" flat (pre-Stage-D
        # behavior preserved).
        assert "\"From Dimensions\"" in body, (
            "Empty folderPath must fall back to the legacy 'From "
            "Dimensions' folder so pre-Stage-D code paths work."
        )


# ---------------------------------------------------------------------------
# 3. setupWorkspace branches on mirrorTree presence
# ---------------------------------------------------------------------------

class TestSetupWorkspaceBranching:
    def _body(self):
        src = _read()
        start = src.index("setupWorkspace: function(presetName, expectedCompName")
        # next public method
        end = src.index("    // ── Injection state")
        return src[start:end]

    def test_signature_accepts_mirror_tree_and_bin_path(self):
        body = self._body()
        # New args appended to the end of the signature — legacy
        # callers without them still work (JS undefined → falsy).
        sig = re.search(
            r"setupWorkspace:\s*function\s*\(([^)]+)\)",
            body,
        )
        assert sig is not None, "Could not locate setupWorkspace signature."
        args = [a.strip() for a in sig.group(1).split(",")]
        assert args[:7] == [
            "presetName", "expectedCompName",
            "targetWidth", "targetHeight",
            "mirrorTree", "targetBinPath",
            # Track B / B2 (2026-08-26) — appended, not inserted, so
            # legacy callers without it still work (JS undefined → falsy).
            "outputCompName",
        ], (
            f"Expected setupWorkspace prefix args = ['presetName', 'expectedCompName', "
            f"'targetWidth', 'targetHeight', 'mirrorTree', 'targetBinPath', "
            f"'outputCompName']; got {args!r}"
        )
        if len(args) > 7:
            assert args[7:] == ["targetFps", "targetDuration"], (
                f"Expected Track E appended args = ['targetFps', 'targetDuration']; got {args[7:]!r}"
            )

    def test_branches_on_mirror_tree_presence(self):
        body = self._body()
        # The branch — when mirrorTree is non-empty, take the N-comp
        # path; otherwise fall through to legacy single-comp.
        assert "if (mirrorTree && mirrorTree.length > 0)" in body, (
            "setupWorkspace must branch on `mirrorTree && mirrorTree.length > 0`. "
            "Empty / absent mirrorTree → legacy single-comp path."
        )

    def test_legacy_path_uses_dimension_prefix_name(self):
        body = self._body()
        # Legacy fallback name format — pre-Stage-D `[DIMENSION] <preset>`.
        assert "\"[DIMENSION] \" + presetName" in body, (
            "Legacy path must preserve the pre-Stage-D `[DIMENSION] "
            "<preset>` naming convention so 87N flat regression stays "
            "byte-identical when chunk_manifest lacks mirror_tree."
        )

    def test_mirror_path_iterates_entries(self):
        body = self._body()
        assert re.search(r"for\s*\(\s*var\s+mi\s*=\s*0\s*;\s*mi\s*<\s*mirrorTree\.length", body), (
            "Mirror-tree path must iterate every entry in mirrorTree."
        )

    def test_mirror_path_calls_create_mirror_comp(self):
        body = self._body()
        # Both paths delegate to the same per-comp helper. The call may
        # span multiple lines; tolerate whitespace between args.
        assert re.search(
            r"this\._createMirrorComp\(\s*srcByName\s*,\s*entry\.output_name",
            body,
        ), (
            "Mirror-tree path must call _createMirrorComp(srcComp, "
            "outputName, outW, outH, parentFolder) per entry — same "
            "per-comp logic as the legacy path."
        )

    def test_mirror_path_resolves_target_bin(self):
        body = self._body()
        assert "this._findOrCreateFolder(targetBinPath" in body, (
            "Mirror-tree path must resolve targetBinPath via "
            "_findOrCreateFolder so nested bin folders land correctly."
        )

    def test_mirror_path_returns_root_comp(self):
        body = self._body()
        # The chunk pump expects setupWorkspace to return ONE
        # outputComp (the root). Item 2 keeps that contract; the
        # non-root mirrors stash on _state.mirrorComps.
        assert "if (entry.is_root === true) rootComp = mirror;" in body, (
            "Mirror-tree path must identify the is_root entry and "
            "return its comp so the chunk pump targets the right comp."
        )

    def test_mirror_path_stashes_mirrors_for_later_items(self):
        body = self._body()
        # Items 3-4 will consume _state.mirrorComps to wire wrapper
        # rewires and recompute transforms.
        assert "this._state.mirrorComps = mirrors;" in body, (
            "Mirror-tree path must stash the per-source-name mirrors "
            "map on _state.mirrorComps for Items 3-4 to consume."
        )


# ---------------------------------------------------------------------------
# 4. _state and _resetState carry the new fields
# ---------------------------------------------------------------------------

class TestStateWiring:
    def test_reset_state_includes_mirror_tree_fields(self):
        src = _read()
        reset_start = src.index("_resetState: function()")
        reset_end = src.index("executeSovereignInjection: function")
        body = src[reset_start:reset_end]
        for field in ("mirrorTree", "targetBinPath", "mirrorComps"):
            assert field in body, (
                f"_resetState must clear `{field}` so a previous "
                "session's mirror-tree state can't leak into a new one."
            )

    def test_execute_sovereign_injection_reads_mirror_tree_from_manifest(self):
        src = _read()
        exec_start = src.index("executeSovereignInjection: function")
        # Find the end of the function (next major declaration after).
        exec_body = src[exec_start:exec_start + 4000]
        for field, manifest_key in (
            ("mirrorTree", "mirror_tree"),
            ("targetBinPath", "target_bin_path"),
        ):
            pattern = f"{field}:" + r"\s+manifest\." + manifest_key
            assert re.search(pattern, exec_body), (
                f"executeSovereignInjection must populate _state.{field} "
                f"from manifest.{manifest_key} (chunk_manifest field "
                "emitted by exporter.py when mirror tree is built)."
            )

    def test_pump_passes_mirror_tree_to_setup_workspace(self):
        src = _read()
        # The pump's "setup" phase calls setupWorkspace; the call
        # must include st.mirrorTree + st.targetBinPath.
        pump_block = src[src.index("if (st.phase === \"setup\")"):]
        pump_block = pump_block[:1500]
        assert "st.mirrorTree, st.targetBinPath" in pump_block, (
            "Pump's setup phase must forward _state.mirrorTree and "
            "_state.targetBinPath into setupWorkspace."
        )


# ---------------------------------------------------------------------------
# 5. ES3 compatibility of the new code
# ---------------------------------------------------------------------------

class TestEs3Compatibility:
    def _new_code_body(self):
        src = _read()
        start = src.index("_findOrCreateFolder: function")
        end = src.index("    // ── Injection state")
        return src[start:end]

    def test_no_arrow_functions(self):
        body = self._new_code_body()
        assert "=>" not in body, (
            "Arrow functions are not ES3-compatible."
        )

    def test_no_let_const(self):
        body = self._new_code_body()
        assert not re.search(r"\blet\s+\w", body), "ES3: use var, not let."
        assert not re.search(r"\bconst\s+\w", body), "ES3: use var, not const."

    def test_uses_classic_for_loops(self):
        body = self._new_code_body()
        assert re.search(r"for\s*\(\s*var\s+\w+\s*=", body), (
            "New JSX must use classic `for (var ...; ...; ...)` loops."
        )
