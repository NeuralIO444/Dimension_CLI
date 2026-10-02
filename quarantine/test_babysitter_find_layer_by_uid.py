# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_babysitter_find_layer_by_uid.py — Slot 12.5 Stage A / item 4.

Static contract test for `Babysitter.findLayerByUID` (Babysitter.jsx).

Pre-Slot-12.5, this function was a misnamed index-fallback pass-through:
it accepted a `uid` argument but ignored it entirely and always returned
`comp.layer(fallbackIndex)`. Filed in BUGS.md (2026-05-15) as a Slot 12
landmine because Slot 12.5's mirror-tree recursive inject (Q4A) introduces
per-comp layer-index collisions — the UID branch is the only reliable
disambiguator across comps.

Stage A replaces the body with a real UID-locked scan (pattern mirrored
from `SovCore_Layer.jsx::findLayerByUID`), preserving the fallback-index
behavior as a back-compat safety net for legacy / pre-stamp manifests.

These tests pattern-check the JSX source. Real AE round-trip verification
is in the manual-QA checklist for the eventual Slot 12.5 PR — JSX can't
run in pytest.
"""

from __future__ import annotations

import re
from pathlib import Path


_BABYSITTER_PATH = (
    Path(__file__).resolve().parents[2]
    / "Scripts" / "Dimension_Assets" / "Babysitter.jsx"
)


def _read_babysitter_source() -> str:
    return _BABYSITTER_PATH.read_text(encoding="utf-8")


def _extract_find_layer_function(source: str) -> str:
    """Return the source of `findLayerByUID:` from the start of its
    declaration through the matching closing brace. Brace-counts so the
    nested for-loop body is included."""
    start = source.index("findLayerByUID:")
    # First `{` on or after start is the function's opening brace.
    open_brace = source.index("{", start)
    depth = 1
    i = open_brace + 1
    n = len(source)
    while i < n and depth > 0:
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return source[start:i]


class TestFindLayerByUIDStructure:
    def test_function_exists(self):
        src = _read_babysitter_source()
        assert "findLayerByUID:" in src, (
            "Babysitter.jsx must still expose findLayerByUID as a property."
        )

    def test_function_takes_three_args(self):
        src = _read_babysitter_source()
        # Signature: `findLayerByUID: function(comp, uid, fallbackIndex)`
        sig = re.search(
            r"findLayerByUID:\s*function\s*\(([^)]*)\)",
            src,
        )
        assert sig is not None, "Could not locate findLayerByUID signature."
        args = [a.strip() for a in sig.group(1).split(",")]
        assert args == ["comp", "uid", "fallbackIndex"], (
            f"Unexpected signature args: {args!r}. "
            "Existing callers depend on (comp, uid, fallbackIndex) order."
        )

    def test_function_scans_comments_for_uid_token(self):
        """The body must contain a UID-token scan against layer.comment
        — the canonical pattern from SovCore_Layer.jsx::findLayerByUID.
        This is what the function NAME has always promised; pre-Slot-12.5
        the body did not actually do it."""
        body = _extract_find_layer_function(_read_babysitter_source())
        # The scan looks for `uid:` prefix combined with the supplied uid.
        # Match `"uid:" + ` style concatenation (ES3-compatible string concat).
        assert '"uid:"' in body, (
            'findLayerByUID body must construct a "uid:" token (mirrors '
            "SovCore_Layer.findLayerByUID pattern)."
        )
        # The scan accesses `.comment` on at least one layer.
        assert ".comment" in body, (
            "findLayerByUID body must read layer.comment to locate the "
            "scrape-time UID stamp."
        )
        # The scan tests for token presence via indexOf().
        assert "indexOf(token)" in body or "indexOf(" in body and "token" in body, (
            "findLayerByUID body must check the comment for the uid token "
            "(e.g. via indexOf)."
        )

    def test_function_iterates_comp_layers(self):
        body = _extract_find_layer_function(_read_babysitter_source())
        # A classic for-loop iterating from 1 to numLayers — ES3 form.
        assert "numLayers" in body, (
            "findLayerByUID body must reference numLayers to iterate the "
            "comp's layer stack."
        )
        assert re.search(r"for\s*\(\s*var\s+i\s*=\s*1\b", body), (
            "findLayerByUID body must contain a `for (var i = 1; ...; i++)` "
            "loop (ES3-compatible, 1-based AE indexing)."
        )

    def test_function_preserves_index_fallback(self):
        """The pre-Slot-12.5 single-comp inject path relies on the index
        fallback (it works because copyToComp / srcComp.duplicate preserve
        1-based indices). The fix must not regress that path."""
        body = _extract_find_layer_function(_read_babysitter_source())
        assert "fallbackIndex" in body, (
            "fallbackIndex argument must still be honored as the back-compat "
            "safety net for legacy / un-stamped manifests."
        )
        # The fallback path returns `comp.layer(fallbackIndex)`.
        assert "comp.layer(fallbackIndex)" in body, (
            "Fallback path must call comp.layer(fallbackIndex) when no UID "
            "scan match — preserves the pre-Slot-12.5 single-comp inject path."
        )

    def test_function_does_not_throw_on_bad_comp(self):
        """Defensive: the function must be null-safe on the comp arg
        (callers receive null on lookup failure rather than an exception)."""
        body = _extract_find_layer_function(_read_babysitter_source())
        # Look for either an explicit `if (!comp)` guard or a try around
        # comp.numLayers — both are acceptable.
        has_null_guard = "if (!comp)" in body or "if (comp == null)" in body
        has_try_around_numlayers = bool(re.search(
            r"try\s*\{\s*[^}]*numLayers", body,
        ))
        assert has_null_guard or has_try_around_numlayers, (
            "findLayerByUID body must guard against a null/invalid comp arg "
            "— either an `if (!comp)` check or a try around comp.numLayers."
        )

    def test_function_is_es3_compatible(self):
        """Spot-check the body for ES3-incompatible syntax. The full set
        of ES3 prohibitions is in CLAUDE.md; this guards the most common
        regressions for new JSX code."""
        body = _extract_find_layer_function(_read_babysitter_source())
        # Arrow functions and template literals are the most common ES6
        # accidents in this codebase.
        assert "=>" not in body, "Arrow functions are not ES3-compatible."
        assert "`" not in body, "Template literals are not ES3-compatible."
        # `let` / `const` are ES6.
        assert not re.search(r"\blet\s+\w", body), (
            "`let` is not ES3-compatible; use `var`."
        )
        assert not re.search(r"\bconst\s+\w", body), (
            "`const` is not ES3-compatible; use `var`."
        )


class TestRegressionGuard:
    """Pin the specific regression Slot 12.5 Stage A item 4 closes:
    the pre-fix body returned comp.layer(fallbackIndex) WITHOUT
    consulting uid at all. The new body MUST consult uid first."""

    def test_uid_arg_is_actually_used(self):
        body = _extract_find_layer_function(_read_babysitter_source())
        # The body must reference `uid` (not just declare it in the
        # signature). Pre-fix: the arg was declared but never used.
        sig_end = body.index(")")
        body_after_sig = body[sig_end:]
        uses_uid = re.search(r"\buid\b", body_after_sig)
        assert uses_uid, (
            "findLayerByUID body must use the `uid` argument. The pre-fix "
            "implementation declared `uid` in the signature but ignored it "
            "in the body — silent index-fallback behind a UID-locked API."
        )

    def test_uid_check_happens_before_fallback(self):
        """The UID scan must come before the index fallback. Otherwise
        the fallback fires first and the UID branch is dead code."""
        body = _extract_find_layer_function(_read_babysitter_source())
        # Heuristic: locate the first appearance of `uid:` (the token
        # construction) and the first appearance of `comp.layer(fallbackIndex)`.
        uid_token_pos = body.find('"uid:"')
        fallback_pos = body.find("comp.layer(fallbackIndex)")
        assert uid_token_pos != -1, "UID token construction missing."
        assert fallback_pos != -1, "Index fallback path missing."
        assert uid_token_pos < fallback_pos, (
            "The UID scan must run before the index fallback so a successful "
            "UID match short-circuits the fallback."
        )
