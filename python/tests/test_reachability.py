# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_reachability.py — the dead-code gate (DCE Phase 0).

WHY THIS TEST EXISTS
--------------------
`dimension_engine.spec` bundles the shipped binary using
`collect_submodules('core' | 'logic' | 'models')`. That force-bundles
every module in those packages regardless of whether any production
entry point can reach it. Unreachable code has therefore had **zero
visible cost** in this repo: it does not break a build, does not fail a
test, does not shrink when abandoned. It simply accumulates — to 14.5%
of production Python by the 2026-08-18 audit
(`docs/audits_2026/2026-08-18-reachability-audit-and-dce-plan.md`).

This test restores the cost. It fails the moment a production module
becomes unreachable from every production entry point without someone
writing down why that is acceptable. Without it, everything Phases
2a-2e delete grows back and the audit has to be paid for a second time.

The gate is deliberately MODULE-level only. `tools/reachability.py`
also reports dead symbols inside live modules, but symbol-level gating
is out of scope for Phase 0 and would be far more brittle. Likewise
this file never asserts LOC totals or `dead_pct` — those move with
every unrelated commit and would make the gate a change-detector
rather than a guard.

THE GOAL IS NOT AN EMPTY ALLOWLIST. The allowlist is split into
`PERMANENT` and `TRANSITIONAL` for exactly this reason: at least six
entries (four pytest-plugin modules, the JSX drift-guard table, and one
operator tool) will never be production-reachable and are not debt. The
deletion phases drive **`TRANSITIONAL`** to empty. Its non-emptiness is
therefore a live signal rather than permanent background noise.
"""

from __future__ import annotations

from typing import Any, Dict, Set

import pytest

from tools.reachability import analyze

# ---------------------------------------------------------------------------
# The allowlist: production modules that no production entry point reaches,
# and the reason each one is permitted to stay that way.
#
# Split into two dicts on 2026-08-18, because collapsing them into one made a
# genuinely permanent keep look like debt. A reader working the single list
# mechanically would have deleted `core.manifest_diff` — a diagnostic Matt
# actually uses.
#
#   PERMANENT     — will never be production-reachable, by design. This dict
#                   is NOT expected to shrink. Test infrastructure, an
#                   external-artifact drift guard, and operator tools reached
#                   only through `python/scripts/`.
#
#   TRANSITIONAL  — debt. THIS is the dict the deletion phases drive to
#                   EMPTY. "SCHEDULED FOR DELETION" entries go away when
#                   their phase lands; "UNDECIDED" entries go away when a
#                   ruling is made and the module is deleted or wired back
#                   into the product. An entry sitting here a long time is a
#                   signal, not a settled state.
#
# Adding an entry to either means making a claim in writing. Every value must
# be a real one-line justification — asserted below, so a future entry cannot
# be slipped in with an empty string to quiet the gate.
# ---------------------------------------------------------------------------
PERMANENT: Dict[str, str] = {
    "core.match_name_registry": (
        "VERIFIED spec, not dead code — reference table for the live JSX drift "
        "guard test_scraper_v5.py::test_jsx_table_matches_python_registry, which "
        "compares it against the 173 matchName entries in SovCore_Classify.jsx. "
        "Deleting it removes the only guard against that table drifting (same "
        "failure class as the V5_LAYER_KEYS bug)"
    ),
    "core.manifest_diff": (
        "OPERATOR_TOOL — NOT dead code. A live manifest-divergence diagnostic, "
        "reached only via python/scripts/diff_manifest.py and documented as the "
        "standard review workflow in BUGS.md:494-505 (--include-noise, "
        "--conformed, the 2026-07-05 playhead-noise fix). It shows as "
        "unreachable because python/scripts/ is correctly outside the "
        "production entry set — see the DECIDED note beside ENTRY_MODULES in "
        "tools/reachability.py for why promoting scripts/ to an entry class is "
        "the wrong fix. Permanent keep; do not delete"
    ),
    "core.conform_invariant_checks": (
        "OPERATOR_TOOL — NOT dead code. Phase 4 of the autonomous-engineering "
        "initiative (2026-09-02): the universal invariant battery (finiteness, "
        "layer-count preservation, sealed-unit atomicity) shared by "
        "test_full_catalog_conform_sweep.py and python/tools/pr_diff_harness.py. "
        "Same shape as core.manifest_diff above — reached only via "
        "python/tools/ (correctly outside the production entry set) and the "
        "test suite, never dead in practice. Permanent keep; do not delete"
    ),
    "core.degenerate_geometry_generators": (
        "OPERATOR_TOOL — NOT dead code. Phase 4 of the autonomous-engineering "
        "initiative: deterministic degenerate-geometry generators for "
        "python/tools/pr_diff_harness.py. Same shape as core.manifest_diff "
        "above — reached only via python/tools/ and the test suite. "
        "Permanent keep; do not delete"
    ),
    "core.random_geometry_fuzzer": (
        "OPERATOR_TOOL — NOT dead code. Phase 5 of the autonomous-engineering "
        "initiative (2026-09-02): dependency-free property-based fuzzer "
        "(stdlib random + real shrinking, chosen over adding the `hypothesis` "
        "dependency after researching whether a dependency-free alternative "
        "would cover this codebase's actual need — it does). Reached only via "
        "python/tools/fuzz_conform.py and the test suite. Same shape as "
        "core.manifest_diff above. Permanent keep; do not delete"
    ),
}

TRANSITIONAL: Dict[str, str] = {
    "bridge.color_match_bridge": (
        "CONFIRMED WIRE CANDIDATE (2026-09-05 #414 decision) — Color Match "
        "(Track D / Horizon Toolkit, CM2). Dispatches reference-frame renders "
        "and 3D LUT injection jobs via SovereignBridge.execute_bridge_job(). "
        "Matching JSX handlers exist in socket_server.jsx, Dimension_Launcher.jsx "
        "(_handleColorMatchRenderJob), and export_frame.jsx (exportFrameById). "
        "Kept as the designated Python dispatch bridge for Horizon Color "
        "Planner Phase 1 / Track C (#351) and A/B/C Grade Revision Switcher "
        "(#243) workflows requiring out-of-process / headless render or "
        "LUT injection."
    ),
    "core.dag_duplication": (
        "UNDECIDED (staged) — PR 1 of Next-Gen Relayout Pipeline. "
        "Provides DAGDuplicationPlanner, meant to replace duplication_planner.py's "
        "flat 1-level matching with recursive multi-level DAG mirror duplication "
        "(see docs/roadmap/grounded-relayout-and-bidirectional-reconform-blueprint-2026-08-30.md "
        "Anchor 2). Corrected 2026-09-01: PR 6 (shipped as PR #294) was always scoped "
        "as the E2E/QA-diagnostic capstone, never an orchestration-wiring PR — the "
        "'wiring in PR 6' claim this entry previously carried was inaccurate. No PR "
        "currently schedules this integration; it needs explicit scoping before any "
        "wiring work starts (touches duplication_planner.py, a live production path). "
        "2026-09-05 (#406/#413): the engine-choice question this entry tracked against "
        "core.precomp.dag_graph is RESOLVED — Matt approved the comparison memo's "
        "recommendation (docs/architecture/dag-engine-comparison-and-recommendation-"
        "2026-09-03.md): dag_duplication.py wins (real DuplicationPlan output, "
        "structural sealed/unsealed classification, 20+ tests vs. 6, three pillar-check "
        "callers vs. zero). core.precomp.dag_graph was deleted; its iterative Kahn "
        "topological sort and in_degree/out_degree bookkeeping were salvaged into this "
        "module first. This entry stays UNDECIDED because the engine CHOICE is settled "
        "but WIRING into duplication_planner.py is not — that is still separate, "
        "explicitly-scoped future work, blocked on the open Babysitter multi-level-"
        "rewire question (memo §6)."
    ),
    "core.expression_rewriter": (
        "UNDECIDED (staged) — PR 5 of Next-Gen Relayout Pipeline. "
        "Provides an Adaptive AST Expression Normalizer with 1-click rollback backups, "
        "meant to extend expression_analyzer.py's existing classify-only pass "
        "(safe/rewritable/unsafe) into actually rewriting 'rewritable' expressions "
        "(see docs/roadmap/grounded-relayout-and-bidirectional-reconform-blueprint-2026-08-30.md "
        "Anchor 1). Corrected 2026-09-01: PR 6 (shipped as PR #294) was always scoped "
        "as the E2E/QA-diagnostic capstone, never a scale-engine-wiring PR — the "
        "'wiring in PR 6' claim this entry previously carried was inaccurate. No PR "
        "currently schedules this integration. "
        "2026-09-02 re-triage (dead-code pass): issue #332 itself demands "
        "case-by-case AE-semantics verification and a live-AE round-trip "
        "before wiring — explicitly not just green unit tests, drawing a "
        "direct precedent to effect_conformer.py (shipped on a plausible "
        "premise, silently double-transformed effect params, deleted a year "
        "later). Correctly stays staged pending that safety review. No change."
    ),
    # core.color.* (9 entries) removed 2026-09-05 (issue #479) — wired via
    # a new scripts/derive_smart_match.py CLI ("Smart Match" in the Color
    # Match panel), reachable through cli.py -> lut_cli.py -> planner.py.
    # The package was also fixed to actually be importable outside pytest
    # (core/color/__init__.py and three internal modules self-imported via
    # a "from python.core.color..." prefix that only resolved by accident
    # under pytest's dual sys.path setup; every real execution context —
    # this new script included — hit ModuleNotFoundError: No module named
    # 'python'). core.color.gateway's LogSpaceGateway (renamed from a
    # colliding "HorizonColorGateway" that duplicated core.horizon_color's
    # own class of that name) is reachable via the package's own
    # re-export but still has no caller inside plan() — camera-log/gamut
    # decoding stays a deliberate follow-up.
    "core.grade_switcher": (
        "CONFIRMED WIRE CANDIDATE (2026-09-02 dead-code triage) — "
        "TASK-CM-TOOLKIT-02 (#243) Horizon A/B/C Grade Revision Switcher "
        "Controller. #243's closure comment names this module as part of "
        "what shipped alongside SovCore_GradeSwitcher.jsx, but the live "
        "production path (host.jsx::injectGradeSwitcher + color_match_ui.js) "
        "doesn't call it. Real feature work; scoped as its own future PR."
    ),
    "core.naming_context": (
        "STAGED 2026-09-09 (Matt decision 2026-10-01: keep, wiring pending) — "
        "130-LOC naming-context module; only consumer is its own test file. "
        "Not wired into output_naming.resolve_output_name yet; that wiring is "
        "unscoped feature work. Kept deliberately; revisit when naming work resumes."
    ),
}

# Combined view — what the gate actually asserts against.
ALLOWLIST: Dict[str, str] = {**PERMANENT, **TRANSITIONAL}


@pytest.fixture(scope="module")
def report() -> Dict[str, Any]:
    """One analysis pass for the module.

    `analyze()` parses every .py file under python/, so it is run once and
    shared rather than rebuilt per assertion.
    """
    return analyze()


class TestReachabilityGate:
    def test_unreachable_production_modules_match_allowlist(
        self, report: Dict[str, Any]
    ) -> None:
        """Unreachable production code must be deleted or justified in writing.

        Four checks, ordered cheapest-and-most-fundamental first:

        1. Every allowlist entry carries a real justification.
        2. The analyser parsed every file (a parse failure silently shrinks
           the graph, which would make this gate green for the wrong reason).
        3. Nothing unreachable is missing from the allowlist — the gate.
        4. Nothing in the allowlist has stopped being unreachable — without
           this the ledger rots and stops shrinking, which is the whole
           mechanism by which the later deletion phases drive TRANSITIONAL
           to empty.
        """
        # 1 — justifications are real
        unjustified = sorted(
            m
            for m, why in ALLOWLIST.items()
            if not isinstance(why, str) or not why.strip()
        )
        assert not unjustified, (
            "ALLOWLIST entr(ies) with no justification: "
            + ", ".join(unjustified)
            + "\nEvery allowlisted module needs a one-line reason it is "
            "permitted to be unreachable."
        )

        # 2 — the analyser saw the whole tree
        parse_failures = report["parse_failures"]
        assert parse_failures == [], (
            "tools/reachability.py could not parse:\n"
            + "\n".join(f"  - {f}" for f in parse_failures)
            + "\n\nThe reachability graph is incomplete, so this gate cannot "
            "be trusted until these files parse cleanly."
        )

        by_module = {m["module"]: m for m in report["unreachable_modules"]}
        actual: Set[str] = set(by_module)
        allowed: Set[str] = set(ALLOWLIST)

        # 3 — the gate itself
        appeared = sorted(actual - allowed)
        assert not appeared, (
            "Unreachable production module(s) not covered by ALLOWLIST:\n"
            + "\n".join(
                f"  - {m}  ({by_module[m]['path']}, {by_module[m]['loc']} LOC)"
                for m in appeared
            )
            + "\n\nNo production entry point can reach this code, so it ships "
            "as dead weight in the binary.\nFor each module: delete it, or add "
            "it to ALLOWLIST in python/tests/test_reachability.py with a "
            "justification.\nRun ./bin/reach for the full report."
        )

        # 4 — the ledger stays truthful
        stale = sorted(allowed - actual)
        assert not stale, (
            "Stale ALLOWLIST entr(ies) — no longer unreachable production "
            "code:\n"
            + "\n".join(f"  - {m}  (justification: {ALLOWLIST[m]})" for m in stale)
            + "\n\nEach was either deleted or is now reachable from a "
            "production entry point.\nRemove the entry from PERMANENT or "
            "TRANSITIONAL in python/tests/test_reachability.py."
        )

    def test_no_vestigial_import_keeps_an_unreachable_module_alive(
        self, report: Dict[str, Any]
    ) -> None:
        """A dead module must not be kept nominally alive by an unused import.

        THIS IS THE LAUNDERING PATH, and it is the exact combination that
        makes it dangerous: an import whose bound name nothing in its own
        module ever references, pointing at a module that is already
        allowlisted or otherwise unreachable.

        Why that combination specifically. `tools/reachability.py` follows
        every import edge whether or not the imported name is used — and it
        must, because importing a module genuinely executes its top-level
        code. Dropping the edge would report modules dead while they still
        run, a false positive the tool is explicitly biased against. So the
        edge stays and the gate closes the hole from this side instead.

        The shape this catches is the standard half-finished deletion: the
        call sites are removed and the now-unused import at the top of the
        file is left behind. Without this assertion such a PR could delete
        every caller of a module and the gate would certify it as live
        forever — while it kept shipping in the binary. A gate that
        manufactures false confidence is worse than no gate.

        Remedy when this fails: delete the leftover import. If the import
        is deliberately side-effecting or a re-export, mark it
        `# noqa: F401` with a reason — a written claim, reviewable in the
        diff, exactly like an ALLOWLIST entry.
        """
        unreachable = {m["module"] for m in report["unreachable_modules"]}
        suspect = sorted(unreachable | set(ALLOWLIST))

        launderers = [
            v
            for v in report["vestigial_imports"]
            if v["target_module"] in suspect
        ]
        assert not launderers, (
            "Vestigial import(s) propping up unreachable/allowlisted "
            "module(s):\n"
            + "\n".join(
                f"  - {v['module']}:{v['lineno']}  imports {v['imported']}  "
                f"(target: {v['target_module']}) but never uses it"
                for v in launderers
            )
            + "\n\nThe import makes the target look reachable to the module "
            "gate above while nothing in it is actually used — this is how a "
            "half-finished deletion hides a dead module.\nDelete the import, "
            "or mark it '# noqa: F401 — <reason>' if it is a deliberate "
            "re-export or side-effecting import.\nRun ./bin/reach for the "
            "full vestigial-import list."
        )

    def test_star_imports_are_absent_from_the_tree(
        self, report: Dict[str, Any]
    ) -> None:
        """`from x import *` binds names the analyser cannot enumerate.

        Every such import is a blind spot for the vestigial check above,
        so they are reported separately rather than silently skipped. The
        tree has none today; this asserts it stays that way rather than
        having a hole open without anyone noticing.
        """
        stars = report["star_imports"]
        assert stars == [], (
            "Star import(s) found — bindings cannot be resolved, so the "
            "vestigial-import gate is blind to them:\n"
            + "\n".join(
                f"  - {s['module']}:{s['lineno']}  from {s['target_module']} "
                "import *"
                for s in stars
            )
            + "\n\nReplace with explicit imports."
        )
