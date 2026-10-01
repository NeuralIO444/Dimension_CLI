# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_sovereign_core_recursive_scrape.py — Slot 12.5 Stage B / item 1.

Static contract tests for the recursive comp walk in `Sovereign_Core.jsx`.

Pre-Stage-B, `DIMENSION.core.atoms.scrapeUnified` scraped only the active
comp — on a nested production comp (Free Parallax: 195 layers / 54 comps)
it surfaced 2 layers and silently dropped the other 193. Q1 ships deep
conform by default; the scrape feeds it. Stage B refactors `scrapeUnified`
into a BFS walker that visits the active comp and every reachable
precomp source (de-duped by comp.id per Q3A), and stamps the schema 5.1
breadcrumb fields on every emitted layer.

These tests pattern-check the JSX source. Real AE round-trip
verification is Matt's smoke step (Free Parallax: should now scrape
close to 195 layers across 54 comps).
"""

from __future__ import annotations

import re
from pathlib import Path


_SOV_CORE_PATH = (
    Path(__file__).resolve().parents[2]
    / "Scripts" / "Dimension_Assets" / "Sovereign_Core.jsx"
)


def _read() -> str:
    return _SOV_CORE_PATH.read_text(encoding="utf-8")


class TestScrapeAcceptsOptionalCompArgument:
    def test_scrape_signature_has_optcomp(self):
        src = _read()
        # Signature: `DIMENSION.core.atoms.scrape = function(scrapeMode, optComp)`
        sig = re.search(
            r"DIMENSION\.core\.atoms\.scrape\s*=\s*function\s*\(([^)]*)\)",
            src,
        )
        assert sig is not None, "Could not locate scrape signature."
        args = [a.strip() for a in sig.group(1).split(",")]
        assert args[:2] == ["scrapeMode", "optComp"], (
            f"Unexpected scrape args: {args!r}. Stage B requires "
            "scrape(scrapeMode, optComp) so the recursive orchestrator can "
            "scrape a specific comp instead of activeItem."
        )

    def test_scrape_uses_optcomp_or_activeitem(self):
        src = _read()
        assert "optComp || _project.activeItem" in src, (
            "scrape() must use `optComp || _project.activeItem` so the "
            "no-arg path stays back-compat (activeItem) while the "
            "orchestrator can pass an explicit comp."
        )

    def test_scrape_forces_standard_mode_when_optcomp_set(self):
        src = _read()
        # The pattern: `optComp ? "standard" : (scrapeMode || "standard")`.
        # Selection-aware scrape mode is meaningful only for the active comp.
        assert 'optComp ? "standard"' in src, (
            "Non-root visits must force standard mode — AE's per-comp "
            "selection state is meaningless for nested precomps."
        )


class TestScrapeUnifiedRecursive:
    def test_scrape_unified_delegates_to_recursive_walker(self):
        src = _read()
        # scrapeUnified body must call _scrapeRecursive.
        assert "DIMENSION.core.atoms._scrapeRecursive(rootComp" in src, (
            "scrapeUnified must delegate to _scrapeRecursive(rootComp, ...) "
            "so the walker drives recursion from the active comp."
        )

    def test_recursive_walker_exists(self):
        src = _read()
        assert "DIMENSION.core.atoms._scrapeRecursive = function" in src

    def test_per_comp_merge_helper_exists(self):
        src = _read()
        assert "DIMENSION.core.atoms._scrapeAndMergeOneComp = function" in src

    def test_walker_dedupes_by_comp_id(self):
        """Q3A: shared precomps share one conformed copy. The walker
        must de-dupe by comp.id so a precomp referenced from N parents
        scrapes exactly once."""
        src = _read()
        assert "seenCompIds" in src, (
            "Recursive walker must maintain a seenCompIds map to satisfy "
            "Q3A (shared precomps scrape once)."
        )

    def test_walker_stamps_cross_comp_fields(self):
        """Each layer emitted by the walker must carry the four
        schema 5.1 breadcrumb fields. The walker stamps them in one
        place; verify each name appears in the stamp block."""
        src = _read()
        stamp_block = src[src.index("Stamp cross-comp fields"):]
        # Look at the next ~1000 chars after the comment marker.
        stamp_block = stamp_block[:1500]
        for field in (
            "containing_comp_id",
            "containing_comp_uid",
            "wrapper_layer_uid",
            "nesting_depth",
        ):
            assert field in stamp_block, (
                f"Walker's cross-comp stamp block missing `{field}`."
            )

    def test_walker_has_max_comps_safety_net(self):
        src = _read()
        assert "MAX_COMPS" in src, (
            "Recursive walker must have a MAX_COMPS visit cap as a "
            "defensive cycle/runaway guard (real projects are far below it; "
            "tripping the cap surfaces a structured error rather than "
            "infinite work)."
        )

    def test_walker_skips_self_referential_source(self):
        src = _read()
        # A comp whose layer's source is itself would create infinite
        # recursion if seenCompIds didn't already cover it. Belt-and-
        # suspenders self-reference guard.
        assert "self-reference guard" in src, (
            "Recursive walker should comment + guard against a layer "
            "whose source is its own containing comp."
        )

    def test_manifest_size_tripwire_is_5_mb(self):
        """R8 (scope-doc): flag-for-investigation at ~5 MB."""
        src = _read()
        assert "5 * 1024 * 1024" in src, (
            "scrapeUnified must check manifest size against the R8 "
            "5 MB tripwire and log when exceeded."
        )
        assert "R8" in src, "R8 should be cited in the tripwire log line."


class TestEs3CompatibilityOfNewCode:
    """Spot-check ES3 prohibitions on the new walker code. Matches the
    rules in CLAUDE.md and the same pattern used by
    test_babysitter_find_layer_by_uid::test_function_is_es3_compatible."""

    def _walker_body(self):
        src = _read()
        start = src.index("DIMENSION.core.atoms._scrapeRecursive = function")
        # Take through the end of _scrapeAndMergeOneComp — the next public
        # function after both walker helpers is scanProjectStructure.
        end = src.index("DIMENSION.core.atoms.scanProjectStructure")
        return src[start:end]

    def test_no_arrow_functions(self):
        assert "=>" not in self._walker_body(), (
            "Arrow functions are not ES3-compatible."
        )

    def test_no_template_literals(self):
        """Look for actual template-literal syntax `${...}` rather than
        any backtick — JSDoc / block comments commonly use backticks for
        markdown-style inline code (`varname`), which is not a template
        literal and is fine in ES3 contexts."""
        body = self._walker_body()
        # Strip block + line comments so JSDoc markdown backticks don't
        # false-flag. We're hunting for actual template literals: backtick-
        # delimited strings with ${expr} interpolation.
        no_block = re.sub(r"/\*[\s\S]*?\*/", "", body)
        no_line = re.sub(r"//[^\n]*", "", no_block)
        assert "${" not in no_line, (
            "Template literal interpolation `${...}` is not ES3-compatible. "
            "Use string concatenation instead."
        )

    def test_no_let_or_const(self):
        body = self._walker_body()
        assert not re.search(r"\blet\s+\w", body), "`let` is not ES3-compatible."
        assert not re.search(r"\bconst\s+\w", body), "`const` is not ES3-compatible."

    def test_uses_classic_for_loops(self):
        # Just verify at least one ES3 for-loop in the new code, as a
        # sanity check that the walker actually iterates the AE DOM.
        body = self._walker_body()
        assert re.search(r"for\s*\(\s*var\s+\w+\s*=", body), (
            "Recursive walker must use `for (var ...)` loops."
        )


# ---------------------------------------------------------------------------
# Stage B item 2 — effect-input layer-reference scrape
# ---------------------------------------------------------------------------

class TestEmissionCleanupItem0:
    """Slot 12.5 Stage C item 0 — three schema fields are defined on the
    Python side but were not emitted by the JSX scrape writer at the
    end of Stage B. The Stage B Free Parallax smoke surfaced the first;
    the other two are Stage B's filed follow-ups. Stage C item 0 closes
    all three before M1 (M1 may read continuously_rasterize)."""

    def test_scrape_v5_emits_schema_version_5_1(self):
        src = _read()
        # Two occurrences (success path + error path). Both now 5.1.
        five_one_lines = [
            line for line in src.splitlines()
            if "schema_version" in line and '"5.1"' in line
        ]
        five_zero_lines = [
            line for line in src.splitlines()
            if "schema_version" in line and '"5.0"' in line
        ]
        assert len(five_one_lines) >= 2, (
            "Sovereign_Core.jsx must stamp schema_version: \"5.1\" on "
            "both scrapeV5 paths (success + error) so the manifest "
            "carries the version explicitly rather than relying on "
            "Python's default on load."
        )
        # No straggling 5.0 emissions in the scraper itself (the
        # 5.0 string can still appear in test fixtures and prose).
        for line in five_zero_lines:
            assert "scrape_meta" not in line.lower() and "schema_version" not in line.lower() or "5.1" in line, (
                f"Stale schema_version: \"5.0\" emission in scraper: {line!r}"
            )

    def test_scrape_v5_emits_scraper_version_5_1(self):
        src = _read()
        # The scraper_version moved 5.0.0 → 5.1.0 alongside the schema bump.
        assert '"sovereign-5.1.0"' in src, (
            "scraper_version should track schema_version semver — "
            "5.0 → 5.1 to surface the recursive-scrape capability."
        )

    def test_scan_project_structure_emits_preserve_nested_flags(self):
        src = _read()
        # Locate the CompNode literal inside scanProjectStructure.
        scan_start = src.index("DIMENSION.core.atoms.scanProjectStructure = function")
        scan_end = src.index("DIMENSION.core.atoms.saveProjectStructureToFile")
        scan_body = src[scan_start:scan_end]
        for field in ("preserve_nested_frame_rate", "preserve_nested_resolution"):
            assert field in scan_body, (
                f"scanProjectStructure must emit `{field}` on every "
                "CompNode. Schema added these in Stage A; Stage C item "
                "0c wires the JSX writer."
            )

    def test_scan_project_structure_reads_ae_property_names(self):
        src = _read()
        scan_start = src.index("DIMENSION.core.atoms.scanProjectStructure = function")
        scan_end = src.index("DIMENSION.core.atoms.saveProjectStructureToFile")
        scan_body = src[scan_start:scan_end]
        # AE's property names are preserveNestedFrameRate / preserveNestedResolution.
        for ae_name in ("preserveNestedFrameRate", "preserveNestedResolution"):
            assert ae_name in scan_body, (
                f"CompNode emission must read AE's `{ae_name}` property."
            )


class TestSovCoreLayerEmitsContinuouslyRasterize:
    """SovCore_Layer::_scrapeFlags is the v5 walker's flags emitter.
    Stage A added `continuously_rasterize` to the Pydantic LayerFlags
    schema with default False; Stage C item 0b wires the JSX side to
    populate it from layer.collapseTransformation (same AE bit as
    collapse_transformations but semantically distinct for non-precomp
    vector layers)."""

    @staticmethod
    def _read_sov_layer():
        return (
            Path(__file__).resolve().parents[2]
            / "Scripts" / "Dimension_Assets" / "SovCore_Layer.jsx"
        ).read_text(encoding="utf-8")

    def test_scrape_flags_includes_continuously_rasterize(self):
        src = self._read_sov_layer()
        # Both fields read the same AE bit; verify both appear in
        # _scrapeFlags.
        flags_start = src.index("function _scrapeFlags")
        flags_end = src.index("\n    }", flags_start)
        flags_body = src[flags_start:flags_end]
        assert "continuously_rasterize" in flags_body, (
            "_scrapeFlags must emit continuously_rasterize so the "
            "LayerFlags schema field is populated by the JSX side."
        )
        assert "collapse_transformations" in flags_body, (
            "_scrapeFlags must continue emitting collapse_transformations "
            "(pre-Stage-C behavior preserved)."
        )

    def test_continuously_rasterize_reads_collapse_transformation_property(self):
        src = self._read_sov_layer()
        # Both fields read layer.collapseTransformation — same AE bit,
        # distinct semantics by layer source kind.
        match = re.search(
            r"continuously_rasterize:.*?function\s*\(\s*\)\s*\{[^}]+\}",
            src,
            re.DOTALL,
        )
        assert match is not None, "Could not locate continuously_rasterize field."
        assert "layer.collapseTransformation" in match.group(0), (
            "continuously_rasterize must read `layer.collapseTransformation` "
            "— the AE bit it represents for non-precomp vector / text / "
            "shape layers."
        )

    def test_collapse_transformations_is_gated_to_precomp_source(self):
        """Slot 12.5 Stage C corpus-smoke finding 2 (2026-05-17).

        `collapse_transformations` must be gated to layers whose source
        is a CompItem. AE's `AVLayer.collapseTransformation` DOM property
        is overloaded — for non-precomp AVLayers it represents the
        Continuously Rasterize bit, not Collapse Transformations.
        Reading the bit ungated leaks the rasterize state into the
        collapse field on text / shape / solid / footage layers.

        Mirrors the legacy scrape gating at Sovereign_Core.jsx::scrape."""
        src = self._read_sov_layer()
        # Locate the collapse_transformations field's function body
        # (multi-line body since Stage C corpus-smoke gating).
        match = re.search(
            r"collapse_transformations:\s*_safe\(\s*function\s*\(\s*\)\s*\{(.*?)\}\s*,\s*false\s*\)",
            src,
            re.DOTALL,
        )
        assert match is not None, (
            "Could not locate collapse_transformations field body."
        )
        body = match.group(1)
        # The gate has two halves: AVLayer instance check + source-is-CompItem.
        assert "instanceof AVLayer" in body, (
            "collapse_transformations gate must verify the layer is an "
            "AVLayer (CameraLayer / LightLayer have no source-is-CompItem)."
        )
        assert "instanceof CompItem" in body, (
            "collapse_transformations gate must verify "
            "`layer.source instanceof CompItem` — the bit means "
            "'collapse' only when the source is another comp."
        )
        # And it still reads the AE bit when the gate passes.
        assert "layer.collapseTransformation" in body, (
            "Gated path must still read `layer.collapseTransformation` "
            "when the layer is a precomp."
        )


class TestLayerReferenceScrape:
    """Static contract checks for the LAYER_INDEX property capture
    block added in Stage B item 2. Detection is generic via
    PropertyValueType.LAYER_INDEX — covers Set Matte, Displacement Map,
    Compound Blur, Calculations, Set Channels, Channel Combiner,
    CC Composite, and any future layer-index-bearing effect."""

    def test_layer_index_property_value_type_is_checked(self):
        src = _read()
        assert "PropertyValueType.LAYER_INDEX" in src, (
            "Effect-parade walk must check pvt === PropertyValueType.LAYER_INDEX "
            "to capture cross-layer references."
        )

    def test_layer_refs_array_is_per_layer(self):
        src = _read()
        # Initialized inside the per-layer try block, near the effects walk.
        assert "var layerRefs = []" in src, (
            "Per-layer layerRefs array must be initialized so each layer "
            "carries only its own LAYER_INDEX references."
        )

    def test_refs_resolve_uid_via_uid_by_index(self):
        src = _read()
        assert "uidByIndex[toIdx]" in src, (
            "LAYER_INDEX values resolve to UID via the same comp's "
            "uidByIndex map built in Phase 1 of scrape()."
        )

    def test_zero_index_treated_as_no_link(self):
        src = _read()
        # AE uses 0 to mean `<none>` for layer-index properties. The
        # capture block must guard against eVal === 0 / negative.
        assert "eVal > 0" in src, (
            "LAYER_INDEX value of 0 means `<none>` in AE — capture must "
            "skip it so a zero-valued property doesn't produce a ghost ref."
        )

    def test_ref_stamps_from_layer_uid_after_layer_uid_known(self):
        src = _read()
        # The from_layer_uid is left null inside the capture loop and
        # filled in after layerUID is resolved (layerUID is computed
        # earlier in the function, but the stamp happens explicitly).
        assert "layerRefs[lri].from_layer_uid = layerUID" in src, (
            "from_layer_uid must be stamped on each ref entry from the "
            "owning layer's resolved UID."
        )

    def test_layer_references_only_attached_when_non_empty(self):
        src = _read()
        assert "if (layerRefs.length > 0)" in src, (
            "Empty layer_references arrays shouldn't bloat the manifest — "
            "attach only when at least one reference was captured."
        )

    def test_ref_payload_carries_all_four_named_fields(self):
        """The captured payload must carry the four fields the
        implementation plan named, plus the `to_layer_index` diagnostic
        and the `_display_name` informational pair."""
        src = _read()
        # Find the layerRefs.push block.
        match = re.search(
            r"layerRefs\.push\(\{(.*?)\}\);",
            src,
            re.DOTALL,
        )
        assert match is not None, (
            "Could not locate the layerRefs.push payload block."
        )
        payload = match.group(1)
        for key in (
            "from_layer_uid",
            "to_layer_uid",
            "to_layer_index",
            "effect_match_name",
            "effect_display_name",
            "property_match_name",
            "property_display_name",
        ):
            assert key in payload, (
                f"layerRefs.push payload missing `{key}` — needed for "
                "Q8 detect-and-warn surface in the conform report."
            )
