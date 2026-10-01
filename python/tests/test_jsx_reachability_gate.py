# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_jsx_reachability_gate.py — the JSX dead-code gate (issue #478).

WHY THIS TEST EXISTS
--------------------
`tools/jsx_reachability.py` (#419) reports dead ExtendScript definitions
but enforces nothing — it can be run, read, and ignored. Python has had a
real gate for this since Phase 0 (`test_reachability.py`): a curated
allowlist with dated justifications, and a test that fails the moment a
new unreachable symbol appears or a listed one stops being unreachable.
JSX had the report but not the gate, and #419's own docstring names the
resulting asymmetry directly: "Python has a gate and doesn't rot; JSX has
none and does." Two dead-JSX discoveries were made by hand, months apart,
specifically because nothing mechanical checks that surface.

This test gives `jsx_reachability` the same allowlist-gate shape. It does
NOT change the tool's own conservatism (name matching stays global and
unqualified — see that module's docstring) — it only makes the tool's
existing output binding instead of advisory.

Symbol-level, not module-level. `tools/jsx_reachability.py` reports
individual dead FUNCTIONS/definitions, not files — ExtendScript has no
module system the way Python does. The allowlist is therefore keyed by
symbol name, matching what `analyze()` actually reports.

Deleting an allowlisted symbol is explicitly OUT OF SCOPE here (see the
issue): each requires manual AE QA per CLAUDE.md's JSX rule, and is a
separate, narrower follow-up per symbol. This test only gates the
detector's output against a written, dated ledger.
"""

from __future__ import annotations

from typing import Any, Dict, Set

import pytest

from tools.jsx_reachability import analyze

# ---------------------------------------------------------------------------
# The allowlist: JSX definitions jsx_reachability.py finds no caller for,
# and the reason each is permitted to stay that way for now.
#
# Every entry here was confirmed dead during the 2026-09-05 architecture
# cleanup audit (docs/knowledge/2026-09-05-architecture-cleanup-audit.md,
# Finding 7, PR #472) and reconfirmed 2026-09-06 while building this gate
# (issue #478). Deleting any of them is a separate, per-symbol follow-up
# requiring manual AE QA per CLAUDE.md's JSX rule — this gate only asserts
# that the detector's live output matches this written ledger.
# ---------------------------------------------------------------------------
ALLOWLIST: Dict[str, str] = {
    "_stripExpression": (
        "2026-09-08 (#509/#510) — newly dead as a side effect of deleting "
        "_setVectorWithFallback, its only caller. Out of scope for #509/"
        "#510 (neither issue named this symbol); flagged as a follow-up "
        "rather than deleted unilaterally per CLAUDE.md scope discipline. "
        "Deletion needs manual AE QA per CLAUDE.md's JSX rule."
    ),
    "applyManifest": (
        "2026-09-06 (#478) — the superseded position_4k/anchor_4k "
        "injection path, replaced by the current conformed_transforms "
        "writer. Confirmed dead in the 2026-09-05 architecture cleanup "
        "audit (Finding 7, PR #472). Deletion is a separate follow-up "
        "needing manual AE QA."
    ),
    "cleanOrphanedComps": (
        "2026-09-06 (#478) — SovCore_Cleanup.jsx, separately flagged in "
        "docs/audits/2026-08-30-comprehensive-fullstack-audit.md (INV-02) "
        "with a recommendation to wire it into package.sh that was never "
        "acted on. Confirmed dead in the 2026-09-05 architecture cleanup "
        "audit (Finding 7, PR #472). Wiring or deletion is a separate "
        "follow-up needing manual AE QA."
    ),
    "verifyContext": (
        "2026-09-06 (#478) — no caller found in JSX, Python, or CEP JS. "
        "Confirmed dead in the 2026-09-05 architecture cleanup audit "
        "(Finding 7, PR #472). Deletion is a separate follow-up needing "
        "manual AE QA."
    ),
    "writeDebugLog": (
        "2026-09-06 (#478) — appends to the user-visible "
        "~/Desktop/dimension_jsx_debug.log from dead code. Confirmed dead "
        "in the 2026-09-05 architecture cleanup audit (Finding 7, PR "
        "#472). Deletion is a separate follow-up needing manual AE QA."
    ),
}


@pytest.fixture(scope="module")
def report() -> Dict[str, Any]:
    """One analysis pass for the module — jsx_reachability.analyze() walks
    every .jsx/.js/.py file in scope, so it is run once and shared."""
    return analyze()


class TestJsxReachabilityGate:
    def test_unreached_jsx_definitions_match_allowlist(
        self, report: Dict[str, Any]
    ) -> None:
        """Unreached JSX definitions must be deleted, wired, or justified
        in writing.

        Three checks, same shape as test_reachability.py's module gate:

        1. Every allowlist entry carries a real justification.
        2. Nothing unreached is missing from the allowlist — the gate.
        3. Nothing in the allowlist has stopped being unreached — without
           this the ledger rots instead of shrinking as symbols get wired
           or deleted.
        """
        # 1 — justifications are real
        unjustified = sorted(
            name
            for name, why in ALLOWLIST.items()
            if not isinstance(why, str) or not why.strip()
        )
        assert not unjustified, (
            "ALLOWLIST entr(ies) with no justification: "
            + ", ".join(unjustified)
            + "\nEvery allowlisted JSX definition needs a one-line reason "
            "it is permitted to have no caller."
        )

        dead = report["dead_definitions"]
        by_name = {d["name"]: d for d in dead}
        actual: Set[str] = set(by_name)
        allowed: Set[str] = set(ALLOWLIST)

        # 2 — the gate itself
        appeared = sorted(actual - allowed)
        assert not appeared, (
            "JSX definition(s) with no caller in JSX, Python, or CEP JS, "
            "not covered by ALLOWLIST:\n"
            + "\n".join(
                f"  - {name}  ({by_name[name]['definitions'][0]['file']}:"
                f"{by_name[name]['definitions'][0]['line']})"
                for name in appeared
            )
            + "\n\nFor each: delete it (needs manual AE QA per CLAUDE.md's "
            "JSX rule), wire it up, or add it to ALLOWLIST in "
            "python/tests/test_jsx_reachability_gate.py with a "
            "justification.\nRun `python -m tools.jsx_reachability` for "
            "the full report."
        )

        # 3 — the ledger stays truthful
        stale = sorted(allowed - actual)
        assert not stale, (
            "Stale ALLOWLIST entr(ies) — jsx_reachability.py no longer "
            "reports these as having no caller:\n"
            + "\n".join(f"  - {name}  (justification: {ALLOWLIST[name]})" for name in stale)
            + "\n\nEach was either deleted or gained a real caller.\nRemove "
            "the entry from ALLOWLIST in "
            "python/tests/test_jsx_reachability_gate.py."
        )

    def test_analysis_saw_the_whole_jsx_tree(self, report: Dict[str, Any]) -> None:
        """A near-empty scan would make this gate pass for the wrong
        reason. Pins the same floor test_jsx_reachability.py already
        asserts, so a scan-scope regression fails loudly here too."""
        assert report["jsx_files_scanned"] >= 15
        assert report["definitions_found"] > 100
