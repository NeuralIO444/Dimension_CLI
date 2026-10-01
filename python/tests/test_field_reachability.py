# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_field_reachability.py — tests for the computed-but-never-read
field detector (issue #418).

The tool's whole value rests on one asymmetry: it must never report a LIVE
field as dead (that would send someone deleting a field Babysitter reads —
the exact silent-drop failure it exists to prevent), and it is allowed to
miss dead ones. These tests pin that asymmetry against the real tree, not
synthetic models, per CLAUDE.md's standing warning that synthetic fixtures
have masked JSX-contract bugs in this repo for a year at a time.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools import field_reachability as fr  # noqa: E402


class TestAgainstRealTree:
    """Runs the analyzer over the actual repo — no synthetic models."""

    def test_analysis_completes_and_finds_models(self):
        report = fr.analyze()
        assert report["models_scanned"] > 20, "should find the real models/ package"
        assert report["fields_declared"] > 200
        assert report["js_read_names"] > 0, "JSX/CEP scan must actually find something"

    def test_jsx_consumed_fields_are_never_reported_dead(self):
        """The load-bearing guarantee.

        These fields are read by Babysitter.jsx, not by Python. A
        Python-only analyzer would report every one as dead. If this test
        fails, the cross-language scan has broken and the tool has become
        actively dangerous.
        """
        report = fr.analyze()
        dead = {f["field"] for f in report["unread_fields"]}
        guarded = {f["field"] for f in report["self_guarded_fields"]}
        flagged = dead | guarded

        for field in (
            "conformed_transforms",
            "conformed_enabled",
            "conformed_keys",
            "content_tag",
            "world_bounds",
            "uid",
        ):
            assert field not in flagged, (
                f"{field!r} is consumed downstream but was flagged as unread — "
                "the cross-language reader scan is broken"
            )

    def test_known_dead_field_is_detected(self):
        """`paired_with` is documented in CLAUDE.md as deliberately spared:
        the pairing feature was deleted, but the wire field stays because
        `jsx_wire_manifest.py` is extra="forbid" and removing it hard-fails
        conform on manifests already on disk. Nothing reads it. It is the
        canonical true positive."""
        report = fr.analyze()
        dead = {f["field"] for f in report["unread_fields"]}
        assert "paired_with" in dead

    def test_archetype_now_has_a_genuine_reader(self):
        """`archetype` was the founding example that motivated this tool
        (issue #423): computed by surveyor.py AND by SovCore_Layer.jsx,
        with its only Python touch being
        `getattr(layer, "archetype", None) is None` — a guard deciding
        whether to write it, not a consumer of the value.

        Issue #423 closed that gap by surfacing `archetype` as a
        diagnostic in the conform report (logic/report_generator.py) and
        the CEP tagging panel (cep/js/tagging_v2.js). It must now show up
        as neither unread nor self-guarded.
        """
        report = fr.analyze()
        dead = {f["field"] for f in report["unread_fields"]}
        guarded = {f["field"] for f in report["self_guarded_fields"]}
        assert "archetype" not in dead
        assert "archetype" not in guarded

    def test_every_flagged_field_reports_a_real_declaration_site(self):
        report = fr.analyze()
        for item in report["unread_fields"] + report["self_guarded_fields"]:
            assert item["declared_in"], f"{item['field']} has no declaration site"
            first = item["declared_in"][0]
            assert first["module"].startswith("models"), first
            assert first["line"] > 0


class TestReadDiscrimination:
    """The JS scanner must tell reading from writing."""

    def test_js_scan_finds_reads(self):
        reads = fr.collect_js_reads()
        assert len(reads) > 100, "JSX scan returned implausibly little"

    def test_python_scan_separates_real_reads_from_self_guards(self):
        py = fr.collect_python_reads()
        assert "real" in py and "self_guard" in py
        # A name genuinely read somewhere must never land in self_guard.
        assert not (py["real"] & py["self_guard"])

    def test_scope_collector_flags_write_then_guard_read_as_self_guard(self, tmp_path):
        """Unit-level proof of the self-guard mechanism itself, independent
        of whichever real field currently sits in that category (archetype
        no longer does, as of issue #423 — see
        test_archetype_now_has_a_genuine_reader above). Mirrors the exact
        shape that made archetype the founding example: a function that
        writes a field only to decide whether it already has a value."""
        import ast

        src = (
            "def classify(layer):\n"
            "    if getattr(layer, 'made_up_diagnostic_field', None) is None:\n"
            "        layer.made_up_diagnostic_field = 'x'\n"
        )
        c = fr._ScopeCollector()
        c.visit(ast.parse(src))
        stores = c.scope_stores.get("classify", set())
        reads = c.scope_reads.get("classify", set())
        assert "made_up_diagnostic_field" in stores
        assert "made_up_diagnostic_field" in reads


class TestReportShape:
    def test_human_render_does_not_crash_and_mentions_counts(self):
        report = fr.analyze()
        text = fr._render_human(report)
        assert "field-reachability report" in text
        assert "pydantic models scanned" in text

    def test_main_runs_clean_in_both_modes(self, capsys):
        assert fr.main([]) == 0
        assert capsys.readouterr().out.strip()
        assert fr.main(["--json"]) == 0
        import json
        json.loads(capsys.readouterr().out)
