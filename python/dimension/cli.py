# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial.
# See LICENSE for full terms.

"""The unified `dimension` command tree (issue #5).

Handlers are deliberately dumb: parse args, call one `dimension.ops`
function, format the result. All business logic lives in ops so the
future Textual TUI (issue #15) can call the same functions.

Output contract:
  --json      exactly one JSON document on stdout, logs on stderr,
              meaningful exit codes (the machine face for MographJailed).
  (default)   human-readable summary on stdout (the human face).

Exit codes: 0 OK · 1 error · 2 usage · 65 LUT_UNSCRIPTABLE ·
69 AE_UNAVAILABLE (live-AE command, no After Effects reachable).

Commands tagged [headless] run anywhere. Commands under `ae` tagged
[live AE] need After Effects running with the Dimension poller.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Callable, Optional

from dimension import __version__
from dimension.common import (
    DimensionError,
    emit_human,
    emit_json,
    error_payload,
    log_to_stderr,
)
from dimension.ops import (
    ae as ae_ops,
    catalog as catalog_ops,
    conform as conform_ops,
    duplication as duplication_ops,
    lut as lut_ops,
    naming as naming_ops,
    occlusion as occlusion_ops,
    provenance as provenance_ops,
    safe_zone as safe_zone_ops,
    survey as survey_ops,
    target as target_ops,
)

HEADLESS = "[headless]"
LIVE_AE = "[live AE]"

EXIT_CODES_HELP = (
    "exit codes: 0 ok · 1 error · 2 usage · 65 LUT_UNSCRIPTABLE "
    "(#494: AE cannot script a LUT path) · 69 AE_UNAVAILABLE"
)


# ── output helpers ────────────────────────────────────────────────

def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _show_conform(r: dict[str, Any]) -> None:
    emit_human(f"conform complete: {r['chunk_count']} chunk(s)")
    emit_human(f"  chunk manifest: {r['chunk_manifest_path']}")
    emit_human(f"  conformed:      {r['conformed_path']}")
    if r["report_path"]:
        emit_human(f"  report:         {r['report_path']}")
    for w in r["run_warnings"]:
        emit_human(f"  warning: {w}")


def _show_survey(r: dict[str, Any]) -> None:
    emit_human(f"survey complete: {r['manifest']}")
    emit_human(f"  written: {'yes' if r['written'] else 'no (dry run)'}")
    stats = r["stats"]
    if isinstance(stats, dict):
        for key, value in stats.items():
            if key == "warnings":
                for w in value or []:
                    emit_human(f"  warning: {w}")
            else:
                emit_human(f"  {key}: {value}")


def _show_mask_plan(r: dict[str, Any]) -> None:
    t = r["target"]
    emit_human(f"preset {r['preset']} → {t['width']}x{t['height']} ({t['id']})")
    m = r["mask"]
    emit_human(f"  mask: {m['kind']} — {m['detail']}")
    if "zones" in r:
        z = r["zones"]
        emit_human(
            f"  zones: GO {_pct(z['go'])} · NUDGE {_pct(z['nudge'])} "
            f"· CUTOFF {_pct(z['cutoff'])}"
        )


def _show_zones(r: dict[str, Any]) -> None:
    z = r["zones"]
    t = r["target"]
    emit_human(f"{r['preset']} ({t['width']}x{t['height']}):")
    emit_human(f"  GO     {_pct(z['go'])}")
    emit_human(f"  NUDGE  {_pct(z['nudge'])}")
    emit_human(f"  CUTOFF {_pct(z['cutoff'])}")


def _show_occlusion_check(r: dict[str, Any]) -> None:
    emit_human(
        f"occlusion check: {r['layers_seen']} layer(s), "
        f"{r['correction_count']} correction(s)"
    )
    for c in r["corrections"][:25]:
        emit_human(
            f"  [{c.get('layer_index')}] {c.get('layer_name')}: "
            f"{c.get('zone_hit')} via {c.get('strategy')}"
        )
    if r["correction_count"] > 25:
        emit_human(f"  … and {r['correction_count'] - 25} more (see --json)")


def _show_naming(r: dict[str, Any]) -> None:
    emit_human(f"{r['source']} → {r['name']}  (version {r['version']})")


def _show_lut_validate(r: dict[str, Any]) -> None:
    emit_human(
        f"LUT ok: {r['source_format']}, {r['grid_size']}³ grid, "
        f"{r['bit_depth']}-bit"
    )
    if r.get("title"):
        emit_human(f"  title: {r['title']}")


def _show_preview(r: dict[str, Any]) -> None:
    if not r["available"]:
        emit_human(f"no duplication needed: {r['reason']}")
        return
    emit_human(
        f"duplication plan: {r['duplicate_count']} duplicate(s), "
        f"{r['rewire_count']} rewire(s)"
    )
    for d in r["duplicates"]:
        emit_human(f"  {d['original_name']} → {d['duplicate_name']} ({d['reason']})")
    if r["rewires_truncated"]:
        emit_human("  … rewire list truncated (see --json)")


def _show_cleanup(r: dict[str, Any]) -> None:
    emit_human(
        f"cleanup report (READ-ONLY — nothing deleted): "
        f"{r['total_orphans_count']} orphan candidate(s) of "
        f"{r['total_comps_scanned']} comps scanned"
    )
    emit_human(f"  provenance names loaded: {r['provenance_names_loaded']}")
    emit_human(f"  est. memory freed: {r['estimated_memory_freed_mb']:.1f} MB")
    for c in r["orphaned_candidates"][:20]:
        emit_human(f"  - {c.get('name')} ({c.get('reason', '')})")
    if len(r["orphaned_candidates"]) > 20:
        emit_human("  … truncated (see --json)")


def _show_provenance_duplicates(r: dict[str, Any]) -> None:
    emit_human(f"provenance db: {r['db']} ({'found' if r['db_exists'] else 'missing'})")
    emit_human(f"  {r['duplicate_count']} Babysitter-created comp(s) on record")
    for name in r["duplicates"][:30]:
        emit_human(f"  - {name}")
    if r["duplicate_count"] > 30:
        emit_human("  … truncated (see --json)")


def _show_provenance_runs(r: dict[str, Any]) -> None:
    emit_human(f"run history: {r['run_count']} run(s)")
    for run in r["runs"][:15]:
        emit_human(
            f"  #{run['id']} {run['session']} — {run['status']} "
            f"({run['started_at']})"
        )


def _show_presets(r: dict[str, Any]) -> None:
    emit_human(f"{r['preset_count']} preset(s):")
    for p in r["presets"]:
        dims = f"{p['width']}x{p['height']}" if p["width"] else "?"
        emit_human(f"  {p['id']}: {p['label']} ({dims})")


def _show_preset(r: dict[str, Any]) -> None:
    emit_human(f"{r['id']}: {r['label']}")
    emit_human(f"  dims: {r['width']}x{r['height']}  aspect: {r['aspect_label']}")
    if r.get("subcategory"):
        emit_human(f"  subcategory: {r['subcategory']}")
    if r.get("channel"):
        emit_human(f"  channel: {r['channel']}")


def _show_profiles(r: dict[str, Any]) -> None:
    emit_human(f"{r['profile_count']} studio profile(s), active: {r['active_profile']}")
    for p in r["profiles"]:
        emit_human(f"  {p['id']}: {p['name']}")


def _show_target(r: dict[str, Any]) -> None:
    emit_human(f"target {r['id']}: {r['label']} ({r['width']}x{r['height']})")


def _show_ae_probe(r: dict[str, Any]) -> None:
    emit_human(f"AE reachable: {'yes' if r['ready'] else 'no'}")
    emit_human(f"  socket: {'listening' if r['socket_listening'] else 'closed'}")
    hb = r["heartbeat"]
    emit_human(f"  poller heartbeat: {'fresh' if hb['fresh'] else hb['reason']}")
    if r.get("hint"):
        emit_human(f"  → {r['hint']}")


# ── dispatch ──────────────────────────────────────────────────────

def _run(args: argparse.Namespace, op: Callable[[], dict[str, Any]],
         show: Callable[[dict[str, Any]], None]) -> int:
    """Call one op, format the result, return the process exit code.

    DimensionError becomes a structured error payload; anything
    unexpected becomes INTERNAL (exit 1) — stdout stays parseable
    JSON under --json no matter what the engine throws.
    """
    try:
        result = op()
    except DimensionError as e:
        if args.json:
            emit_json(error_payload(e))
        else:
            emit_human(f"error [{e.code}]: {e}")
        return e.exit_code
    except Exception as e:  # noqa: BLE001 — last-resort contract guard
        logging.getLogger("dimension").exception("unhandled op failure")
        err = DimensionError(f"internal error: {e}", code="INTERNAL")
        if args.json:
            emit_json(error_payload(err))
        else:
            emit_human(f"error [INTERNAL]: {e}")
        return err.exit_code
    if args.json:
        emit_json(result)
    else:
        show(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dimension",
        description=(
            "Dimension format-conform engine — tag once, conform everywhere.\n"
            f"{HEADLESS} commands run anywhere; `ae` commands {LIVE_AE} need "
            "After Effects with the Dimension poller.\n" + EXIT_CODES_HELP
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--json", action="store_true",
                   help="machine-readable JSON on stdout; logs go to stderr")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                   help="engine log level (logs always go to stderr)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    # — conform —
    c = sub.add_parser("conform", help=f"run the conform pipeline {HEADLESS}")
    c.add_argument("--source", required=True, help="scrape_manifest.json path")
    c.add_argument("--preset", default=None)
    c.add_argument("--profile", default=None)
    c.add_argument("--width", type=int, default=None)
    c.add_argument("--height", type=int, default=None)
    c.add_argument("--duration", type=float, default=None)
    c.add_argument("--fps", type=float, default=None)
    c.add_argument("--mode", default="Fit",
                   choices=["Auto", "Fit", "Fill", "Stretch"])
    c.add_argument("--layout", default="tags", choices=["tags", "scene", "auto"])
    c.add_argument("--bleed", type=float, default=0.0)
    c.add_argument("--output", default="Chunks")
    c.add_argument("--no-report", action="store_true")
    c.add_argument("--allow-state-hash-bypass", action="store_true")
    c.set_defaults(_op="conform")

    # — survey —
    s = sub.add_parser("survey", help=f"classify layers on a manifest {HEADLESS}")
    s.add_argument("manifest", help="scrape_manifest.json path")
    s.add_argument("--profile", default=None, help="studio profile id")
    s.add_argument("--dry-run", action="store_true",
                   help="classify without writing the manifest back")
    s.set_defaults(_op="survey")

    # — safe-zone —
    sz = sub.add_parser("safe-zone", help=f"safe-zone mask queries {HEADLESS}")
    sz_sub = sz.add_subparsers(dest="sz_cmd", required=True)
    szp = sz_sub.add_parser("plan", help="resolve a preset's mask + zone coverage")
    szp.add_argument("--preset", required=True)
    szp.set_defaults(_op="safe-zone plan")
    szl = sz_sub.add_parser("list", help="list mask assets on disk")
    szl.set_defaults(_op="safe-zone list")

    # — occlusion —
    oc = sub.add_parser("occlusion", help=f"spatial occlusion engine {HEADLESS}")
    oc_sub = oc.add_subparsers(dest="oc_cmd", required=True)
    ocz = oc_sub.add_parser("zones", help="GO/NUDGE/CUTOFF fractions for a preset")
    ocz.add_argument("--preset", required=True)
    ocz.set_defaults(_op="occlusion zones")
    occ = oc_sub.add_parser("check", help="run the SOE relayout pass over conformed layers")
    occ.add_argument("--preset", required=True)
    occ.add_argument("--conformed", required=True,
                     help='JSON file {"layers": [...]} from the conform pipeline')
    occ.set_defaults(_op="occlusion check")

    # — naming —
    n = sub.add_parser("naming", help=f"resolve output comp names {HEADLESS}")
    n_sub = n.add_subparsers(dest="n_cmd", required=True)
    nr = n_sub.add_parser("resolve", help="resolve one output name (collision-bumping)")
    nr.add_argument("--source", required=True, help="source comp name")
    nr.add_argument("--existing", default=None,
                    help="comma-separated existing comp names")
    nr.add_argument("--existing-file", default=None,
                    help="file with existing names (JSON list or one per line)")
    nr.add_argument("--template", default=None,
                    help="naming template; default is the engine's "
                         "{source}_{preset} (empty preset → 'conform')")
    nr.add_argument("--width", type=int, default=0)
    nr.add_argument("--height", type=int, default=0)
    nr.add_argument("--preset-id", default="")
    nr.set_defaults(_op="naming resolve")

    # — lut —
    lu = sub.add_parser("lut", help=f"local LUT math {HEADLESS} (inject honest-fails)")
    lu_sub = lu.add_subparsers(dest="lut_cmd", required=True)
    luv = lu_sub.add_parser("validate", help="parse a .cube/.3dl, print a summary")
    luv.add_argument("path")
    luv.set_defaults(_op="lut validate")
    for kind in ("derive", "derive-parametric", "derive-smart"):
        lud = lu_sub.add_parser(kind, help=f"{kind} (see `lut validate --help` for notes)")
        lud.add_argument("args", nargs=argparse.REMAINDER)
        lud.set_defaults(_op=f"lut {kind}")
    lui = lu_sub.add_parser(
        "inject",
        help="HONEST-FAIL: AE cannot script a LUT path (exit 65, code LUT_UNSCRIPTABLE)",
    )
    lui.add_argument("args", nargs=argparse.REMAINDER)
    lui.set_defaults(_op="lut inject")

    # — duplication —
    d = sub.add_parser("duplication", help=f"fork planning + cleanup {HEADLESS}")
    d_sub = d.add_subparsers(dest="d_cmd", required=True)
    dp = d_sub.add_parser("preview", help="what will be duplicated for this conform")
    dp.add_argument("--manifest", required=True)
    dp.add_argument("--width", type=int, required=True)
    dp.add_argument("--height", type=int, required=True)
    dp.add_argument("--preset", default="manual")
    dp.set_defaults(_op="duplication preview")
    dc = d_sub.add_parser(
        "cleanup-report",
        help="READ-ONLY orphan report (never deletes; #540 descoped to report-only)",
    )
    dc.add_argument("--project-structure", required=True)
    dc.add_argument("--active-comp-id", type=int, default=None)
    dc.add_argument("--db", default=None,
                    help="path to .dimension/dimension.db (default: next to project_structure)")
    dc.add_argument("--chunk-manifest", default=None)
    dc.set_defaults(_op="duplication cleanup-report")

    # — provenance —
    pr = sub.add_parser("provenance", help=f"query the project SQLite store {HEADLESS}")
    pr_sub = pr.add_subparsers(dest="pr_cmd", required=True)
    prd = pr_sub.add_parser("duplicates",
                            help="comps Babysitter provably created (read-only)")
    prd.add_argument("--db", default=None)
    prd.add_argument("--project-dir", default=None)
    prd.set_defaults(_op="provenance duplicates")
    prr = pr_sub.add_parser("runs", help="conform run history (read-only)")
    prr.add_argument("--db", default=None)
    prr.add_argument("--project-dir", default=None)
    prr.add_argument("--session", default=None)
    prr.add_argument("--limit", type=int, default=50)
    prr.set_defaults(_op="provenance runs")
    pri = pr_sub.add_parser(
        "import",
        help="one-time import of legacy .dimension/duplication_log.json",
    )
    pri.add_argument("--project-dir", required=True)
    pri.set_defaults(_op="provenance import")

    # — catalog —
    ca = sub.add_parser("catalog", help=f"preset + profile catalog {HEADLESS}")
    ca_sub = ca.add_subparsers(dest="ca_cmd", required=True)
    cap = ca_sub.add_parser("presets", help="list every resolvable preset")
    cap.set_defaults(_op="catalog presets")
    cas = ca_sub.add_parser("show", help="full detail for one preset")
    cas.add_argument("preset")
    cas.set_defaults(_op="catalog show")
    caf = ca_sub.add_parser("profiles", help="studio profiles for the surveyor")
    caf.set_defaults(_op="catalog profiles")

    # — target —
    t = sub.add_parser("target", help=f"user custom targets {HEADLESS}")
    t_sub = t.add_subparsers(dest="t_cmd", required=True)
    ta = t_sub.add_parser("add", help="save a custom target (preset)")
    ta.add_argument("--label", required=True)
    ta.add_argument("--width", type=int, required=True)
    ta.add_argument("--height", type=int, required=True)
    ta.add_argument("--subcategory", default="user")
    ta.add_argument("--duration", type=float, default=None)
    ta.add_argument("--fps", type=float, default=None)
    ta.add_argument("--output-name-template", default=None)
    ta.set_defaults(_op="target add")
    tr = t_sub.add_parser("remove", help="remove a custom target by id")
    tr.add_argument("--id", required=True)
    tr.set_defaults(_op="target remove")

    # — ae (live) —
    a = sub.add_parser("ae", help=f"live After Effects bridge {LIVE_AE}")
    a_sub = a.add_subparsers(dest="a_cmd", required=True)
    ap = a_sub.add_parser("probe", help="check AE/poller reachability (never sends a job)")
    ap.set_defaults(_op="ae probe")
    ams = a_sub.add_parser("mask", help="safe-zone overlay in the active comp")
    ams_sub = ams.add_subparsers(dest="am_cmd", required=True)
    amsh = ams_sub.add_parser("show", help="import the preset's mask PNG as overlay")
    amsh.add_argument("--preset", required=True)
    amsh.set_defaults(_op="ae mask show")
    amhd = ams_sub.add_parser("hide", help="remove the mask overlay")
    amhd.set_defaults(_op="ae mask hide")

    return p


def dispatch(args: argparse.Namespace) -> int:
    """Map the parsed op to one ops call + one human formatter."""
    op_name = args._op

    if op_name == "conform":
        return _run(args,
                    lambda: conform_ops.run_conform_op(
                        source=args.source, preset=args.preset, profile=args.profile,
                        width=args.width, height=args.height, duration=args.duration,
                        fps=args.fps, mode=args.mode, layout=args.layout,
                        bleed=args.bleed, output=args.output, no_report=args.no_report,
                        allow_state_hash_bypass=args.allow_state_hash_bypass),
                    _show_conform)
    if op_name == "survey":
        return _run(args,
                    lambda: survey_ops.survey_op(
                        manifest_path=args.manifest, profile=args.profile,
                        dry_run=args.dry_run),
                    _show_survey)
    if op_name == "safe-zone plan":
        return _run(args,
                    lambda: safe_zone_ops.mask_plan_op(preset=args.preset),
                    _show_mask_plan)
    if op_name == "safe-zone list":
        return _run(args, safe_zone_ops.mask_list_op,
                    lambda r: emit_human("\n".join(r["masks"]) or "(no masks on disk)"))
    if op_name == "occlusion zones":
        return _run(args,
                    lambda: occlusion_ops.zones_op(preset=args.preset),
                    _show_zones)
    if op_name == "occlusion check":
        return _run(args,
                    lambda: occlusion_ops.check_op(
                        preset=args.preset, conformed_path=args.conformed),
                    _show_occlusion_check)
    if op_name == "naming resolve":
        return _run(args,
                    lambda: naming_ops.resolve_name_op(
                        source=args.source, existing=args.existing,
                        existing_file=args.existing_file, template=args.template,
                        width=args.width, height=args.height,
                        preset_id=args.preset_id),
                    _show_naming)
    if op_name == "lut validate":
        return _run(args,
                    lambda: lut_ops.validate_op(path=args.path),
                    _show_lut_validate)
    if op_name in ("lut derive", "lut derive-parametric", "lut derive-smart"):
        kind = op_name.split(" ", 1)[1]
        return _run(
            args,
            lambda: lut_ops.derive_op(kind=kind, args=args.args),
            lambda r: emit_human(r.get("output") or r.get("status", "ok")),
        )
    if op_name == "lut inject":
        # Honest fail: raises DimensionError(code=LUT_UNSCRIPTABLE, exit 65).
        return _run(args, lut_ops.inject_op, lambda r: None)
    if op_name == "duplication preview":
        return _run(args,
                    lambda: duplication_ops.preview_op(
                        manifest=args.manifest, width=args.width,
                        height=args.height, preset=args.preset),
                    _show_preview)
    if op_name == "duplication cleanup-report":
        return _run(args,
                    lambda: duplication_ops.cleanup_report_op(
                        project_structure=args.project_structure,
                        active_comp_id=args.active_comp_id, db=args.db,
                        chunk_manifest=args.chunk_manifest),
                    _show_cleanup)
    if op_name == "provenance duplicates":
        return _run(args,
                    lambda: provenance_ops.duplicates_op(
                        db=args.db, project_dir=args.project_dir),
                    _show_provenance_duplicates)
    if op_name == "provenance runs":
        return _run(args,
                    lambda: provenance_ops.runs_op(
                        db=args.db, project_dir=args.project_dir,
                        session=args.session, limit=args.limit),
                    _show_provenance_runs)
    if op_name == "provenance import":
        return _run(args,
                    lambda: provenance_ops.import_legacy_op(
                        project_dir=args.project_dir),
                    lambda r: emit_human(
                        f"imported {r['imported_rows']} row(s) into {r['db']}"))
    if op_name == "catalog presets":
        return _run(args, catalog_ops.presets_op, _show_presets)
    if op_name == "catalog show":
        return _run(args,
                    lambda: catalog_ops.show_preset_op(preset=args.preset),
                    _show_preset)
    if op_name == "catalog profiles":
        return _run(args, catalog_ops.profiles_op, _show_profiles)
    if op_name == "target add":
        return _run(args,
                    lambda: target_ops.add_target_op(
                        label=args.label, width=args.width, height=args.height,
                        subcategory=args.subcategory, duration=args.duration,
                        fps=args.fps,
                        output_name_template=args.output_name_template),
                    _show_target)
    if op_name == "target remove":
        return _run(args,
                    lambda: target_ops.remove_target_op(target_id=args.id),
                    lambda r: emit_human(
                        f"{'removed' if r['removed'] else 'not found'}: {r['id']}"))
    if op_name == "ae probe":
        return _run(args, ae_ops.probe_op, _show_ae_probe)
    if op_name == "ae mask show":
        return _run(args,
                    lambda: ae_ops.mask_show_op(preset=args.preset),
                    lambda r: emit_human(
                        f"mask overlay shown for {r['preset']} (live AE)"))
    if op_name == "ae mask hide":
        return _run(args, ae_ops.mask_hide_op,
                    lambda r: emit_human("mask overlay removed (live AE)"))

    raise AssertionError(f"unhandled op: {op_name}")  # pragma: no cover


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    log_to_stderr(args.log_level)
    try:
        return dispatch(args)
    except DimensionError as e:  # op raised outside _run (derive path)
        if args.json:
            emit_json(error_payload(e))
        else:
            emit_human(f"error [{e.code}]: {e}")
        return e.exit_code
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
