# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_jsx_reachability.py — tests for the ExtendScript dead-code
detector (issue #419).

Same asymmetry the tool itself is built around: reporting LIVE JSX as dead
is unacceptable (someone would delete a function the bridge invokes by
string, an outage no Python test would catch), while missing dead JSX is
merely the status quo. These tests pin the unacceptable direction against
the real tree.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools import jsx_reachability as jr  # noqa: E402


class TestAgainstRealTree:
    def test_analysis_finds_the_real_jsx_tree(self):
        report = jr.analyze()
        assert report["jsx_files_scanned"] >= 15
        assert report["definitions_found"] > 100
        assert report["reached"] > report["unreached"]

    def test_bridge_dispatched_functions_are_never_reported_dead(self):
        """The load-bearing guarantee.

        These JSX functions are invoked by STRING from Python or CEP JS,
        never by JSX-side call syntax. A JSX-only parse would report every
        one as dead. If this fails, the cross-language scan has broken and
        the tool has become actively dangerous to act on.
        """
        report = jr.analyze()
        dead = {d["name"] for d in report["dead_definitions"]}
        for name in ("scrapeLayer", "scrapeUnified", "generateUID"):
            assert name not in dead, (
                f"{name!r} is bridge-dispatched but was flagged dead — "
                "the cross-language caller scan is broken"
            )

    def test_launcher_is_scanned_as_a_caller_source(self):
        """Regression guard for a real bug in this tool's first version.

        `Scripts/Dimension_Launcher.jsx` — the panel's main dispatcher —
        sits one directory ABOVE the code it calls. Scoping caller roots to
        `Scripts/Dimension_Assets/` missed it entirely and produced false
        'dead' reports for `exportFrame` and others. `exportFrame` is
        referenced only from the launcher, so it is the canary.
        """
        report = jr.analyze()
        dead = {d["name"] for d in report["dead_definitions"]}
        assert "exportFrame" not in dead

    def test_definition_lines_are_not_counted_as_their_own_callers(self):
        """If declaring counted as calling, nothing would ever be dead."""
        report = jr.analyze()
        assert report["unreached"] > 0, (
            "zero dead definitions is implausible and suggests definition "
            "lines are being counted as call sites"
        )

    def test_auditor_runfull_is_now_called(self):
        """The finding that justified building this tool -- now fixed.

        `Auditor.runFull` is the entire post-inject QC suite. It was
        uncalled from 2026-06-05 (an archived structural audit found and
        proposed the fix, never applied) until #427/#419 fixed it
        2026-09-05: `Babysitter_src/80_audit.jsx::performAudit` now
        conditionally delegates to `$.global.Auditor.runFull` via a new
        `_runFullAuditIfAvailable` helper, once its own structural check
        (mirror-tree or legacy single-comp) has passed.

        This test used to assert the opposite -- that `runFull` was
        reachability-dead -- specifically so that fixing the bug would
        break the test and force whoever fixed it to update this file
        rather than silently leave a stale regression marker behind. That
        moment has arrived. Do not flip this back without also reverting
        the `_runFullAuditIfAvailable` wiring in `80_audit.jsx`.
        """
        report = jr.analyze()
        dead = {d["name"] for d in report["dead_definitions"]}
        assert "runFull" not in dead

    def test_every_dead_entry_carries_a_real_source_location(self):
        report = jr.analyze()
        for item in report["dead_definitions"]:
            assert item["definitions"], f"{item['name']} has no definition site"
            first = item["definitions"][0]
            assert first["file"].endswith(".jsx")
            assert first["line"] > 0
            assert first["style"] in ("dotted", "literal", "plain")


class TestGeneratedBundleIsExcluded:
    def test_cep_jsx_bundle_is_not_double_counted(self):
        """`cep/jsx/` is a build artifact (CLAUDE.md), a byte copy of
        `Scripts/Dimension_Assets/`. Scanning it as source would double
        every definition."""
        defs = jr.collect_definitions()
        for sites in defs.values():
            for s in sites:
                assert not s["file"].startswith("cep/jsx/"), s


class TestReportShape:
    def test_human_render_works(self):
        text = jr._render_human(jr.analyze())
        assert "JSX reachability report" in text
        assert "definitions found" in text

    def test_main_runs_clean_in_both_modes(self, capsys):
        assert jr.main([]) == 0
        assert capsys.readouterr().out.strip()
        assert jr.main(["--json"]) == 0
        import json
        json.loads(capsys.readouterr().out)
