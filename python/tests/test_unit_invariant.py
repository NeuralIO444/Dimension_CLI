# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_unit_invariant.py
U3 Phase C — THE INVARIANT TEST.

Machine-enforces the U3 mission's core contract: once the placement
resolution decides a unit is SEALED (a nested precomp) or PRESERVING
(a camera scene), EVERY sibling pass that writes transform-bearing
output must respect that decision — never remap a sealed static, never
emit sealed keys, never drop a sealed mirror's keep_source_dims, never
let SOE move a preserving/sealed member.

Modeled on test_camera_parity_invariant.py's structure (see its
docstring + CLAUDE.md's "camera-math parity invariant" sharp edge):
one artifact registry enumerated in ONE place
(`AUDITED_ARTIFACT_KEYS`), a coverage assertion that fails loudly when
a future pass adds a new `conformed_*` key without registering it
here, and a behavioral assertion per artifact.

Fixture prep mirrors test_scene_preserve_layout.py's
`TestSealedUnitsFullPipeline` exactly (same donor/nested temporal-data
graft, reused not reinvented) — loads
fixtures/session_2026_07_02/87n_fresh_manifest.json, grafts "YOU WANT
Outlines"'s temporal_data onto nested "Cyan Solid 2", runs the real
orchestrator.py end-to-end via subprocess under
`--preset builtin:tiktok_video --layout auto` (the preset is REQUIRED
— bare --width/--height never resolves a safe-zone mask, which would
make the SOE assertion vacuous).

Ground truth for which comps are sealed/preserving is derived from the
SAME pure function the engine uses (`compute_placement_resolution`),
not hardcoded comp ids — same "derive expectations from source values,
not hardcoded coordinates" discipline as the rest of the scene-preserve
test suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from models.scrape_manifest import ScrapeManifest  # noqa: E402
from core.placement_units import compute_placement_resolution  # noqa: E402

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
_FRESH = os.path.join(_FIXTURES, "session_2026_07_02", "87n_fresh_manifest.json")

REPO_PY = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# ── THE artifact registry — enumerated in ONE place ─────────────────
# A future pass that writes a new transform-bearing `conformed_*` key
# on a chunk layer WITHOUT adding it here fails the coverage assertion
# below loudly, instead of silently escaping every check in this file.
AUDITED_ARTIFACT_KEYS = {
    "conformed_transforms",
    "conformed_keys",
    "conformed_effects",
    "conformed_layer_styles",
    "conformed_expressions",
    # PR-V2. Not transform data — a per-layer visibility instruction for
    # Babysitter (`variant:` directives). Registered here because it is in
    # the `conformed_*` namespace the coverage gate sweeps, and because a
    # sealed unit whose members disagreed about visibility would violate
    # THE INVARIANT just as surely as one whose members disagreed about
    # scale. core/variant_gate.py refuses directives on sealed members for
    # that reason; test_variants.py asserts it.
    "conformed_enabled",
}


def _load_fixture_data() -> dict:
    with open(_FRESH) as f:
        return json.load(f)


def _graft_nested_temporal_data(data: dict) -> tuple:
    """Mirrors TestSealedUnitsFullPipeline's graft exactly: plant real
    keyframe data on a layer INSIDE the innermost sealed precomp so the
    invariant test has something non-trivial to prove was skipped.
    Returns (root_cid, nested_cid, nested_layer_name)."""
    root_cid = data["layers"][0]["containing_comp_id"]
    donor = next(l for l in data["layers"] if l["name"] == "YOU WANT Outlines")
    nested = next(
        l for l in data["layers"]
        if l.get("containing_comp_id") not in (None, root_cid)
        and l["name"] == "Cyan Solid 2"
    )
    nested["temporal_data"] = json.loads(json.dumps(donor["temporal_data"]))
    return root_cid, nested["containing_comp_id"], nested["name"]


def _graft_expressions(data: dict, root_cid: int) -> None:
    """Companion graft for the expression-scaling sealed-check
    regression (BUGS.md "Recently closed 2026-07-08"). Mirrors
    `_graft_nested_temporal_data`'s mutate-in-place pattern: plant a
    scalable AE expression (a top-level array literal — the only shape
    `core.expression_parser.ExpressionScaler` actually rewrites, see
    test_expression_scaling.py) on one layer INSIDE the innermost
    sealed precomp ("Cyan Solid 2") and one layer in the root
    (non-sealed) scene ("YOU WANT Outlines"), so the new
    sealed-vs-non-sealed assertion has real material to prove was
    respected instead of being vacuously true."""
    outlines = next(l for l in data["layers"] if l["name"] == "YOU WANT Outlines")
    nested = next(
        l for l in data["layers"]
        if l.get("containing_comp_id") not in (None, root_cid)
        and l["name"] == "Cyan Solid 2"
    )
    outlines["expressions"] = {"position": "[300, 400]"}
    nested["expressions"] = {"position": "[100, 200]"}


def _build_project_structure(data: dict) -> dict:
    """Derive a minimal project_structure.json from the manifest's own
    source_item entries (the nesting-doll wrapper metadata) — no
    hardcoded comp ids/dims, so fixture drift can't silently invalidate
    the mirror-tree assertion."""
    root_cid = data["layers"][0]["containing_comp_id"]
    pi = data["project_info"]
    comps = [{
        "id": root_cid, "name": pi["name"],
        "width": pi["width"], "height": pi["height"],
        "fps": pi.get("fps") or 24.0, "is_render_target": True,
        "layer_count": 0,
    }]
    seen = {root_cid}
    for l in data["layers"]:
        si = l.get("source_item")
        if si and si.get("kind") == "comp":
            cid = si.get("nested_comp_id") or si.get("id")
            if cid is not None and cid not in seen:
                seen.add(cid)
                comps.append({
                    "id": cid,
                    "name": si.get("name") or str(cid),
                    "width": si.get("width") or 1920,
                    "height": si.get("height") or 1080,
                    "fps": si.get("frame_rate") or 24.0,
                    "is_render_target": False,
                    "layer_count": 0,
                })
    return {
        "status": "OK", "schema_version": "1.0",
        "scan_meta": {"scanned_at": "2026-07-02T12:00:00Z"},
        "comps": comps, "references": [],
    }


def _run_conform(tmp_path: Path, data: dict, env: dict | None = None) -> dict:
    """Writes manifest.json + project_structure.json to tmp_path, runs
    orchestrator.py end-to-end, returns a dict of every loaded
    artifact this test needs.

    `env`, when given, replaces the subprocess's environment (e.g. to
    point `logic.preferences_state._state_path()` at an isolated tmp
    HOME instead of the real developer machine's prefs file — see
    `_make_expr_scaling_env`). Defaults to None so subprocess.run
    inherits the current process env exactly as before, unchanged for
    every existing caller."""
    src_path = tmp_path / "manifest.json"
    src_path.write_text(json.dumps(data))
    (tmp_path / "project_structure.json").write_text(
        json.dumps(_build_project_structure(data)))

    proc = subprocess.run(
        [sys.executable, os.path.join(REPO_PY, "orchestrator.py"),
         "--source", str(src_path),
         "--width", "1080", "--height", "1920",
         "--preset", "builtin:tiktok_video",
         "--layout", "auto", "--no-report",
         "--output", str(tmp_path / "chunks")],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=300,
        env=env)
    assert proc.returncode == 0, proc.stderr[-4000:]

    chunk_manifest = json.loads((tmp_path / "chunk_manifest.json").read_text())
    layers = []
    for cp in chunk_manifest["chunk_paths"]:
        cdata = json.loads(open(cp, encoding="utf-8").read())
        layers.extend(cdata.get("layers", cdata) if isinstance(cdata, dict)
                      else cdata)

    soe_path = tmp_path / "soe_corrections.json"
    soe_corrections = (json.loads(soe_path.read_text())
                       if soe_path.is_file() else [])

    return {
        "layers": layers,
        "chunk_manifest": chunk_manifest,
        "mirror_tree": chunk_manifest.get("mirror_tree") or [],
        "soe_corrections": soe_corrections,
        "stdout": proc.stdout,
    }


@pytest.fixture(scope="module")
def conform_result(tmp_path_factory):
    """Runs the real pipeline ONCE for this module — every test below
    reads a different facet of the same conform run (cheaper than a
    subprocess per assertion, and guarantees every artifact came from
    the exact same run)."""
    data = _load_fixture_data()
    root_cid, nested_cid, nested_name = _graft_nested_temporal_data(data)

    manifest = ScrapeManifest.model_validate(data)
    resolution = compute_placement_resolution(manifest, layout="auto")

    tmp_path = tmp_path_factory.mktemp("unit_invariant")
    result = _run_conform(tmp_path, data)
    result["root_cid"] = root_cid
    result["nested_cid"] = nested_cid
    result["nested_name"] = nested_name
    result["sealed_cids"] = resolution.sealed_precomp_cids
    result["preserving_cids"] = resolution.preserving_scene_cids
    assert result["sealed_cids"], (
        "fixture must exercise at least one sealed precomp — resolution "
        "computed zero; fixture or layout resolution has drifted")
    assert result["preserving_cids"], (
        "fixture must exercise the preserving root scene — resolution "
        "computed zero; fixture or layout resolution has drifted")
    return result


def _by_uid(layers):
    return {l.get("uid"): l for l in layers if l.get("uid")}


def _by_name(layers, name):
    hits = [l for l in layers if l.get("name") == name]
    assert hits, f"layer {name!r} not found in conform output"
    return hits[0]


# ── Coverage assertion ───────────────────────────────────────────────


def test_conformed_key_coverage(conform_result):
    """Every `conformed_*` key on every chunk layer must be registered
    in AUDITED_ARTIFACT_KEYS. A future pass adding
    `conformed_whatever` without updating this set fails here loudly —
    the whole point of enumerating artifacts in ONE place."""
    for layer in conform_result["layers"]:
        present = {k for k in layer if k.startswith("conformed_")}
        unregistered = present - AUDITED_ARTIFACT_KEYS
        assert not unregistered, (
            f"layer {layer.get('name')!r} carries unregistered "
            f"transform-bearing key(s) {unregistered} — register them in "
            f"AUDITED_ARTIFACT_KEYS (test_unit_invariant.py) before "
            f"shipping this pass")


# ── Statics: sealed members equal source; root scene members don't ──


def test_sealed_statics_equal_source(conform_result):
    data = _load_fixture_data()
    # Re-derive uid->source lookup from the (already-grafted, but
    # graft only touches temporal_data — statics untouched) fixture.
    root_cid, _, _ = _graft_nested_temporal_data(data)
    src_by_uid = _by_uid(data["layers"])
    sealed_cids = conform_result["sealed_cids"]

    checked = 0
    for out_layer in conform_result["layers"]:
        if out_layer.get("containing_comp_id") not in sealed_cids:
            continue
        src = src_by_uid.get(out_layer.get("uid"))
        assert src is not None, out_layer.get("name")
        tf = out_layer.get("conformed_transforms") or {}
        assert tf.get("position") == pytest.approx(
            src.get("position") or [0, 0, 0], abs=1e-6), out_layer["name"]
        assert tf.get("scale") == pytest.approx(
            src.get("scale") or [100, 100, 100], abs=1e-6), out_layer["name"]
        assert tf.get("anchor") == pytest.approx(
            src.get("anchor") or [0, 0, 0], abs=1e-6), out_layer["name"]
        checked += 1
    assert checked >= 5, "fixture must exercise several sealed-unit layers"


def test_root_scene_member_gets_scene_remap(conform_result):
    """Sanity counterpart: a root-comp member MUST differ from its
    source position (it went through the uniform scene-preserve
    remap) — proves the sealed-statics equality above isn't trivially
    true because nothing moved anywhere."""
    outlines = _by_name(conform_result["layers"], "YOU WANT Outlines")
    tf = outlines.get("conformed_transforms") or {}
    src_pos = outlines.get("position") or [0, 0, 0]
    assert tf.get("position") != pytest.approx(src_pos, abs=0.5), (
        "root-scene member must be remapped, not passed through verbatim")


# ── Keys: absent for sealed, present for root scene ─────────────────


def test_sealed_keys_absent_root_keys_present(conform_result):
    nested = _by_name(conform_result["layers"], conform_result["nested_name"])
    assert not nested.get("conformed_keys"), (
        "sealed nested layer must NOT emit conformed keys")

    outlines = _by_name(conform_result["layers"], "YOU WANT Outlines")
    ck = outlines.get("conformed_keys")
    assert ck, "root-scene animated layer must still emit conformed keys"


# ── Effects / layer styles: never conformed (global policy) ─────────


def test_effects_and_styles_never_conformed(conform_result):
    assert not any(l.get("conformed_effects") for l in conform_result["layers"]), (
        "effect params must not be conformed on the transform-scaled path")
    assert not any(l.get("conformed_layer_styles") for l in conform_result["layers"]), (
        "layer-style params must not be conformed on the transform-scaled path")


# ── Mirror tree: keep_source_dims on every sealed precomp entry ─────


def test_mirror_tree_keep_source_dims_on_sealed_entries(conform_result):
    mirror_tree = conform_result["mirror_tree"]
    assert mirror_tree, "fixture's project_structure.json must produce a mirror tree"
    by_cid = {e["source_comp_id"]: e for e in mirror_tree}
    sealed_cids = conform_result["sealed_cids"]
    root_cid = conform_result["root_cid"]

    assert by_cid[root_cid]["keep_source_dims"] is False, (
        "root must ALWAYS resize to the conform target")
    checked = 0
    for cid in sealed_cids:
        entry = by_cid.get(cid)
        assert entry is not None, f"sealed cid {cid} missing from mirror tree"
        assert entry["keep_source_dims"] is True, (
            f"sealed precomp {entry['source_comp_name']!r} must keep source dims")
        checked += 1
    assert checked == len(sealed_cids)


# ── SOE: never moves a sealed/preserving-unit member ─────────────────


def test_soe_never_moves_sealed_or_preserving_member(conform_result):
    sealed_cids = conform_result["sealed_cids"]
    preserving_cids = conform_result["preserving_cids"]
    uid_to_cid = {l.get("uid"): l.get("containing_comp_id")
                 for l in conform_result["layers"]}

    corrections = conform_result["soe_corrections"]
    assert corrections, (
        "fixture must exercise at least one SOE correction record (even "
        "a structural skip) — otherwise this assertion is vacuous")

    protected_checked = 0
    for c in corrections:
        cid = uid_to_cid.get(c.get("layer_uid"))
        if cid in sealed_cids or cid in preserving_cids:
            assert c["original_position"] == c["corrected_position"], (
                f"SOE moved a sealed/preserving-unit member: {c['layer_name']!r} "
                f"({c['original_position']} -> {c['corrected_position']})")
            protected_checked += 1
    assert protected_checked >= 1, (
        "fixture must exercise at least one sealed/preserving-unit "
        "correction record to make this assertion non-vacuous")

    # A scene-preserve Run Warning must have actually fired (the union
    # AABB genuinely overlaps the tiktok safe zone for this fixture) —
    # proves SOE really evaluated the preserving unit, not just skipped
    # silently with nothing to check.
    warning_lines = [
        json.loads(line) for line in conform_result["stdout"].splitlines()
        if line.strip().startswith("{") and '"type": "warning"' in line
    ]
    scene_warnings = [w for w in warning_lines
                      if "scene-preserve" in w.get("msg", "")]
    assert scene_warnings, (
        "expected at least one scene-preserve Run Warning on stdout — "
        "SOE should have evaluated the preserving unit's union AABB "
        "against the tiktok safe zone and found a violation")


# ── Report rows: src==dst for sealed (same fields report_generator.py
#    reads at ~report_generator.py:1057-1061: layer.get("position") as
#    src, conformed_transforms.get("position", src) as dst — checked
#    directly on the chunk layer dicts since --no-report skips HTML
#    generation; no new report_generator.py plumbing needed) ────────


def test_report_row_src_equals_dst_for_sealed(conform_result):
    sealed_cids = conform_result["sealed_cids"]
    checked = 0
    for layer in conform_result["layers"]:
        if layer.get("containing_comp_id") not in sealed_cids:
            continue
        tf = layer.get("conformed_transforms") or {}
        src_pos = layer.get("position") or [0, 0, 0]
        src_anc = layer.get("anchor") or [0, 0, 0]
        dst_pos = tf.get("position", src_pos)
        dst_anc = tf.get("anchor", src_anc)
        assert dst_pos == pytest.approx(src_pos, abs=1e-6), layer["name"]
        assert dst_anc == pytest.approx(src_anc, abs=1e-6), layer["name"]
        checked += 1
    assert checked >= 5


# ── Expression scaling: sealed units never get conformed_expressions ─
#
# Follow-up from BUGS.md "Recently closed (2026-07-08)" — the sealed
# check crash (`is_sealed` never existed on PlacementResolution) is
# fixed in stages/expression.py, but `conformed_expressions` was never
# registered here, the one machine-enforced guarantee that a sibling
# pass can't scatter a sealed unit. This closes that gap with a real
# behavioral assertion, not just the coverage-list addition above.
#
# `expression_scaling_enabled` defaults to False (preferences_state.py)
# pending real-AE verification, so this needs its own subprocess run
# with the pref force-enabled — the module-scoped `conform_result`
# fixture above runs with the pass OFF and must keep proving that
# (test_conformed_key_coverage is a coverage check, not a behavior
# check; it stays green with the flag off and is left untouched).
#
# `_run_conform` shells out via subprocess.run, so in-process
# monkeypatching of `logic.preferences_state.preferences` (the pattern
# test_expression_scaling.py uses) never reaches the child process —
# preferences_state.py reads a real state.json off disk, resolved from
# Path.home(). We isolate HOME for just this subprocess (via env=) and
# pre-seed a throwaway state.json there — never touching the real
# developer machine's prefs file at
# `~/Library/Application Support/NeuralIO_Dimension/state.json`.


def _make_expr_scaling_env(tmp_path_factory) -> dict:
    """Isolated HOME with expression_scaling_enabled forced True.

    `preferences_state._state_path()` resolves to
    `<home>/Library/Application Support/NeuralIO_Dimension/state.json`
    whenever `<home>/Library/Application Support` exists on disk at
    call time, else falls back to
    `<home>/.config/NeuralIO_Dimension/state.json`. A freshly minted
    tmp dir starts with neither — BUT `studio_profile_registry.py`
    creates `<home>/Library/Application Support/NeuralIO_Dimension/
    profiles/` as a side effect of its own (unrelated) profile-seeding
    logic early in this same orchestrator run, *before* the expression
    stage's first preferences read. That flips `_state_path()`'s
    resolution from the `.config` fallback to the mac path mid-run —
    confirmed empirically (a `.config`-only seed silently reads back
    the all-defaults dict, so `expression_scaling_enabled` is False
    and the whole test is vacuous). Seeding BOTH candidate paths makes
    this robust to that ordering regardless of which one wins."""
    fake_home = tmp_path_factory.mktemp("expr_scaling_home")
    payload = json.dumps({"expression_scaling_enabled": True})
    for rel_parts in (
        (".config", "NeuralIO_Dimension"),
        ("Library", "Application Support", "NeuralIO_Dimension"),
    ):
        cfg_dir = fake_home.joinpath(*rel_parts)
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "state.json").write_text(payload)
    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    return env


def test_expression_scaling_respects_sealed_units(tmp_path_factory):
    """Real-subprocess regression test for the 2026-07-08 sealed-check
    crash fix: with expression_scaling_enabled forced True and S != 1,
    a layer INSIDE a sealed precomp must never receive a
    `conformed_expressions` block, while a layer in the (non-sealed)
    root scene must."""
    data = _load_fixture_data()
    root_cid, nested_cid, nested_name = _graft_nested_temporal_data(data)
    _graft_expressions(data, root_cid)

    env = _make_expr_scaling_env(tmp_path_factory)
    tmp_path = tmp_path_factory.mktemp("expr_scaling_run")
    result = _run_conform(tmp_path, data, env=env)

    # Sanity: the pass must not have silently failed the way it did in
    # production before the fix (BUGS.md — "Expression scaling pass
    # failed: ..." run_warnings entry). If this fires, the assertions
    # below would be vacuously true for the wrong reason.
    warning_lines = [
        json.loads(line) for line in result["stdout"].splitlines()
        if line.strip().startswith("{") and '"type": "warning"' in line
    ]
    expr_failures = [w for w in warning_lines
                     if "Expression scaling pass failed" in w.get("msg", "")]
    assert not expr_failures, (
        f"expression scaling pass raised instead of running cleanly: "
        f"{expr_failures}")

    nested = _by_name(result["layers"], nested_name)
    assert not nested.get("conformed_expressions"), (
        "sealed nested layer must NOT receive a conformed_expressions "
        "block — the sealed check must skip it before scaling runs")

    outlines = _by_name(result["layers"], "YOU WANT Outlines")
    ce = outlines.get("conformed_expressions")
    assert ce, (
        "root-scene (non-sealed) layer must receive a conformed_expressions "
        "block — proves the pass actually ran, not just that the flag "
        "was ignored")
    assert ce["position"] != outlines["expressions"]["position"], (
        "conformed expression must differ from the source literal — "
        "S != 1.0 for this HD->tiktok conform")
