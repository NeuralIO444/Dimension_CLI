# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_babysitter_chunk_routing.py — Slot 12.5 Stage D per-comp routing.

Static contract tests for the chunk-pump's per-comp routing fix
(2026-05-19). Pre-fix, Babysitter's chunk loop used
`st.outputComp.layer(cLayer.index)` for every chunk layer — but
recursive-scrape indices are numbered per containing comp, not
globally. The result on Corpus_01 → TikTok: 9 of 11 layers
collided into the root mirror's 2 index slots; the out-of-range
.layer() errors were swallowed by a silent catch.

These tests pin the JSX shape so a future regression that drops
the per-comp routing OR re-silences the chunk-loop error log
fails CI without needing AE.
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
# 1. mirrorCompsById is built alongside mirrorComps
# ---------------------------------------------------------------------------

class TestMirrorCompsByIdBuild:
    def test_state_field_exists(self):
        """`_state.mirrorCompsById` must be declared in _resetState so
        the chunk-loop's null-check has something to read against."""
        src = _read()
        assert "mirrorCompsById" in src, (
            "_state.mirrorCompsById missing. The chunk-pump loop "
            "reads this map to route each layer's write into its "
            "OWN mirror comp via cLayer.containing_comp_id; without "
            "it every layer collapses onto the root mirror's index "
            "slots (the 2026-05-19 Corpus_01 → TikTok bug)."
        )

    def test_setup_workspace_populates_by_id(self):
        """`setupWorkspace` must populate `mirrorCompsById` keyed by
        each entry's `source_comp_id` so chunk-pump's int→mirror
        lookup works."""
        src = _read()
        # Look for the assignment `mirrorsById[...source_comp_id...] = mirror`
        # without pinning the exact local-variable name — but enforce
        # that source_comp_id is read by the setup branch.
        assert re.search(
            r"mirrorsById\s*\[\s*String\(\s*entry\.source_comp_id\s*\)\s*\]\s*=\s*mirror",
            src,
        ) or re.search(
            r"mirrorsById\s*\[\s*entry\.source_comp_id\s*\]\s*=\s*mirror",
            src,
        ), (
            "setupWorkspace's mirror-tree branch must populate "
            "mirrorsById[entry.source_comp_id] = mirror. The "
            "chunk-pump loop reads cLayer.containing_comp_id "
            "against this map to resolve the correct mirror."
        )

    def test_state_stash_after_build(self):
        """The locally-built `mirrorsById` must end up on
        `this._state.mirrorCompsById` — otherwise the chunk pump
        can't see it."""
        src = _read()
        assert re.search(
            r"this\._state\.mirrorCompsById\s*=\s*mirrorsById",
            src,
        ), (
            "`this._state.mirrorCompsById = mirrorsById` missing. "
            "The chunk-pump phase reads _state, not the local "
            "setupWorkspace scope."
        )


# ---------------------------------------------------------------------------
# 2. Chunk-pump loop reads containing_comp_id and routes accordingly
# ---------------------------------------------------------------------------

class TestChunkLoopPerCompRouting:
    def test_reads_containing_comp_id(self):
        """The chunk loop must read `cLayer.containing_comp_id` —
        this is the fix's load-bearing line. Removing this read
        re-introduces the bug."""
        src = _read()
        assert "cLayer.containing_comp_id" in src, (
            "Chunk-pump loop must read `cLayer.containing_comp_id` "
            "to route each layer's write into its correct mirror. "
            "Pre-fix the loop did `st.outputComp.layer(cLayer.index)` "
            "and 9 of 11 Corpus_01 layers were dropped silently."
        )

    def test_routes_via_mirror_comps_by_id(self):
        """The chunk loop must look up the containing mirror in
        `st.mirrorCompsById` (the per-id map), then call .layer()
        on THAT mirror — not always on st.outputComp."""
        src = _read()
        # Look for `st.mirrorCompsById[<expr involving containing_comp_id>]`
        assert re.search(
            r"st\.mirrorCompsById\s*\[\s*String\(\s*cLayer\.containing_comp_id\s*\)\s*\]",
            src,
        ) or re.search(
            r"st\.mirrorCompsById\s*\[\s*cLayer\.containing_comp_id\s*\]",
            src,
        ), (
            "Chunk-pump loop must look up `st.mirrorCompsById["
            "cLayer.containing_comp_id]` to resolve each layer's "
            "containing mirror."
        )

    def test_calls_layer_on_resolved_mirror(self):
        """The .layer(cLayer.index) call must run against the
        RESOLVED containing mirror (a local variable), not always
        against st.outputComp. The fix introduces a local like
        `containingMirror` that defaults to st.outputComp and gets
        reassigned when mirrorCompsById has a match."""
        src = _read()
        # Find the line that does .layer(cLayer.index) in the chunk pump.
        # It must NOT be `st.outputComp.layer(cLayer.index)` (the pre-fix shape).
        chunk_section_match = re.search(
            r'if\s*\(\s*st\.phase\s*===\s*"chunk"\s*\)([\s\S]+?)\}\s*\n\s*if\s*\(\s*st\.phase\s*===\s*"audit"',
            src,
        )
        assert chunk_section_match, "Could not locate the chunk-phase block"
        chunk_section = chunk_section_match.group(1)

        assert "st.outputComp.layer(cLayer.index)" not in chunk_section, (
            "Chunk pump still calls `st.outputComp.layer(cLayer.index)` "
            "— this is the pre-fix shape that drops 9 of 11 layers "
            "on recursive scrapes."
        )
        # A resolved-mirror-shaped .layer() call must exist.
        assert re.search(
            r"\.layer\s*\(\s*cLayer\.index\s*\)", chunk_section
        ), (
            "Chunk pump must still call .layer(cLayer.index) — just "
            "against the resolved containing mirror, not st.outputComp."
        )

    def test_legacy_fallback_preserved(self):
        """When mirrorCompsById is null/empty (87N flat path / legacy
        single-comp), the resolved mirror must fall back to
        st.outputComp so flat regression is byte-identical. Verify
        the fallback line exists."""
        src = _read()
        # The fix sets `containingMirror = st.outputComp` as the
        # default, then reassigns if a per-id match is found. So the
        # default-assignment must be present.
        chunk_section_match = re.search(
            r'if\s*\(\s*st\.phase\s*===\s*"chunk"\s*\)([\s\S]+?)\}\s*\n\s*if\s*\(\s*st\.phase\s*===\s*"audit"',
            src,
        )
        assert chunk_section_match, "Could not locate the chunk-phase block"
        chunk_section = chunk_section_match.group(1)
        assert re.search(
            r"=\s*st\.outputComp\s*;", chunk_section
        ), (
            "Chunk pump must default the resolved containing-mirror "
            "to `st.outputComp` so the legacy single-comp / 87N flat "
            "path stays byte-identical."
        )


# ---------------------------------------------------------------------------
# 3. Silent failure killed — error log is loud
# ---------------------------------------------------------------------------

class TestChunkLoopErrorLogging:
    def test_error_log_carries_routing_diagnostics(self):
        """When a chunk-layer write fails, the ERROR log must
        include `containing_comp_id` and `layer_uid` so the next
        routing miss is immediately visible. Pre-fix the catch
        logged only `level: ERROR, layer, msg` — enough to know
        SOMETHING failed but not enough to see that 9 of 11 layers
        were missing their writes."""
        src = _read()
        # Find the chunk-loop catch block.
        chunk_section_match = re.search(
            r'if\s*\(\s*st\.phase\s*===\s*"chunk"\s*\)([\s\S]+?)\}\s*\n\s*if\s*\(\s*st\.phase\s*===\s*"audit"',
            src,
        )
        assert chunk_section_match, "Could not locate the chunk-phase block"
        chunk_section = chunk_section_match.group(1)

        # Look for the catch(err) block in the chunk loop.
        catch_match = re.search(
            r'\}\s*catch\s*\(\s*err\s*\)\s*\{([\s\S]+?)\}\s*\}',
            chunk_section,
        )
        assert catch_match, (
            "Could not find chunk-loop `catch(err)` block. The fix "
            "must keep the catch but expand the log payload."
        )
        catch_body = catch_match.group(1)

        # Required diagnostic fields.
        for field in ("layer_uid", "containing_comp_id"):
            assert field in catch_body, (
                f"Chunk-loop error log missing `{field}`. The "
                f"silent-failure-class bug (9 of 11 Corpus_01 "
                f"layers dropped without surfacing in the log) is "
                f"the actual reason the per-comp routing bug "
                f"survived three sessions. Every miss must now log "
                f"enough to identify which layer and which comp."
            )

        assert '"ERROR"' in catch_body or "'ERROR'" in catch_body, (
            "Chunk-loop error log must carry `level: ERROR`."
        )
