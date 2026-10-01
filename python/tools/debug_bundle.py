#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/debug_bundle.py
Aggregates session conform reports, correlated logs, probe diagnostics,
and system context into a standalone QA Debug Bundle (ZIP + Markdown summary)
ready for transmission to a QA agent or attaching to a bug ticket.

Usage:
  python3 python/tools/debug_bundle.py [session_id]
  python3 python/tools/debug_bundle.py --since-last
  python3 python/tools/debug_bundle.py 3EF27671 --output-dir logs/bundles
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "python") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "python"))

from pytest_dimension_ae.probes import probe_ae
from tools.qa_common import (
    find_conform_reports_by_session,
    find_latest_session_id,
    normalize_session_id,
)
from tools.session_correlate import correlate_session


def _filtered_log_tail(
    text: str,
    *,
    patterns: tuple[str, ...] = ("error", "warning", "exception"),
    context_lines: int = 5,
    max_matched_lines: int = 50,
    fallback_lines: int = 50,
) -> str:
    """Issue #503 — token-optimized log truncation.

    Grep-like filter: keeps only lines containing one of `patterns`
    (case-insensitive), each with `context_lines` of surrounding
    context, capped at `max_matched_lines` matched lines (context
    lines don't count against the cap). Overlapping/adjacent windows
    merge into one block with a single header instead of duplicating
    shared lines.

    Full After Effects/transfer logs run thousands of lines of
    successful, uninteresting telemetry that bury the actual
    exceptions in an LLM's context window — this surfaces only the
    lines that matter. Falls back to the last `fallback_lines` lines
    verbatim (still far short of the old unconditional 500-line tail)
    when nothing matches, so a clean successful run still carries
    enough tail context to confirm it actually ran.
    """
    lines = text.splitlines()
    if not lines:
        return "(log is empty)"

    lowered_patterns = [p.lower() for p in patterns]
    match_indices = [
        i for i, line in enumerate(lines)
        if any(p in line.lower() for p in lowered_patterns)
    ]

    if not match_indices:
        tail = lines[-fallback_lines:] if len(lines) > fallback_lines else lines
        return (
            f"(no {'/'.join(patterns)} lines found — showing last "
            f"{len(tail)} line(s) for context)\n" + "\n".join(tail)
        )

    matched = match_indices[:max_matched_lines]
    truncated_matches = len(match_indices) - len(matched)

    # Merge each match's [i-context, i+context] window with any
    # overlapping/adjacent window so shared lines print once.
    windows: list[list[int]] = []
    for i in matched:
        lo = max(0, i - context_lines)
        hi = min(len(lines) - 1, i + context_lines)
        if windows and lo <= windows[-1][1] + 1:
            windows[-1][1] = max(windows[-1][1], hi)
        else:
            windows.append([lo, hi])

    out: list[str] = [
        f"({len(match_indices)} matching line(s) for "
        f"{'/'.join(patterns)}, {context_lines}-line context each"
        + (f", showing first {max_matched_lines}" if truncated_matches else "")
        + ")"
    ]
    for lo, hi in windows:
        out.append(f"--- lines {lo + 1}-{hi + 1} ---")
        out.extend(lines[lo:hi + 1])
    return "\n".join(out)


def _get_git_info(repo: Path) -> dict[str, str]:
    info = {"branch": "unknown", "commit": "unknown", "status_clean": "false"}
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo), text=True
        ).strip()
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(repo), text=True
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=str(repo), text=True
        ).strip()
        info["branch"] = branch
        info["commit"] = commit
        info["status_clean"] = "true" if not status else "false"
    except Exception:
        pass
    return info


def build_debug_bundle(
    session_id: str,
    repo: Path,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Assemble logs, report, probe status, and QA summary into a zip bundle.
    Returns (zip_path, markdown_path).
    """
    sid = normalize_session_id(session_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_name = f"dimension_debug_bundle_{sid}"
    staging_dir = output_dir / bundle_name
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    # 1. Correlate session
    log_path = repo / "logs" / "dimension.log"
    if not log_path.is_file():
        log_path = repo / "dimension.log"
    transfer_path = repo / "transfer_status.log"

    correlation = correlate_session(
        repo=repo,
        session_id=sid,
        log_path=log_path,
        transfer_path=transfer_path,
    )

    # 2. Copy Conform Reports (HTML + JSON)
    reports = find_conform_reports_by_session(repo, sid)
    copied_reports = []
    if reports:
        for r in reports:
            if r.json_path.is_file():
                dest_json = staging_dir / r.json_path.name
                shutil.copy2(r.json_path, dest_json)
                copied_reports.append(dest_json.name)
            if r.html_path and r.html_path.is_file():
                dest_html = staging_dir / r.html_path.name
                shutil.copy2(r.html_path, dest_html)
                copied_reports.append(dest_html.name)

    # 3. Copy relevant log slices / sidecars
    artifacts_dir = staging_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    # Scrape manifest & hash sidecar
    for p in [repo / "scrape_manifest.json", repo / ".dimension" / "scrape_manifest.json"]:
        if p.is_file():
            shutil.copy2(p, artifacts_dir / "scrape_manifest.json")
            sha_p = p.with_suffix(".json.sha256")
            if sha_p.is_file():
                shutil.copy2(sha_p, artifacts_dir / "scrape_manifest.json.sha256")
            break

    # Chunk manifest
    for p in [repo / "chunk_manifest.json", repo / ".dimension" / "chunk_manifest.json"]:
        if p.is_file():
            shutil.copy2(p, artifacts_dir / "chunk_manifest.json")
            break

    # Conformed manifest (post-scale pre-chunk)
    for p in [repo / "conformed_manifest.json", repo / ".dimension" / "conformed_manifest.json"]:
        if p.is_file():
            shutil.copy2(p, artifacts_dir / "conformed_manifest.json")
            break

    # SOE Corrections
    for p in [repo / "soe_corrections.json", repo / ".dimension" / "soe_corrections.json"]:
        if p.is_file():
            shutil.copy2(p, artifacts_dir / "soe_corrections.json")
            break

    # Duplication Log
    for p in [repo / "duplication_log.json", repo / ".dimension" / "duplication_log.json"]:
        if p.is_file():
            shutil.copy2(p, artifacts_dir / "duplication_log.json")
            break

    # Active profile snapshot (if social, theatrical, default, ooh)
    profiles_dir = repo / "config" / "profiles"
    if profiles_dir.is_dir():
        for prof in profiles_dir.glob("*.yaml"):
            shutil.copy2(prof, artifacts_dir / prof.name)

    # Transfer status log — filtered to Error/Warning/Exception lines
    # with context (issue #503), not an unconditional raw tail.
    if transfer_path.is_file():
        try:
            raw_text = transfer_path.read_text(encoding="utf-8", errors="replace")
            (staging_dir / "transfer_status_tail.log").write_text(
                _filtered_log_tail(raw_text), encoding="utf-8"
            )
        except Exception:
            pass

    # Dimension log (correlated records)
    (staging_dir / "session_correlation.json").write_text(
        json.dumps(correlation, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 4. Probe AE Status
    probe_result = probe_ae(str(repo))
    ae_info = probe_result.to_dict() if hasattr(probe_result, "to_dict") else {}
    (staging_dir / "ae_probe_status.json").write_text(
        json.dumps(ae_info, indent=2), encoding="utf-8"
    )

    # 5. Git & System Environment
    git_info = _get_git_info(repo)
    env_info = {
        "os": platform.system(),
        "os_release": platform.release(),
        "python_version": platform.python_version(),
        "git": git_info,
        "bundle_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    (staging_dir / "environment.json").write_text(
        json.dumps(env_info, indent=2), encoding="utf-8"
    )

    # 6. Generate Markdown QA_SUMMARY.md with 2-Tier Triage Vector
    meta = correlation.get("report", {}).get("meta", {})
    verdict = correlation.get("verdict", "UNKNOWN")
    clog = correlation.get("conform_log", {})
    inj = correlation.get("inject", {}) or {}
    best_match = inj.get("best_match", {}) or {}
    warnings = clog.get("warnings", [])
    collapse_count = meta.get("collapse_warnings", 0)

    # Determine Tier 1 (AEP Comp) vs Tier 2 (Dimension Engine) triage hints
    aep_issues = []
    engine_issues = []

    if collapse_count > 0:
        aep_issues.append(f"{collapse_count} collapsed transformation warnings detected (check 3D / continuous rasterization / nested scale).")
    if not best_match or best_match.get("status") != "COMPLETE":
        engine_issues.append("Injection incomplete or failed during pump execution.")
    if best_match.get("auditPass") is False:
        engine_issues.append("Auditor post-injection QC check failed (transform drift or missing layer).")
    if not ae_info.get("ready"):
        engine_issues.append(f"Host poller not ready: {ae_info.get('summary', 'offline')}")

    # Issue #503 — Critical Exceptions + AE Probe Status lead the document
    # (inverted hierarchy) so an agent's limited context/attention lands on
    # the actionable findings first, not three sections of preamble in.
    all_issues = aep_issues + engine_issues

    qa_lines = [
        f"# Dimension Dual-Tier QA Triage Bundle — Session `{sid}`",
        "",
        f"**Generated:** {env_info['bundle_timestamp']}  ",
        f"**Verdict:** `{verdict}`  ",
        f"**Git Commit:** `{git_info['commit']}` on branch `{git_info['branch']}`  ",
        "",
        "## 1. 🚨 Critical Exceptions",
    ]
    if all_issues:
        qa_lines.extend(f"- ❌ {issue}" for issue in all_issues)
    else:
        qa_lines.append("- ✅ None detected — pipeline and injection completed cleanly.")

    qa_lines.extend([
        "",
        "## 2. 🛰️ AE Probe Status",
        f"- **Ready:** `{ae_info.get('ready', 'unknown')}` (`{ae_info.get('status', 'UNKNOWN')}`) — {ae_info.get('summary', 'no probe data')}",
        f"- **AE process:** `{ae_info.get('ae_process', '?')}` · **Socket :45445:** `{ae_info.get('socket_listening', '?')}`",
        f"- **Heartbeat:** exists=`{ae_info.get('heartbeat_exists', '?')}` fresh=`{ae_info.get('heartbeat_fresh', '?')}`"
        + (f" age={ae_info['heartbeat_age_s']:.1f}s" if ae_info.get("heartbeat_age_s") is not None else ""),
        f"- **Inbox:** pending=`{ae_info.get('inbox_pending', '?')}` claimed=`{ae_info.get('inbox_claimed', '?')}`",
        "",
        "## 3. Session & Target Specifications",
        f"- **Source Comp:** `{meta.get('source_name', 'Unknown')}`",
        f"- **Target Resolution:** `{meta.get('target_w', '?')} × {meta.get('target_h', '?')}` ({meta.get('aspect_strategy', '—')})",
        f"- **Scale Factor:** `{meta.get('uniform_scale', '?')}×` (Mode: `{meta.get('scale_mode', '?')}`)",
        f"- **Total Layers:** `{meta.get('total_layers', '?')}` across `{meta.get('total_chunks', '?')}` chunks",
        f"- **Collapse Warnings:** `{collapse_count}`",
        "",
        "## 4. ⚡ Autonomous Agent Triage Directives",
        "",
        "### 🎯 Tier 1: AEP Project / Composition Health (Artist & Comp Level)",
        f"- **Comp Scope:** Check layer hierarchy, parenting cycles, and zero-dimension solids in `{meta.get('source_name', 'Unknown')}`.",
        f"- **AEP Flags:** {(' · '.join(aep_issues)) if aep_issues else '✅ Clean project structure. No collapse or parenting locks detected.'}",
        "- **Recommended Action:** If visual drift occurs on hero layers, inspect `artifacts/scrape_manifest.json` for layer geometry and anchor points.",
        "",
        "### ⚙️ Tier 2: Dimension Engine / Bridge Diagnostics (Tool & Pipeline Level)",
        f"- **Engine Status:** {(' · '.join(engine_issues)) if engine_issues else '✅ Engine pipelines, socket bridge, and Auditor passed cleanly.'}",
        f"- **IPC Transfer:** Status `{best_match.get('status', 'NONE')}` · Transport: `{ae_info.get('summary', 'Socket/Bridge')}`",
        "- **SOE Corrections:** `" + str(clog.get("soe_correction_count", 0)) + "` nudges applied (digest below; full detail in `artifacts/soe_corrections.json`).",
        "- **Recommended Action:** If IPC stalled, inspect `transfer_status_tail.log` and `session_correlation.json`.",
    ])

    # Extract Top-3 Layer coordinate shifts from report.json if available
    top_layers = []
    if reports and reports[0].json_path.is_file():
        try:
            r_data = json.loads(reports[0].json_path.read_text(encoding="utf-8", errors="replace"))
            layers = r_data.get("layers", [])
            shifts = []
            for lyr in layers:
                sp = lyr.get("src_pos")
                dp = lyr.get("dst_pos")
                if isinstance(sp, list) and isinstance(dp, list) and len(sp) >= 2 and len(dp) >= 2:
                    dx = round(dp[0] - sp[0])
                    dy = round(dp[1] - sp[1])
                    mag = (dx * dx + dy * dy) ** 0.5
                    shifts.append({
                        "name": lyr.get("name", "Layer"),
                        "tag": lyr.get("content_tag") or "UNCLASS",
                        "dx": dx,
                        "dy": dy,
                        "mag": round(mag),
                    })
            shifts.sort(key=lambda s: s["mag"], reverse=True)
            top_layers = shifts[:3]
        except Exception:
            pass

    if top_layers:
        qa_lines.append("")
        qa_lines.append("### 📐 Top Layer Coordinate Shifts (In-line Telemetry)")
        for tl in top_layers:
            sign_x = "+" if tl["dx"] >= 0 else ""
            sign_y = "+" if tl["dy"] >= 0 else ""
            qa_lines.append(
                f"- `{tl['name']}` [{tl['tag']}]: ΔX {sign_x}{tl['dx']}px, ΔY {sign_y}{tl['dy']}px (shift: {tl['mag']}px)"
            )

    # Issue #503 ("Flatten Data") — compact digests of the nested JSON
    # artifacts instead of requiring the agent to open the raw files for
    # the common "how many things happened, and what stands out" question.
    soe_path = artifacts_dir / "soe_corrections.json"
    if soe_path.is_file():
        try:
            soe_data = json.loads(soe_path.read_text(encoding="utf-8", errors="replace"))
            corrections = soe_data if isinstance(soe_data, list) else soe_data.get("corrections", [])
            moved = [c for c in corrections if isinstance(c, dict) and c.get("move_distance_px", 0)]
            moved.sort(key=lambda c: c.get("move_distance_px", 0), reverse=True)
            qa_lines.append("")
            qa_lines.append(f"### 🧭 SOE Corrections Digest ({len(corrections)} total, {len(moved)} moved)")
            for c in moved[:5]:
                qa_lines.append(
                    f"- `{c.get('layer_name', '?')}`: {c.get('strategy', '?')} "
                    f"({c.get('move_distance_px', 0):.0f}px, zone `{c.get('zone_hit', '?')}`)"
                )
        except Exception:
            pass

    dup_path = artifacts_dir / "duplication_log.json"
    if dup_path.is_file():
        try:
            dup_data = json.loads(dup_path.read_text(encoding="utf-8", errors="replace"))
            duplicates_made = dup_data.get("duplicates_made", []) if isinstance(dup_data, dict) else []
            rewires_made = dup_data.get("rewires_made", []) if isinstance(dup_data, dict) else []
            skipped = dup_data.get("skipped", []) if isinstance(dup_data, dict) else []
            dup_errors = dup_data.get("errors", []) if isinstance(dup_data, dict) else []
            qa_lines.append("")
            qa_lines.append(
                f"### 🧬 Duplication Digest ({len(duplicates_made)} duplicated, "
                f"{len(rewires_made)} rewired, {len(skipped)} skipped, "
                f"{len(dup_errors)} error(s))"
            )
            for d in duplicates_made[:5]:
                if isinstance(d, dict):
                    qa_lines.append(f"- `{d.get('duplicate_name', '?')}`")
            for e in dup_errors[:5]:
                if isinstance(e, dict):
                    qa_lines.append(f"- 🚨 `{e.get('phase', '?')}`: {e.get('detail', '?')}")
        except Exception:
            pass

    if warnings:
        qa_lines.append("")
        qa_lines.append("## 5. Active Pipeline Warnings")
        for w in warnings:
            qa_lines.append(f"- ⚠️ {w}")

    qa_lines.extend([
        "",
        "## 6. Packaged Diagnostic Artifacts",
        "- `report.html` & `report.json` — Compact conform report and layer transformation table.",
        "- `transfer_status_tail.log` — Inject engine pump and chunk telemetry.",
        "- `session_correlation.json` — Complete session correlation dictionary.",
        "- `ae_probe_status.json` — Live After Effects host and bridge heartbeat probe.",
        "- `environment.json` — OS, Python runtime, and git status metadata.",
        "- `artifacts/scrape_manifest.json` — Source AE composition scrape manifest.",
        "- `artifacts/chunk_manifest.json` — Injection chunk payload.",
        "- `artifacts/conformed_manifest.json` — Full mathematical conformed target state.",
        "- `artifacts/soe_corrections.json` — Safe-zone spatial occlusion corrections.",
        "- `artifacts/duplication_log.json` — Flat precomp duplication plan and rewiring.",
        "",
        "> Generated by `python/tools/debug_bundle.py` for automated QA triage.",
    ])

    summary_content = "\n".join(qa_lines)
    summary_path = staging_dir / "QA_SUMMARY.md"
    summary_path.write_text(summary_content, encoding="utf-8")

    # Also write a standalone copy next to the zip in output_dir
    standalone_summary = output_dir / f"QA_SUMMARY_{sid}.md"
    standalone_summary.write_text(summary_content, encoding="utf-8")

    # 7. Zip staging directory
    zip_target = output_dir / f"{bundle_name}.zip"
    with zipfile.ZipFile(zip_target, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(staging_dir):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(staging_dir)
                zf.write(file_path, arcname)

    # Clean up staging directory
    shutil.rmtree(staging_dir)

    return zip_target, standalone_summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bundle conform reports, logs, and diagnostics for QA agent review."
    )
    parser.add_argument(
        "session_id",
        nargs="?",
        default=None,
        help="Conform session ID (e.g. 3EF27671). Omit if --since-last is passed.",
    )
    parser.add_argument(
        "--since-last",
        action="store_true",
        help="Automatically pick the latest session ID from the newest report.",
    )
    parser.add_argument(
        "--output-dir",
        default="logs/bundles",
        help="Directory to place the resulting .zip bundle and QA_SUMMARY.md (default: logs/bundles)",
    )
    parser.add_argument(
        "--repo",
        default=str(REPO_ROOT),
        help="Dimension repo root directory",
    )

    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    output_dir = repo / args.output_dir

    if args.since_last and args.session_id:
        parser.error("Specify either an explicit session_id or --since-last, not both.")
    if not args.since_last and not args.session_id:
        parser.error("Session ID is required (or pass --since-last).")

    session_id = args.session_id
    if args.since_last:
        session_id = find_latest_session_id(repo)
        if not session_id:
            print("❌ No conform reports found in repo to determine latest session ID.", file=sys.stderr)
            return 2

    session_id = normalize_session_id(session_id)
    print(f"📦 Packaging Dev Debug Bug Bundle for session [{session_id}]...")

    zip_path, summary_path = build_debug_bundle(
        session_id=session_id,
        repo=repo,
        output_dir=output_dir,
    )

    print("✅ QA Debug Bundle Created Successfully!")
    print(f"  • Archive:  {zip_path}")
    print(f"  • Summary:  {summary_path}")
    print(f"\nYou can now pass {summary_path} directly to your QA agent or review ticket.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
