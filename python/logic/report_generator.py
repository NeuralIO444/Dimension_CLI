# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Dimension Engine — Post-Conform HTML Report Generator
Produces conform_report.html after a successful injection.
Lists every modified layer, its source/target transforms, and
flags any collapsed-transform warnings in amber.
"""

import json
import os
import time
from html import escape as _h
from pathlib import Path
from typing import Any, Dict, List, Optional

from logic.exporter import MAX_CHUNK_SIZE
from core.logger import log


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Dimension Conform Report</title>
<style>
  :root {{
    --bg: #121214; --surface: #1a1a1f; --surface-hover: #22222a; --border: #282832;
    --text: #e1e1e6; --muted: #7e7e8c; --accent: #00ccff;
    --warn: #f5a623; --ok: #4caf50; --fail: #e53935;
    --mono: "SF Mono","Fira Code","Cascadia Code",monospace;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; font-size: 12px; padding: 16px 20px; }}
  header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-bottom: 14px; }}
  .header-brand {{ display: flex; align-items: baseline; gap: 10px; }}
  h1 {{ font-size: 16px; font-weight: 700; color: var(--accent); letter-spacing: .05em; }}
  .engine-tag {{ color: var(--muted); font-size: 11px; font-family: var(--mono); }}
  .meta {{ color: var(--muted); font-size: 11px; text-align: right; display: flex; align-items: center; gap: 16px; }}
  .meta-specs {{ line-height: 1.4; }}
  .export-bar {{ display: flex; gap: 8px; align-items: center; }}
  .export-btn {{ display: inline-flex; align-items: center; gap: 5px; padding: 5px 12px; border-radius: 4px; font-size: 10.5px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; cursor: pointer; border: 1px solid; background: transparent; transition: all .15s ease; text-decoration: none; }}
  .export-btn.tech-report {{ color: #121214; background: #00ccff; border-color: #00ccff; box-shadow: 0 0 12px rgba(0,204,255,.35); }}
  .export-btn.tech-report:hover {{ background: #33d6ff; border-color: #33d6ff; box-shadow: 0 0 16px rgba(0,204,255,.5); }}
  .export-btn.subtle {{ color: var(--muted); border-color: var(--border); background: var(--surface); }}
  .export-btn.subtle:hover {{ color: var(--text); border-color: var(--muted); background: var(--surface-hover); }}
  .summary {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 8px; margin-bottom: 14px; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 5px; padding: 8px 12px; display: flex; justify-content: space-between; align-items: center; }}
  .card-label {{ font-size: 9.5px; text-transform: uppercase; letter-spacing: .07em; color: var(--muted); font-weight: 600; }}
  .card-value {{ font-size: 16px; font-weight: 700; color: var(--accent); font-family: var(--mono); }}
  .card-value.warn {{ color: var(--warn); }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ background: var(--surface); color: var(--muted); font-size: 9.5px; text-transform: uppercase; letter-spacing: .06em; padding: 6px 10px; text-align: left; border-bottom: 1px solid var(--border); position: sticky; top: 0; }}
  td {{ padding: 6px 10px; border-bottom: 1px solid var(--border); font-family: var(--mono); font-size: 11px; vertical-align: middle; }}
  tr.warn td {{ background: rgba(245,166,35,.06); }}
  tr:hover td {{ background: rgba(255,255,255,.03); }}
  .badge {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 9.5px; font-weight: 600; letter-spacing: .04em; }}
  .badge-ok   {{ background: rgba(76,175,80,.15);  color: var(--ok); }}
  .badge-warn {{ background: rgba(245,166,35,.15); color: var(--warn); }}
  .delta {{ color: var(--muted); }}
  .delta.changed {{ color: var(--accent); font-weight: 600; }}
  footer {{ margin-top: 20px; text-align: center; color: var(--muted); font-size: 10.5px; }}
  /* ── v5.2.5 SOE audit section ─────────────────────────────── */
  .soe-audit {{ margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--border); }}
  .soe-audit h2 {{ font-size: 12px; font-weight: 700; color: var(--accent); letter-spacing: .04em; text-transform: uppercase; margin-bottom: 8px; }}
  .soe-summary {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }}
  .soe-summary .soe-badge {{ font-size: 9.5px; padding: 2px 7px; }}
  .soe-table th {{ font-size: 9px; }}
  .soe-table td {{ padding: 5px 8px; font-size: 10.5px; vertical-align: middle; }}
  .soe-table td.notes {{ color: var(--muted); font-style: italic; font-size: 10px; }}
  .soe-badge {{ display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 9px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; white-space: nowrap; }}
  .soe-badge.relayout    {{ background: rgba(200,130,10,.16); color: #c8820a; }}
  .soe-badge.clear       {{ background: rgba(34,197,94,.16);  color: #22c55e; }}
  .soe-badge.keyed       {{ background: rgba(136,136,136,.18);color: #aaa; }}
  .soe-badge.no-bounds   {{ background: rgba(136,136,136,.18);color: #aaa; }}
  .soe-badge.structural  {{ background: rgba(85,85,85,.20);   color: #888; }}
  .soe-badge.failed      {{ background: rgba(239,68,68,.20);  color: #ef4444; }}
  .soe-badge.zone-clear  {{ background: rgba(34,197,94,.14);  color: #22c55e; }}
  .soe-badge.zone-nudge  {{ background: rgba(245,166,35,.14); color: #f5a623; }}
  .soe-badge.zone-cutoff {{ background: rgba(229,57,53,.16);  color: #e53935; }}
  .soe-badge.zone-unknown{{ background: rgba(136,136,136,.16);color: #888; }}
  .soe-zone-arrow {{ color: var(--muted); margin: 0 4px; }}
  .empty-state {{ color: var(--muted); font-style: italic; padding: 8px 0; font-size: 11px; }}
  /* ── #420 post-inject safe-zone verification badge ─────────── */
  .soe-verify-badge {{ display: inline-block; padding: 2px 7px; margin-left: 8px; border-radius: 3px; font-size: 9px; font-weight: 700; letter-spacing: .03em; text-transform: none; vertical-align: middle; }}
  .soe-verify-ok      {{ background: rgba(34,197,94,.16);  color: #22c55e; }}
  .soe-verify-drift   {{ background: rgba(229,57,53,.16);  color: #e53935; }}
  .soe-verify-unknown {{ background: rgba(245,166,35,.14); color: #f5a623; }}
  /* ── v12.5 Stage D Collapse-disable warnings ───────────────── */
  .collapse-section {{ margin-top: 20px; padding: 12px 14px; border: 1px solid var(--warn); border-radius: 6px; background: rgba(245,166,35,.06); }}
  .collapse-section h2 {{ font-size: 12px; font-weight: 700; color: var(--warn); letter-spacing: .04em; text-transform: uppercase; margin-bottom: 8px; }}
  .collapse-summary {{ color: var(--text); font-size: 11px; line-height: 1.5; margin-bottom: 10px; }}
  .collapse-summary strong {{ color: var(--warn); font-weight: 700; }}
  .collapse-table {{ width: 100%; border-collapse: collapse; }}
  .collapse-table th {{ font-size: 9px; text-align: left; padding: 5px 8px; color: var(--muted); border-bottom: 1px solid var(--border); }}
  .collapse-table td {{ padding: 5px 8px; font-size: 10.5px; border-bottom: 1px solid var(--border); }}
  .collapse-table code {{ font-family: var(--mono); font-size: 10px; color: var(--muted); }}
  /* ── v5.8 Precomp Duplications section ────────────────────── */
  .dup-section {{ margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--border); }}
  .dup-section h2 {{ font-size: 12px; font-weight: 700; color: var(--accent); letter-spacing: .04em; text-transform: uppercase; margin-bottom: 8px; }}
  .dup-section.amber h2 {{ color: var(--warn); }}
  .dup-section.dry-run h2 {{ color: var(--muted); }}
  .dup-summary {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }}
  .dup-badge {{ display: inline-block; padding: 2px 7px; border-radius: 3px; font-size: 9.5px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; }}
  .dup-badge.ok      {{ background: rgba(34,197,94,.16);  color: #22c55e; }}
  .dup-badge.muted   {{ background: rgba(136,136,136,.18);color: #aaa; }}
  .dup-badge.fail    {{ background: rgba(229,57,53,.18);  color: #e53935; }}
  .dup-badge.dry-run {{ background: rgba(126,184,212,.14);color: #7eb8d4; }}
  .dup-table {{ width: 100%; }}
  .dup-table th {{ font-size: 9px; text-align: left; padding: 5px 8px; color: var(--muted); border-bottom: 1px solid var(--border); }}
  .dup-table td {{ padding: 5px 8px; font-size: 10.5px; border-bottom: 1px solid var(--border); vertical-align: middle; }}
  .dup-status {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 9px; font-weight: 700; letter-spacing: .05em; }}
  .dup-status.ok      {{ background: rgba(34,197,94,.16);  color: #22c55e; }}
  .dup-status.muted   {{ background: rgba(136,136,136,.18);color: #aaa; }}
  .dup-status.fail    {{ background: rgba(229,57,53,.18);  color: #e53935; }}
  .dup-status.dry-run {{ background: rgba(126,184,212,.14);color: #7eb8d4; }}
  .dup-err {{ display: block; color: #e53935; font-size: 9.5px; margin-top: 3px; }}
  .dup-footer {{ color: var(--muted); font-size: 10.5px; margin-top: 8px; }}
  .dup-footer code {{ background: var(--surface); padding: 1px 5px; border-radius: 3px; font-family: var(--mono); }}
  /* Tag pills for the Layer column */
  .tag-pill {{ display: inline-block; padding: 1px 5px; border-radius: 2px; font-size: 9px; font-weight: 700; letter-spacing: .05em; margin-left: 5px; }}
  .tag-pill.tt    {{ background: rgba(126,184,212,.14); color: #7eb8d4; }}
  .tag-pill.lgl   {{ background: rgba(200,130,10,.14); color: #c8820a; }}
  .tag-pill.cta   {{ background: rgba(34,197,94,.12);  color: #22c55e; }}
  .tag-pill.hero  {{ background: rgba(176,126,212,.14);color: #b07ed4; }}
  .tag-pill.bg    {{ background: rgba(74,74,85,.30);   color: #aaa; }}
  .tag-pill.logo  {{ background: rgba(212,194,126,.14);color: #d4c27e; }}
  .tag-pill.sup   {{ background: rgba(126,212,176,.14);color: #7ed4b0; }}
  .tag-pill.disc  {{ background: rgba(200,130,10,.10); color: #c8820a; }}
  .tag-pill.unclass {{ background: rgba(68,68,68,.30); color: #aaa; }}
  .tag-pill.fill  {{ background: rgba(0,204,255,.14); color: #00ccff; }}
  .tag-pill.fit   {{ background: rgba(76,175,80,.14); color: #4caf50; }}
  .tag-pill.type  {{ background: rgba(126,184,212,.14); color: #7eb8d4; }}
  .tag-pill.legals {{ background: rgba(200,130,10,.14); color: #c8820a; }}
  .tag-pill.background {{ background: rgba(74,74,85,.30); color: #aaa; }}
  .tag-pill.keyart {{ background: rgba(176,126,212,.14); color: #b07ed4; }}
  .tag-pill.boxart {{ background: rgba(212,194,126,.14); color: #d4c27e; }}
  .tag-pill.artwork {{ background: rgba(126,212,176,.14); color: #7ed4b0; }}
  .tag-pill.animation {{ background: rgba(176,126,212,.14); color: #b07ed4; }}
  .tag-pill.guide {{ background: rgba(85,85,85,.30); color: #888; }}
  .tag-pill.protect {{ background: rgba(85,85,85,.30); color: #888; }}
  /* ── Classification source badges ─────────────────────────────── */
  .src-badge {{ display: inline-block; padding: 1px 4px; border-radius: 2px; font-size: 8.5px; font-weight: 700; letter-spacing: .06em; margin-left: 3px; vertical-align: middle; cursor: default; }}
  .src-badge.manual   {{ background: rgba(245,166,35,.18);  color: #f5a623; }}
  .src-badge.ai       {{ background: rgba(0,204,255,.14);   color: #00ccff; }}
  .src-badge.heuristic{{ background: rgba(176,126,212,.14); color: #b07ed4; }}
  .src-badge.profile  {{ background: rgba(34,197,94,.12);   color: #22c55e; }}
  .src-badge.structural{{ background: rgba(74,74,85,.30);   color: #888;   }}
  .conf-pct {{ font-size: 8.5px; color: var(--muted); margin-left: 2px; }}
  /* ── Compact Diagnostics & AI Insights ──────────────────────── */
  .ai-insights-card {{ margin-bottom: 12px; background: var(--surface); border: 1px solid var(--border); border-radius: 5px; padding: 10px 12px; }}
  .ai-insights-card.run-warnings-card {{ border-left: 3px solid var(--warn); }}
  .ai-insights-card.units-card {{ border-left: 3px solid var(--accent); }}
  .ai-insights-card h2 {{ font-size: 11px; font-weight: 700; color: var(--accent); letter-spacing: .05em; text-transform: uppercase; margin-bottom: 4px; display: flex; align-items: center; justify-content: space-between; }}
  .ai-summary {{ font-size: 11px; line-height: 1.4; color: var(--muted); margin-bottom: 6px; }}
  .ai-insight {{ display: flex; gap: 8px; align-items: center; padding: 3px 0; border-top: 1px solid rgba(255,255,255,.04); font-size: 10.5px; }}
  .ai-insight:first-of-type {{ border-top: none; }}
  .ai-level {{ font-size: 8.5px; font-weight: 700; letter-spacing: .05em; padding: 1px 5px; border-radius: 2px; white-space: nowrap; flex-shrink: 0; }}
  .ai-level.info  {{ background: rgba(0,204,255,.12);  color: #00ccff; }}
  .ai-level.warn  {{ background: rgba(245,166,35,.14); color: var(--warn); }}
  .ai-level.error {{ background: rgba(229,57,53,.15);  color: var(--fail); }}
  .ai-layer {{ color: var(--text); font-family: var(--mono); font-size: 10px; font-weight: 600; flex-shrink: 0; }}
  .ai-note {{ color: var(--muted); line-height: 1.4; }}
  /* ── Performance & Telemetry section ─────────────────────────── */
  .perf-section {{ margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--border); }}
  .perf-section h2 {{ font-size: 12px; font-weight: 700; color: var(--accent); letter-spacing: .04em; text-transform: uppercase; margin-bottom: 8px; display: flex; align-items: center; justify-content: space-between; }}
  .perf-summary-pill {{ font-size: 10px; font-family: var(--mono); color: var(--muted); font-weight: normal; }}
  .perf-details {{ margin-top: 8px; }}
  .perf-details summary {{ font-size: 10.5px; color: var(--muted); cursor: pointer; padding: 4px 0; outline: none; }}
  .perf-details summary:hover {{ color: var(--text); }}
  .perf-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 6px; margin-top: 8px; margin-bottom: 6px; }}
  .perf-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 6px 10px; display: flex; justify-content: space-between; align-items: center; }}
  .perf-label {{ font-size: 10px; color: var(--muted); }}
  .perf-value {{ font-family: var(--mono); font-size: 10.5px; font-weight: 700; color: var(--text); }}
  .toast {{ position: fixed; bottom: 20px; right: 20px; background: #00ccff; color: #121214; padding: 8px 14px; border-radius: 4px; font-size: 11px; font-weight: 700; opacity: 0; pointer-events: none; transition: opacity .2s ease; z-index: 999; box-shadow: 0 4px 12px rgba(0,0,0,.5); }}
  .toast.show {{ opacity: 1; }}
</style>
</head>
<body>
<header>
  <div class="header-brand">
    <h1>DIMENSION CONFORM REPORT</h1>
    <span class="engine-tag">Dimension Engine v5.0 · Session {session_id}</span>
  </div>
  <div class="meta">
    <div class="meta-specs">
      <div>Source: <strong style="color:var(--text)">{source_name}</strong> · Target: <strong style="color:var(--text)">{target_w}×{target_h}</strong> ({orientation})</div>
      <div>Mode: <strong style="color:var(--text)">{scale_mode}</strong> · Aspect: <strong style="color:var(--text)">{aspect_strategy}</strong> · {timestamp}</div>
    </div>
    <div class="export-bar">
      <button class="export-btn tech-report" id="btn-tech-report" onclick="dispatchTechReport()" title="⚡ One-Click AI Agent Triage: Copies Dual-Tier Triage Prompt to Clipboard">⚡ Tech Report</button>
      <a class="export-btn subtle" href="{json_filename}" download title="Download raw layer data as JSON">JSON</a>
      <a class="export-btn subtle" href="{csv_filename}" download title="Download raw layer data as CSV">CSV</a>
    </div>
  </div>
</header>

<div class="summary">
  <div class="card"><span class="card-label">Total Layers</span><span class="card-value">{total_layers}</span></div>
  <div class="card"><span class="card-label">Chunks</span><span class="card-value">{total_chunks}</span></div>
  <div class="card"><span class="card-label">Uniform Scale</span><span class="card-value">{uniform_scale}×</span></div>
  <div class="card"><span class="card-label">Collapse Warnings</span><span class="card-value{warn_class}">{collapse_count}</span></div>
</div>

{placement_units_section}
{run_warnings_section}
<table>
<thead>
  <tr>
    <th>#</th>
    <th>Layer Name</th>
    <th>Classification</th>
    <th>Position → Conformed</th>
    <th>Anchor → Conformed</th>
    <th>Scale</th>
    <th>Flags</th>
  </tr>
</thead>
<tbody>
{rows}
</tbody>
</table>
{variants_section}
{soe_section}
{collapse_section}
{duplication_section}
{performance_section}
<footer>Dimension v5.0 — NeuralIO Pipeline Division</footer>
<div id="toast" class="toast">QA Summary Copied to Clipboard!</div>
<script>
var _REPORT_DATA = {report_data_json};

function dispatchTechReport() {{
  if (!_REPORT_DATA || !_REPORT_DATA.meta) return;
  var m = _REPORT_DATA.meta;
  var collapse = m.collapse_warnings || 0;
  var sid = m.session_id;

  var lines = [
    "# ⚡ DIMENSION DUAL-TIER AGENTIC TRIAGE DISPATCH",
    "Session: `" + sid + "` | Generated: " + m.timestamp,
    "",
    "## 🎯 TIER 1: AEP Project & Composition Scope (Artist / Comp Level)",
    "- **Source Comp:** `" + m.source_name + "`",
    "- **Target Resolution:** `" + m.target_w + "x" + m.target_h + "` (" + m.scale_mode + ", " + m.uniform_scale + "x)",
    "- **Layers:** " + m.total_layers + " across " + m.total_chunks + " chunks",
    "- **Comp Hierarchy Flags:** " + (collapse > 0 ? ("⚠️ " + collapse + " collapsed layer warnings detected! Inspect 3D / continuous rasterization / nested scale.") : "✅ Clean comp hierarchy. No collapse warnings."),
    "- **Agent Instruction:** Check `artifacts/scrape_manifest.json` for layer geometry, zero-dimension layers, and parenting cycles.",
    "",
    "## ⚙️ TIER 2: Dimension Tool & Pipeline Scope (Engine / Bridge Level)",
    "- **Engine Execution:** Pipeline completed (" + m.total_chunks + " chunks).",
    "- **Offline Bundle Command:** `python3 python/tools/debug_bundle.py " + sid + "`",
    "- **Packaged Artifacts:** HTML/JSON reports, `dimension.log` correlation, `transfer_status.log` pump tail, `scrape_manifest.json`, `conformed_manifest.json`, `chunk_manifest.json`, `soe_corrections.json`, and `ae_probe_status.json`.",
    "- **Agent Instruction:** Determine if fault originates in Python scale math or ExtendScript injection pump."
  ];

  if (_REPORT_DATA.layers && _REPORT_DATA.layers.length > 0) {{
    var layerDeltas = _REPORT_DATA.layers.map(function(l) {{
      var dx = 0, dy = 0;
      if (l.src_pos && l.dst_pos && l.src_pos.length >= 2 && l.dst_pos.length >= 2) {{
        dx = Math.round(l.dst_pos[0] - l.src_pos[0]);
        dy = Math.round(l.dst_pos[1] - l.src_pos[1]);
      }}
      var mag = Math.sqrt(dx * dx + dy * dy);
      return {{ name: l.name, tag: l.content_tag || 'UNCLASS', dx: dx, dy: dy, mag: mag }};
    }});
    layerDeltas.sort(function(a, b) {{ return b.mag - a.mag; }});
    var top3 = layerDeltas.slice(0, 3);
    lines.push("", "### 📐 Top Layer Coordinate Shifts (In-line Telemetry)");
    top3.forEach(function(item) {{
      lines.push("- `" + item.name + "` [" + item.tag + "]: ΔX " + (item.dx >= 0 ? "+" : "") + item.dx + "px, ΔY " + (item.dy >= 0 ? "+" : "") + item.dy + "px (shift: " + Math.round(item.mag) + "px)");
    }});
  }}

  if (_REPORT_DATA.run_warnings && _REPORT_DATA.run_warnings.length > 0) {{
    lines.push("", "## ⚠️ Active Pipeline Warnings:");
    _REPORT_DATA.run_warnings.forEach(function(w) {{ lines.push("- " + w); }});
  }} else {{
    lines.push("", "- **Pipeline Health:** Clean run (0 runtime warnings).");
  }}

  var text = lines.join("\\n");
  navigator.clipboard.writeText(text).then(function() {{
    var toast = document.getElementById("toast");
    if (toast) {{
      toast.innerText = "⚡ Tech Report Copied! Ready to paste to coding agent.";
      toast.classList.add("show");
      setTimeout(function() {{ toast.classList.remove("show"); }}, 2500);
    }}
  }}).catch(function() {{
    prompt("Copy Tech Report Triage Prompt:", text);
  }});
}}
</script>
</body>
</html>
"""


# ── v5.2.5 SOE audit ─────────────────────────────────────────────


# Strategy → (badge text, badge css class). Order matches the spec.
_STRATEGY_BADGE: Dict[str, tuple] = {
    "NONE":               ("CLEAR",      "clear"),
    "TRANSLATE":          ("RELAYOUT",   "relayout"),
    "TIGHTEN_MARGIN":     ("RELAYOUT",   "relayout"),
    "ANCHOR_SHIFT":       ("RELAYOUT",   "relayout"),
    "SKIPPED_KEYED":      ("KEYED",      "keyed"),
    "SKIPPED_NO_BOUNDS":  ("NO_BOUNDS",  "no-bounds"),
    "SKIPPED_STRUCTURAL": ("STRUCTURAL", "structural"),
    "SOE_FAILED":         ("FAILED",     "failed"),
}

# Sort priority — failures first so the reviewer's eye lands on
# what needs attention, success last. Within a strategy, layer
# index ascending.
_STRATEGY_PRIORITY: Dict[str, int] = {
    "SOE_FAILED":         0,
    "TRANSLATE":          1,
    "TIGHTEN_MARGIN":     1,
    "ANCHOR_SHIFT":       1,
    "SKIPPED_KEYED":      2,
    "SKIPPED_NO_BOUNDS":  2,
    "SKIPPED_STRUCTURAL": 3,
    "NONE":               4,
}


def _load_soe_corrections(aep_dir: Path) -> Optional[List[Dict[str, Any]]]:
    """Load SOE corrections from `<aep_dir>/.dimension/soe_corrections.json`.

    Three return states:
      None — file missing → caller OMITS the audit section entirely.
      []   — file exists but empty → caller renders empty-state.
      [..] — file populated → caller renders the full audit table.

    Corrupt JSON is treated as missing — log WARN and return None so
    the report still ships clean.
    """
    path = aep_dir / ".dimension" / "soe_corrections.json"
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("SOE_REPORT_LOAD_FAILED",
                    extra={"path": str(path), "error": str(e)})
        return None
    if not isinstance(data, list):
        log.warning("SOE_REPORT_LOAD_FAILED",
                    extra={"path": str(path),
                           "error": "top-level JSON is not a list"})
        return None
    return data


def _load_safe_zone_verification(project_root: Path) -> Optional[Dict[str, Any]]:
    """#420 — was the SOE's plan (`soe_corrections.json`) actually verified
    against the injected comp, or is this report only showing intent?

    Scans `transfer_status.log` for the LAST audit-phase line carrying a
    `qc` field -- that field is only present when `Auditor.runFull` (#427)
    actually executed, distinguishing it from the legacy `performAudit`-
    only line shape that predates that wiring. Three return states:

      None                                — runFull never ran (older
                                             project, or #427 not yet
                                             active for this session).
                                             Caller shows "not verified".
      {"drift": []}                       — ran clean; no SAFE_ZONE_DRIFT
                                             warning. Caller shows a
                                             verified-clean badge.
      {"drift": [...]}                    — ran and found drift. Caller
                                             shows the disagreement list.

    Corrupt or missing log is treated as "never ran" -- log WARN, return
    None, report still ships clean. Mirrors `_load_soe_corrections`'s own
    three-state contract and fail-open posture.
    """
    log_path = project_root / "transfer_status.log"
    if not log_path.is_file():
        return None

    last_qc_line: Optional[Dict[str, Any]] = None
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(entry, dict) and "qc" in entry:
                    last_qc_line = entry
    except OSError as e:
        log.warning("SAFE_ZONE_VERIFICATION_LOAD_FAILED",
                    extra={"path": str(log_path), "error": str(e)})
        return None

    if last_qc_line is None:
        return None

    warnings = last_qc_line.get("warnings") or []
    drift = [w for w in warnings if isinstance(w, dict) and w.get("check") == "SAFE_ZONE_DRIFT"]
    return {"drift": drift[0]["layers"] if drift else []}


def _render_safe_zone_verification_badge(verification: Optional[Dict[str, Any]]) -> str:
    """"Verified post-inject" badge for the SOE section header.

    None → "not verified" (honest about the gap, per CLAUDE.md's "honest
    failure beats clever recovery" -- this must never claim verification
    that didn't happen). Empty drift list → verified clean. Non-empty →
    verified, with the count of layers that drifted post-inject.
    """
    if verification is None:
        return (
            '<span class="soe-verify-badge soe-verify-unknown" '
            'title="No post-inject audit data found for this session -- '
            'this report only shows what SOE planned, not confirmed AE state.">'
            '⚠ Not verified post-inject</span>'
        )
    drift = verification.get("drift") or []
    if not drift:
        return (
            '<span class="soe-verify-badge soe-verify-ok" '
            'title="Auditor.runFull confirmed every SOE-corrected layer '
            'landed at its planned position in the injected comp.">'
            '✓ Verified post-inject</span>'
        )
    return (
        f'<span class="soe-verify-badge soe-verify-drift" '
        f'title="Auditor.runFull found {len(drift)} layer(s) whose '
        f'actual injected position drifted from SOE\'s plan.">'
        f'⚠ {len(drift)} layer(s) drifted post-inject</span>'
    )


def _fmt_pos(pos: Any) -> str:
    """`(x, y)` rendering, dropping z when zero."""
    try:
        x = float(pos[0]); y = float(pos[1])
    except (TypeError, ValueError, IndexError):
        return "—"
    z = 0.0
    if isinstance(pos, (list, tuple)) and len(pos) > 2:
        try:
            z = float(pos[2])
        except (TypeError, ValueError):
            z = 0.0
    if abs(z) > 1e-6:
        return f"{x:.1f}, {y:.1f}, {z:.1f}"
    return f"{x:.1f}, {y:.1f}"


def _zone_class(zone: Optional[str]) -> str:
    z = (zone or "UNKNOWN").upper()
    return {
        "CLEAR":   "zone-clear",
        "GO":      "zone-clear",
        "NUDGE":   "zone-nudge",
        "CUTOFF":  "zone-cutoff",
    }.get(z, "zone-unknown")


def _zone_label(zone: Optional[str]) -> str:
    z = (zone or "UNKNOWN").upper()
    return "CLEAR" if z == "GO" else z


def _tag_pill(tag: Optional[str]) -> str:
    if not tag:
        return ""
    safe = _h(str(tag))
    cls = safe.lower()
    return f'<span class="tag-pill {cls}">{safe}</span>'


_SRC_ABBREV = {
    "manual_label":   ("M", "manual",    "Manual AE label color"),
    "manual_comment": ("M", "manual",    "Manual #TAG comment"),
    "manual":         ("M", "manual",    "Manual override"),
    "ai":             ("A", "ai",        "AI classifier"),
    "ollama":         ("A", "ai",        "AI classifier (Ollama)"),
    "heuristic":      ("H", "heuristic", "Heuristic keyword match"),
    "profile":        ("P", "profile",   "Studio profile rule"),
    "structural":     ("S", "structural","Structural (auto)"),
}


def _source_badge(source: Optional[str], confidence: Optional[float]) -> str:
    if not source:
        return ""
    letter, cls, tooltip = _SRC_ABBREV.get(
        source, (source[:1].upper(), "heuristic", source)
    )
    conf_html = ""
    if confidence is not None:
        pct = int(round(float(confidence) * 100))
        conf_html = f'<span class="conf-pct">{pct}%</span>'
    return (
        f'<span class="src-badge {cls}" title="{_h(tooltip)}">{letter}</span>'
        f'{conf_html}'
    )


def _truncate(name: str, limit: int = 40) -> tuple:
    """Return (rendered_text, full_for_title)."""
    if len(name) <= limit:
        return name, name
    return name[: limit - 1] + "…", name


def _load_creations_as_log(aep_dir, expected_session_id):
    """Read the SQLite store when the legacy JSON log is gone."""
    try:
        from core.dimension_db import list_creations, open_project_db
        conn = open_project_db(str(aep_dir))
        try:
            rows = list_creations(conn, session=expected_session_id or None)
        finally:
            conn.close()
    except Exception:
        return None
    if not rows:
        return None
    return {
        "session_id": expected_session_id or rows[0].get("session") or "",
        "creations": rows,
        "source": "dimension.db",
    }


def _load_duplication_log(
    aep_dir: Path,
    *,
    expected_session_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Load `<aep_dir>/.dimension/duplication_log.json` if present.
    None signals the section should be omitted (no duplication ran).

    `expected_session_id`: when set, returns None unless the on-disk
    log's `session_id` matches. Prevents the cross-session leak where
    a previous conform's duplication log gets rendered into a fresh
    conform's report (when the new conform skipped duplication
    entirely)."""
    log_path = aep_dir / ".dimension" / "duplication_log.json"
    if not log_path.is_file():
        return _load_creations_as_log(aep_dir, expected_session_id)
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("duplication_log.json unreadable",
                    extra={"path": str(log_path), "error": str(e)})
        return None
    if expected_session_id and isinstance(payload, dict):
        if payload.get("session_id") != expected_session_id:
            # Defensive check retained as backstop against cross-session
            # log leaks (a prior session's duplication_log.json present
            # on disk when the current session ran without duplication
            # — Babysitter only unlinks it as part of applyDuplicationPlan).
            # Pre-Slot-9 this fired on every conform-with-duplication
            # because the report ran before the inject wrote the current
            # session's log; post-Slot-9 the report runs after inject so
            # current-session reads should always match. Mismatches now
            # indicate a real cross-session leak — keep the line but
            # downgrade to DEBUG so it's not background noise.
            log.debug(
                "duplication_log.json session_id mismatch — ignoring",
                extra={"path":     str(log_path),
                        "expected": expected_session_id,
                        "got":      payload.get("session_id")},
            )
            return None
    return payload


# ── Slot 12.5 Stage D: collapse-disable warnings ──────────────────


def _load_collapse_overrides(project_root: Path) -> Optional[List[Dict[str, Any]]]:
    """Load `<project_root>/inject_collapse_overrides.json` if present.

    Slot 12.5 Stage D (2026-05-19): Babysitter's rewire phase force-
    disables `collapseTransformation` on every mirror-tree wrapper —
    the wrapper-compensation math depends on the precomp rasterizing
    its target-dimensioned mirror, which a collapse-ON wrapper
    bypasses. Each per-run override is written to a sidecar JSON
    beside `transfer_status.log` so the report can surface a designer-
    visible warning.

    Returns the override list (possibly empty) when the sidecar
    exists, or None when no Stage D inject ran this session (the
    section is then omitted from the report).
    """
    sidecar = project_root / "inject_collapse_overrides.json"
    if not sidecar.is_file():
        return None
    try:
        with open(sidecar, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning(
            "inject_collapse_overrides.json unreadable",
            extra={"path": str(sidecar), "error": str(e)},
        )
        return None
    raw = payload.get("collapse_overrides")
    if not isinstance(raw, list):
        return []
    return raw


def _render_collapse_overrides_section(
    overrides: Optional[List[Dict[str, Any]]],
) -> str:
    """v12.5 Stage D — visible designer-facing warning section listing
    every wrapper whose Collapse Transformations flag Dimension
    force-disabled at inject time. `None` (sidecar absent — no Stage
    D inject ran) omits the section entirely; an empty list (Stage D
    inject ran, no collapse-on wrappers encountered) also omits.
    A non-empty list always renders as amber so a designer scanning
    the report sees it without hunting through a log.
    """
    if not overrides:
        return ""

    rows_html = []
    for ov in overrides:
        wrapper_name = ov.get("wrapper_name") or "—"
        wrapper_uid = ov.get("wrapper_uid") or "—"
        wrapper_in = ov.get("wrapper_in_source_comp") or "—"
        target_mirror = ov.get("target_mirror_source_name") or "—"
        rows_html.append(
            f"<tr>"
            f"<td>{wrapper_name}</td>"
            f"<td>{wrapper_in}</td>"
            f"<td>{target_mirror}</td>"
            f'<td><code>{wrapper_uid}</code></td>'
            f"</tr>"
        )

    count = len(overrides)
    plural = "" if count == 1 else "s"
    return (
        '<section class="collapse-section amber">'
        '<h2>Collapse Transformations — Disabled by Dimension</h2>'
        f'<p class="collapse-summary">'
        f'<strong>{count}</strong> wrapper{plural} had '
        f'Collapse Transformations <strong>disabled</strong> during inject '
        f'so the conform could render correctly. '
        f'Your source comps are unchanged; the override applies only to the '
        f'mirror-tree wrappers in the conformed output. '
        f'If your design intent relies on collapsed-precomp behavior '
        f'(blur sharing, scale-pass-through, etc.), re-enable the flag manually '
        f'on the listed wrappers in the conformed output and re-render — '
        f'note that doing so may re-introduce the off-canvas geometry '
        f'this override prevents.'
        f'</p>'
        '<table class="collapse-table">'
        '<thead><tr>'
        '<th>Wrapper</th><th>In Comp</th><th>Target Mirror Source</th><th>UID</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table>'
        '</section>'
    )


def _render_duplication_section(
    plan_dict: Optional[Dict[str, Any]],
    duplication_log: Optional[Dict[str, Any]],
    *,
    dry_run: bool = False,
) -> str:
    """v5.8 — "Precomp Duplications" report section per spec Part G.

    States:
      - plan empty / both inputs None → section omitted
      - plan present, dry_run=True → "Dry Run" preview
      - log present, no errors → green section
      - log present, with errors → amber section
    """
    # Nothing to show.
    if plan_dict is None and duplication_log is None:
        return ""

    # Determine state.
    if duplication_log is None:
        # Plan was made but never executed (dry-run preview path).
        log_data = {
            "duplicates_made": [],
            "rewires_made":    [],
            "skipped":         [],
            "errors":          [],
        }
        is_dry = True
    else:
        log_data = duplication_log
        is_dry = bool(dry_run)

    plan = plan_dict or {}
    duplicates = list(plan.get("duplicates", []))
    if not duplicates and not log_data.get("duplicates_made") \
            and not log_data.get("skipped") and not log_data.get("errors"):
        # Truly nothing happened.
        return ""

    has_errors = bool(log_data.get("errors"))
    section_class = "amber" if has_errors else "ok"
    if is_dry:
        section_class = "dry-run"

    # Header badge.
    badges: List[str] = []
    if is_dry:
        badges.append('<span class="dup-badge dry-run">DRY RUN</span>')
    if log_data.get("duplicates_made"):
        n = len(log_data["duplicates_made"])
        badges.append(f'<span class="dup-badge ok">{n} created</span>')
    if log_data.get("skipped"):
        n = len(log_data["skipped"])
        badges.append(f'<span class="dup-badge muted">{n} skipped</span>')
    if has_errors:
        n = len(log_data["errors"])
        badges.append(f'<span class="dup-badge fail">{n} failed</span>')
    badges_html = (
        f'<div class="dup-summary">{"".join(badges)}</div>'
        if badges else ""
    )

    # Index log entries by original_uid for status mapping.
    made_by_uid: Dict[str, Dict[str, Any]] = {
        str(d.get("original_uid")): d for d in (log_data.get("duplicates_made") or [])
    }
    skipped_by_uid: Dict[str, Dict[str, Any]] = {
        str(s.get("original_uid")): s for s in (log_data.get("skipped") or [])
    }
    errors_by_uid: Dict[str, List[Dict[str, Any]]] = {}
    for err in (log_data.get("errors") or []):
        uid = str(err.get("original_uid") or "")
        errors_by_uid.setdefault(uid, []).append(err)

    # Build rows. If we have a plan, walk plan order (so the report
    # reflects what the user reviewed in the modal). Otherwise fall
    # back to whatever the log captured.
    rows_html: List[str] = []

    if duplicates:
        for dup in duplicates:
            uid = str(dup.get("original_uid") or "")
            name = _h(str(dup.get("original_name") or ""))
            dup_name = _h(str(dup.get("duplicate_name") or ""))
            folder = _h(str(dup.get("target_folder_path") or ""))
            reason = str(dup.get("reason") or "SHARED")

            if is_dry:
                status_badge = '<span class="dup-status dry-run">DRY RUN</span>'
            elif uid in errors_by_uid:
                status_badge = '<span class="dup-status fail">FAILED</span>'
            elif uid in skipped_by_uid:
                why = skipped_by_uid[uid].get("reason") or "SKIPPED"
                status_badge = (
                    f'<span class="dup-status muted">SKIPPED · {_h(why)}</span>')
            elif uid in made_by_uid:
                status_badge = '<span class="dup-status ok">CREATED</span>'
            else:
                status_badge = '<span class="dup-status muted">—</span>'

            err_detail = ""
            if uid in errors_by_uid:
                err_detail = "<br/>".join(
                    f'<span class="dup-err">{_h(str(e.get("phase","")))}: '
                    f'{_h(str(e.get("detail","")))}</span>'
                    for e in errors_by_uid[uid]
                )

            rows_html.append(
                f"<tr>"
                f"<td>{name}</td>"
                f"<td>{_h(reason)}</td>"
                f"<td><code>{dup_name}</code></td>"
                f"<td><code>{folder}</code></td>"
                f"<td>{status_badge}{err_detail}</td>"
                f"</tr>"
            )

    # Footer line — only for the success path with at least one made.
    footer_html = ""
    if not is_dry and made_by_uid:
        n = len(made_by_uid)
        # Pull the folder from the first plan entry (all share one).
        session_folder = ""
        if duplicates:
            session_folder = str(duplicates[0].get("target_folder_path") or "")
        elif log_data.get("duplicates_made"):
            # Fall back to "From Dimensions" prefix.
            session_folder = "From Dimensions/<unknown session>"
        footer_html = (
            f'<p class="dup-footer">'
            f'Created {n} duplicate{"s" if n != 1 else ""} in '
            f'<code>{_h(session_folder)}</code></p>'
        )

    table_html = (
        '<table class="dup-table">'
        '<thead><tr>'
        '<th>Precomp</th>'
        '<th>Reason</th>'
        '<th>Duplicate Name</th>'
        '<th>Folder</th>'
        '<th>Status</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table>'
    ) if rows_html else ""

    return (
        f'<section class="dup-section {section_class}">'
        f'<h2>Precomp Duplications</h2>'
        f'{badges_html}'
        f'{table_html}'
        f'{footer_html}'
        f'</section>'
    )


def _render_variants_section(conformed_manifest: dict) -> str:
    """Variants section — which layers a `variant:` directive hid on this
    target, and which it kept.

    Renders nothing at all when no layer carries a directive, which is
    every conform until an artist writes one. An empty-state card for a
    feature nobody is using is noise in a QC report that already has
    plenty of sections.

    Reads `conformed_enabled` / `variant_buckets`, which the engine
    stamps only on layers that declared a directive — so "absent" and
    "declared but shown" stay distinguishable here, as they must:
    a layer that was never in the variant system at all is not the same
    as one an artist deliberately kept on this format.
    """
    layers = conformed_manifest.get("layers") or []
    declared = [
        layer for layer in layers
        if layer.get("conformed_enabled") is not None
    ]
    if not declared:
        return ""

    hidden = [layer for layer in declared if layer.get("conformed_enabled") is False]
    shown = [layer for layer in declared if layer.get("conformed_enabled") is True]

    rows: List[str] = []
    for layer in declared:
        is_hidden = layer.get("conformed_enabled") is False
        buckets = layer.get("variant_buckets") or []
        rows.append(
            '<tr>'
            f'<td>{_h(str(layer.get("name") or ""))}</td>'
            f'<td>{_h(", ".join(str(b) for b in buckets)) or "—"}</td>'
            f'<td><span class="soe-badge {"keyed" if is_hidden else "relayout"}">'
            f'{"hidden" if is_hidden else "rendered"}</span></td>'
            '</tr>'
        )

    badges = (
        f'<span class="soe-badge relayout">{len(shown)} rendered</span>'
        f'<span class="soe-badge keyed">{len(hidden)} hidden</span>'
    )

    return (
        '<section class="soe-audit">'
        '<h2>Variants</h2>'
        f'<div class="soe-summary">{badges}</div>'
        '<table class="soe-table">'
        '<thead><tr><th>Layer</th><th>Declared for</th><th>On this target</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
        '<p class="soe-note">Hidden layers are excluded from gravity '
        'grouping and safe-zone checks, and their video switch is turned '
        'off in the conformed comp.</p>'
        '</section>'
    )


def _render_soe_audit_section(
    corrections: Optional[List[Dict[str, Any]]],
    verification: Optional[Dict[str, Any]] = None,
) -> str:
    """Audit section HTML. None → empty string (section omitted).

    `verification` (#420) is independent of `corrections` -- a session
    can have SOE corrections (a plan) with or without a post-inject
    verification of that plan (a fact). Badge renders in both the
    empty-state and populated branches so the "not verified" case is
    never silently invisible just because a comp happened to have no
    layers needing occlusion checking.
    """
    if corrections is None:
        return ""

    verify_badge = _render_safe_zone_verification_badge(verification)

    if not corrections:
        return (
            '<section class="soe-audit">'
            f'<h2>Spatial Occlusion Audit {verify_badge}</h2>'
            '<p class="empty-state">'
            'No layers required occlusion checking. '
            'SOE only validates TYPE and LEGALS layers against safe-zone masks.'
            '</p>'
            '</section>'
        )

    # Summary counts.
    relayouts = sum(1 for c in corrections if c.get("strategy") in
                    ("TRANSLATE", "TIGHTEN_MARGIN", "ANCHOR_SHIFT"))
    keyed = sum(1 for c in corrections if c.get("strategy") == "SKIPPED_KEYED")
    no_bounds = sum(
        1 for c in corrections if c.get("strategy") == "SKIPPED_NO_BOUNDS"
    )
    structural = sum(
        1 for c in corrections if c.get("strategy") == "SKIPPED_STRUCTURAL"
    )
    failed = sum(1 for c in corrections if c.get("strategy") == "SOE_FAILED")
    clear = sum(1 for c in corrections if c.get("strategy") == "NONE")

    summary_badges: List[str] = []
    if relayouts:
        summary_badges.append(
            f'<span class="soe-badge relayout">{relayouts} relayouts</span>')
    if keyed:
        summary_badges.append(
            f'<span class="soe-badge keyed">{keyed} keyed</span>')
    if no_bounds:
        summary_badges.append(
            f'<span class="soe-badge no-bounds">{no_bounds} no_bounds</span>')
    if structural:
        summary_badges.append(
            f'<span class="soe-badge structural">{structural} structural</span>')
    if failed:
        summary_badges.append(
            f'<span class="soe-badge failed">{failed} failed</span>')
    if clear:
        summary_badges.append(
            f'<span class="soe-badge clear">{clear} clear</span>')

    summary_html = (
        f'<div class="soe-summary">{"".join(summary_badges)}</div>'
        if summary_badges else ""
    )

    # Sort by strategy priority then by layer_index.
    sorted_rows = sorted(
        corrections,
        key=lambda c: (
            _STRATEGY_PRIORITY.get(c.get("strategy", ""), 99),
            int(c.get("layer_index", 0) or 0),
        ),
    )

    rows_html: List[str] = []
    for c in sorted_rows:
        strategy = str(c.get("strategy") or "")
        badge_text, badge_cls = _STRATEGY_BADGE.get(
            strategy, (strategy, "structural"))
        name = str(c.get("layer_name") or "")
        rendered_name, full_name = _truncate(name)
        tag_html = _tag_pill(c.get("content_tag"))

        original_pos = c.get("original_position") or [0, 0, 0]
        corrected_pos = c.get("corrected_position") or original_pos
        if strategy == "NONE":
            corrected_render = "—"
        else:
            corrected_render = _h(_fmt_pos(corrected_pos))

        # Zone hit — show "before → after" arrow when corrected.
        original_zone = c.get("original_zone_hit")
        zone_hit = c.get("zone_hit") or "UNKNOWN"
        if original_zone and original_zone.upper() != zone_hit.upper():
            zone_html = (
                f'<span class="soe-badge {_zone_class(original_zone)}">'
                f'{_h(_zone_label(original_zone))}</span>'
                '<span class="soe-zone-arrow">→</span>'
                f'<span class="soe-badge {_zone_class(zone_hit)}">'
                f'{_h(_zone_label(zone_hit))}</span>'
            )
        else:
            zone_html = (
                f'<span class="soe-badge {_zone_class(zone_hit)}">'
                f'{_h(_zone_label(zone_hit))}</span>'
            )

        move = c.get("move_distance_px") or 0.0
        try:
            move_render = f"{float(move):.1f}" if float(move) > 0 else "—"
        except (TypeError, ValueError):
            move_render = "—"

        notes = _h(str(c.get("notes") or ""))

        rows_html.append(
            "<tr>"
            f'<td title="{_h(full_name)}">{_h(rendered_name)}{tag_html}</td>'
            f"<td>{_h(_fmt_pos(original_pos))}</td>"
            f"<td>{corrected_render}</td>"
            f"<td>{zone_html}</td>"
            f'<td><span class="soe-badge {badge_cls}">{badge_text}</span></td>'
            f"<td>{move_render}</td>"
            f'<td class="notes">{notes}</td>'
            "</tr>"
        )

    return (
        '<section class="soe-audit">'
        f'<h2>Spatial Occlusion Audit {verify_badge}</h2>'
        f'{summary_html}'
        '<table class="soe-table">'
        '<thead><tr>'
        '<th>Layer</th>'
        '<th>Original Position</th>'
        '<th>Corrected Position</th>'
        '<th>Zone Hit</th>'
        '<th>Strategy</th>'
        '<th>Move (px)</th>'
        '<th>Notes</th>'
        '</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table>'
        '</section>'
    )


def _render_performance_section(
    scrape_manifest: Dict[str, Any],
    project_root: Path
) -> str:
    scrape_meta = scrape_manifest.get("scrape_meta") or {}
    scrape_telemetry = scrape_meta.get("telemetry") or {}

    telemetry_data = []

    # Scrape timings
    if scrape_telemetry:
        if "comp_walk_ms" in scrape_telemetry:
            telemetry_data.append(("Scrape: BFS Comp Walk (JSX)", f"{scrape_telemetry.get('comp_walk_ms'):.0f} ms"))
        if "per_layer_scrape_ms" in scrape_telemetry:
            telemetry_data.append(("Scrape: Per-layer Property Scrape (JSX)", f"{scrape_telemetry.get('per_layer_scrape_ms'):.0f} ms"))
        if "project_structure_scan_ms" in scrape_telemetry:
            telemetry_data.append(("Scrape: Project Structure Scan (JSX)", f"{scrape_telemetry.get('project_structure_scan_ms'):.0f} ms"))
        if "total_scrape_ms" in scrape_telemetry:
            telemetry_data.append(("Scrape: Total Scrape Duration (JSX)", f"{scrape_telemetry.get('total_scrape_ms'):.0f} ms"))

    # Conform/Inject timings from transfer_status.log
    log_path = project_root / "transfer_status.log"
    if log_path.is_file():
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("event") == "telemetry":
                            phase = entry.get("phase", "")
                            duration = entry.get("duration_ms", 0)
                            if phase == "setupWorkspace":
                                telemetry_data.append(("Conform: Workspace Setup (JSX)", f"{duration:.0f} ms"))
                            elif phase == "rewire":
                                telemetry_data.append(("Conform: Precomp Rewire (JSX)", f"{duration:.0f} ms"))
                            elif phase == "chunk_processing":
                                ch_idx = entry.get("chunk_index", 0)
                                telemetry_data.append((f"Conform: Chunk {ch_idx+1} Processing (JSX)", f"{duration:.0f} ms"))
                            elif phase == "output_comp_inject":
                                comp_n = entry.get("comp_name", "")
                                telemetry_data.append((f"Conform: Inject -> {comp_n} (JSX)", f"{duration:.0f} ms"))
                            elif phase == "audit":
                                telemetry_data.append(("Conform: Post-Conform Audit (JSX)", f"{duration:.0f} ms"))
                            elif phase == "python_manifest_validate":
                                telemetry_data.append(("Conform: Manifest Validate (Python)", f"{duration:.2f} ms"))
                            elif phase == "python_scale_engine":
                                telemetry_data.append(("Conform: Scale Engine (Python)", f"{duration:.2f} ms"))
                            elif phase == "python_lerp_engine":
                                telemetry_data.append(("Conform: Lerp Engine (Python)", f"{duration:.2f} ms"))
                            elif phase == "python_effect_conformer":
                                telemetry_data.append(("Conform: Effect Conformer (Python)", f"{duration:.2f} ms"))
                            elif phase == "python_chunk_export":
                                telemetry_data.append(("Conform: Chunk Export (Python)", f"{duration:.2f} ms"))
                            elif phase == "python_bridge_dispatch":
                                telemetry_data.append(("Scrape: Bridge Dispatch (Python)", f"{duration:.2f} ms"))
                    except Exception:
                        pass
        except Exception:
            pass

    if not telemetry_data:
        return ""

    cards_html = []
    for label, val in telemetry_data:
        cards_html.append(
            f'<div class="perf-card">'
            f'<span class="perf-label">{label}</span>'
            f'<span class="perf-value">{val}</span>'
            f'</div>'
        )

    # For concise visual density, if there are many per-chunk JSX inject items,
    # show the first 4 in a grid and fold the rest in a <details> accordion.
    if len(cards_html) > 4:
        primary_cards = "".join(cards_html[:4])
        extended_cards = "".join(cards_html[4:])
        inner_html = (
            f'<div class="perf-grid">{primary_cards}</div>'
            f'<details class="perf-details">'
            f'<summary>▶ View all {len(cards_html)} telemetry metrics (chunks & sub-phases)...</summary>'
            f'<div class="perf-grid">{extended_cards}</div>'
            f'</details>'
        )
    else:
        inner_html = f'<div class="perf-grid">{"".join(cards_html)}</div>'

    return (
        f'<section class="perf-section">'
        f'<h2>Performance & Telemetry <span class="perf-summary-pill">{len(telemetry_data)} metrics recorded</span></h2>'
        f'{inner_html}'
        f'</section>'
    )


def _fmt_vec(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(f"{x:.2f}" for x in v) + "]"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _changed(a: Any, b: Any, threshold: float = 0.5) -> bool:
    """True if the vectors differ by more than threshold on any axis."""
    try:
        if isinstance(a, list) and isinstance(b, list):
            return any(abs(x - y) > threshold for x, y in zip(a, b))
        return abs(float(a) - float(b)) > threshold
    except Exception:
        return False


def _render_placement_units_section(pu: Optional[Dict[str, Any]]) -> str:
    """U1 — render the 'How Dimension read this comp' card: the
    placement-unit tree (camera scenes, sealed precomps, groups,
    singletons) with each unit's members and its auto-layout anchor.

    Returns "" when no unit data is present (legacy callers)."""
    if not pu or not pu.get("units"):
        return ""
    _KIND_LABEL = {
        "camera_scene": "CAMERA SCENE",
        "precomp": "PRECOMP",
        "group": "GROUP",
        "singleton": "LAYER",
    }
    rows = []
    for u in pu["units"]:
        kind = u.get("kind", "")
        if kind == "root_frame":
            continue
        members = u.get("members") or []
        names = ", ".join(_h(m.get("name", "")) for m in members[:8])
        if len(members) > 8:
            names += f" … +{len(members) - 8} more"
        # U2 Phase 7 — "auto read → resolved": anchor_auto is what the
        # builder READ; anchor_resolved is what the conform DID (stamped
        # from the same resolution object the math consumed). Legacy
        # payloads without the stamp fall back to the read alone.
        _auto = str(u.get("anchor_auto", "") or "")
        _resolved = str(u.get("anchor_resolved", "") or "")
        if _resolved and _auto:
            anchor_txt = f" — read: {_h(_auto)} → did: {_h(_resolved)}"
        elif _resolved:
            anchor_txt = f" — did: {_h(_resolved)}"
        elif _auto:
            anchor_txt = f" — {_h(_auto)}"
        else:
            anchor_txt = ""
        rows.append(
            '<div class="ai-insight">'
            f'<span class="ai-level info">{_KIND_LABEL.get(kind, kind.upper())}</span>'
            f'<span class="ai-layer">{_h(str(u.get("label", "")))}</span>'
            f'<span class="ai-note">{names}'
            f'{anchor_txt}'
            '</span></div>'
        )
    return (
        '<div class="ai-insights-card units-card">'
        '<h2>How Dimension Read This Comp</h2>'
        f'<p class="ai-summary">{_h(str(pu.get("summary", "")))} — units are '
        'placed as wholes; geometry inside a unit is preserved. If this '
        'partition looks wrong to you, that is the thing to flag.</p>'
        + "".join(rows)
        + "</div>"
    )


def _build_units_json(
    pu: Optional[Dict[str, Any]],
    layer_uid_lookup: Dict[tuple, Optional[str]],
) -> List[Dict[str, Any]]:
    """report-dashboard-v1 Item 1 — additive JSON block mirroring the HTML
    'How Dimension Read This Comp' unit tree (`_render_placement_units_section`
    above) as structured data instead of an HTML fragment, so the in-panel
    dashboard can render its own unit cards without scraping HTML.

    Each unit's `members` carry `uid` (resolved via the comp-scoped
    `(containing_comp_id, index)` key, per the CLAUDE.md sharp edge on
    flat-index collisions across precomps) so a member name click can
    drive `selectLayerByUid` the same way the layer table does.

    `read` / `did` mirror `anchor_auto` / `anchor_resolved` — the same
    two strings the HTML section joins into "read: X → did: Y". Kept
    separate here so the dashboard can style/format them itself.

    Returns `[]` when no placement-unit data is present (legacy callers,
    or a run where unit-building degraded) — never omits the key.
    """
    if not pu or not pu.get("units"):
        return []
    out: List[Dict[str, Any]] = []
    for u in pu["units"]:
        if u.get("kind") == "root_frame":
            continue
        members_json: List[Dict[str, Any]] = []
        for m in (u.get("members") or []):
            comp_id = m.get("comp_id")
            index = m.get("index")
            members_json.append({
                "name":    m.get("name", ""),
                "uid":     layer_uid_lookup.get((comp_id, index)),
                "index":   index,
                "comp_id": comp_id,
            })
        out.append({
            "unit_id": u.get("unit_id"),
            "kind":    u.get("kind", ""),
            "label":   u.get("label", ""),
            "members": members_json,
            "read":    u.get("anchor_auto") or "",
            "did":     u.get("anchor_resolved") or "",
        })
    return out


def _render_run_warnings_section(warnings: List[str]) -> str:
    """Phase 1 (loud failures) — render the Run Warnings card listing every
    degraded-but-continued path from this run (survey degradations carried
    on the manifest + pipeline-runtime warnings from the orchestrator).

    Returns "" when there is nothing to show — a clean run stays clean.
    """
    if not warnings:
        return ""
    rows = "".join(
        f'<div class="ai-insight">'
        f'<span class="ai-level warn">WARN</span>'
        f'<span class="ai-note">{_h(str(w))}</span>'
        f'</div>'
        for w in warnings
    )
    return (
        '<div class="ai-insights-card run-warnings-card">'
        '<h2>Run Warnings</h2>'
        '<p class="ai-summary">These steps degraded or were skipped during '
        'this run. The conform still completed, but review each item — '
        'output may differ from what you expected.</p>'
        + rows
        + "</div>"
    )


def _orientation_label(target_w, target_h) -> str:
    """Target shape as an artist reads it (HORIZONTAL / SQUARE / VERTICAL).

    Sits beside `Aspect:` (the conform engine's relative strategy —
    preserve/widen/narrow) rather than replacing it: the two answer
    different questions and both belong in a QC report. Never raises —
    a report must still render for a manifest with junk dimensions, so a
    bad value degrades to an em dash rather than failing the run
    (report_generator's "omit sections gracefully" contract).
    """
    try:
        from core.orientation import orientation_bucket

        return orientation_bucket(int(target_w), int(target_h)).value
    except Exception:  # noqa: BLE001 — report surface, never fatal
        return "—"


def generate_report(
    conformed_manifest: Dict[str, Any],
    scrape_manifest: Dict[str, Any],
    preset_label: str,
    target_w: int,
    target_h: int,
    scale_mode: str,
    uniform_scale: float,
    session_id: str,
    output_path: str = "conform_report.html",
    aep_dir: Optional[Path] = None,
    *,
    duplication_plan: Optional[Dict[str, Any]] = None,
    duplication_dry_run: bool = False,
    run_warnings: Optional[List[str]] = None,
    placement_units: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Build and write conform_report.html.
    Returns the absolute path to the written file.

    `aep_dir` is the project-adjacent directory holding `.dimension/`
    sidecars (scrape_manifest.json, soe_corrections.json). Defaults
    to the directory of `output_path` for backwards compat.
    """
    if aep_dir is None:
        aep_dir = Path(output_path).resolve().parent
    layers: List[Dict] = conformed_manifest.get("layers", [])
    collapsed_names = conformed_manifest.get("warnings", {}).get("collapsed_layers", [])
    collapsed_set = set(collapsed_names)

    # report-dashboard-v1 Item 1 — comp-scoped (containing_comp_id, index)
    # → uid lookup, used to resolve `units[].members[].uid` below. Same
    # tuple-key pattern as everywhere else in the pipeline that indexes
    # per-layer state (see CLAUDE.md's "comp-scoped identity" sharp edge).
    _layer_uid_lookup: Dict[tuple, Optional[str]] = {
        (layer.get("containing_comp_id"), layer.get("index")): layer.get("uid")
        for layer in layers
    }

    source_name = scrape_manifest.get("project_info", {}).get("name", "Unknown")
    total_layers = len(layers)
    total_chunks = (total_layers + MAX_CHUNK_SIZE - 1) // MAX_CHUNK_SIZE

    rows_html = []
    for layer in layers:
        name = layer.get("name", "—")
        idx = layer.get("index", "?")
        tf = layer.get("conformed_transforms", {})
        is_collapsed = name in collapsed_set
        is_brittle = layer.get("isBrittle", False)

        src_pos = layer.get("position") or [0, 0, 0]
        src_anc = layer.get("anchor") or [0, 0, 0]
        dst_pos = tf.get("position", src_pos)
        dst_anc = tf.get("anchor", src_anc)
        dst_scale = tf.get("scale", [100, 100, 100])

        pos_changed = _changed(src_pos, dst_pos)
        anc_changed = _changed(src_anc, dst_anc)

        flags = []
        if is_collapsed:
            flags.append('<span class="badge badge-warn">COLLAPSE</span>')
        if is_brittle:
            flags.append('<span class="badge badge-warn">BRITTLE</span>')
        # PR #121 follow-up — group-aware gravity diagnostic. Silent for
        # the common case (None or size 1); only shown when this layer's
        # gravity placement anchored a multi-member group.
        group_size = layer.get("gravity_group_size")
        if group_size and group_size > 1:
            flags.append(f'<span class="badge badge-ok">GROUP×{group_size}</span>')
        if not flags:
            flags.append('<span class="badge badge-ok">OK</span>')

        row_class = ' class="warn"' if is_collapsed else ""
        pos_class = "changed" if pos_changed else ""
        anc_class = "changed" if anc_changed else ""

        tag_pill = _tag_pill(layer.get("content_tag"))
        src_badge = _source_badge(
            layer.get("content_tag_source"),
            layer.get("content_tag_confidence"),
        )

        # `index` is only unique per-comp, not across the flattened
        # layer list (a precomp's layer 1 and the root comp's layer 1
        # share the same number) — see CLAUDE.md's comp-scoped-identity
        # sharp edge. Surface containing_comp_id as a hover tooltip so
        # the visible "#" stays simple but disambiguation is one hover
        # away. None → top-level/root comp, shown as "root".
        comp_id = layer.get("containing_comp_id")
        idx_title = f'title="comp: {comp_id if comp_id is not None else "root"}"'

        # Visibility-only surface for `layer.archetype` (issue #423 /
        # #418) — computed by surveyor.py and SovCore_Layer.jsx but read
        # by nothing downstream. Diagnostic tooltip on the layer name;
        # deliberately not folded into any gravity/confidence math here.
        # `.value` unwraps `str(LayerArchetype.TYPE)` == "LayerArchetype.TYPE"
        # on Python 3.11+ (CLAUDE.md sharp edge) — this field may still be a
        # live enum instance here if the manifest hasn't round-tripped
        # through JSON yet.
        archetype = layer.get("archetype")
        archetype = getattr(archetype, "value", archetype)
        name_title = f' title="Archetype: {_h(str(archetype))}"' if archetype else ""

        rows_html.append(
            f"<tr{row_class}>"
            f"<td {idx_title}>{idx}</td>"
            f"<td{name_title}>{name}</td>"
            f"<td>{tag_pill}{src_badge}</td>"
            f'<td><span class="delta">{_fmt_vec(src_pos)}</span> → <span class="delta {pos_class}">{_fmt_vec(dst_pos)}</span></td>'
            f'<td><span class="delta">{_fmt_vec(src_anc)}</span> → <span class="delta {anc_class}">{_fmt_vec(dst_anc)}</span></td>'
            f"<td>{_fmt_vec(dst_scale)}</td>"
            f"<td>{''.join(flags)}</td>"
            f"</tr>"
        )

    warn_class = " warn" if collapsed_names else ""

    # transfer_status.log and other project-root sidecars live next to
    # output_path, not aep_dir (which holds .dimension/ specifically) --
    # computed once, up front, since both the SOE section and the
    # collapse-overrides section below need it.
    project_root = Path(output_path).resolve().parent

    # v5.2.5 SOE audit section. None → omit entirely; [] → empty
    # state; [...] → full audit table. #420 — paired with a post-inject
    # verification badge read from transfer_status.log, so the section
    # says whether this is a plan or a confirmed fact.
    soe_section = _render_soe_audit_section(
        _load_soe_corrections(Path(aep_dir)),
        _load_safe_zone_verification(project_root),
    )
    variants_section = _render_variants_section(conformed_manifest)

    # Slot 12.5 Stage D — collapse-disable warnings. Babysitter
    # writes `inject_collapse_overrides.json` next to
    # transfer_status.log when the rewire phase force-disables a
    # wrapper's collapseTransformation flag. None → no Stage D inject
    # ran (section omitted); empty list → Stage D ran with no
    # collapse-on wrappers; non-empty → visible amber warning section
    # listing every override.
    collapse_section = _render_collapse_overrides_section(
        _load_collapse_overrides(project_root)
    )

    # v5.8 Precomp Duplications section. Reads duplication_log.json
    # if present; combines with the plan from the conform flow.
    # session_id alignment guards against a stale prior-session log
    # leaking into a fresh report.
    duplication_section = _render_duplication_section(
        duplication_plan,
        _load_duplication_log(
            Path(aep_dir),
            expected_session_id=session_id,
        ),
        dry_run=duplication_dry_run,
    )

    performance_section = _render_performance_section(scrape_manifest, project_root)

    # ── Embedded export data ──────────────────────────────────────────
    # Serialise all layer data into a JS-safe JSON blob so the
    # client-side exportJSON() / exportCSV() buttons can work with
    # zero server round-trips. The blob is embedded verbatim in a
    # <script> tag; json.dumps produces valid JS object literal syntax.
    export_layers = []
    for layer in layers:
        tf = layer.get("conformed_transforms", {})
        src_pos = layer.get("position") or [0, 0, 0]
        src_anc = layer.get("anchor") or [0, 0, 0]
        name = layer.get("name", "")
        export_layers.append({
            "index":            layer.get("index"),
            # index is only unique per-comp; disambiguate downstream
            # consumers (scripts, spreadsheets) the same way the HTML
            # table's tooltip does. None = top-level/root comp.
            "containing_comp_id": layer.get("containing_comp_id"),
            # uid is the session-stable layer identity (comment-stamped
            # at scrape). None-safe: legacy manifests without stamps
            # export null. Unblocks dashboard click-to-select
            # (docs/design/REPORT-DASHBOARD-UI-SPEC.md, Eng notes).
            "uid":              layer.get("uid"),
            "name":             name,
            "content_tag":      layer.get("content_tag"),
            "tag_source":       layer.get("content_tag_source"),
            "tag_confidence":   layer.get("content_tag_confidence"),
            "layer_kind":       layer.get("layer_kind"),
            # Visibility-only surface for issue #423/#418 — see the
            # tooltip comment above; diagnostic export, not consumed by
            # any conform math. `.value` unwraps a live LayerArchetype
            # enum instance to its plain string (same sharp edge as the
            # HTML tooltip above); JSON serialization alone already gets
            # this right, but the CSV writer's `str()` call would not.
            "archetype":        getattr(layer.get("archetype"), "value", layer.get("archetype")),
            "src_pos":          [round(v, 3) for v in src_pos] if isinstance(src_pos, list) else src_pos,
            "dst_pos":          [round(v, 3) for v in tf.get("position", src_pos)] if isinstance(tf.get("position", src_pos), list) else tf.get("position", src_pos),
            "src_anc":          [round(v, 3) for v in src_anc] if isinstance(src_anc, list) else src_anc,
            "dst_anc":          [round(v, 3) for v in tf.get("anchor", src_anc)] if isinstance(tf.get("anchor", src_anc), list) else tf.get("anchor", src_anc),
            "dst_scale":        tf.get("scale", [100, 100, 100]),
            "is_collapsed":     name in collapsed_set,
            "is_brittle":       bool(layer.get("isBrittle", False)),
            "gravity_group_size": layer.get("gravity_group_size"),
        })

    report_data = {
        "meta": {
            "session_id":       session_id,
            "source_name":      source_name,
            "target_w":         target_w,
            "target_h":         target_h,
            "scale_mode":       scale_mode,
            "uniform_scale":    round(uniform_scale, 6),
            "aspect_strategy":  conformed_manifest.get("aspect_strategy") or "",
            "timestamp":        time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_layers":     total_layers,
            "total_chunks":     total_chunks,
            "collapse_warnings": len(collapsed_names),
        },
        "layers": export_layers,
    }

    # ── AI Insights (Integration 1) ───────────────────────────────────────
    # Only fires when ollama_enabled is set in user preferences. Silent on
    # any exception (Ollama down, import error, etc.).
    # Phase 1 (loud failures) — combined degradation list for the Run
    # Warnings card: survey-time degradations travel on the manifest
    # (survey_warnings, written by surveyor.py), pipeline-runtime ones
    # arrive via the run_warnings parameter (orchestrator.py).
    _all_warnings: List[str] = []
    _sw = scrape_manifest.get("survey_warnings") or []
    if isinstance(_sw, list):
        _all_warnings.extend(str(w) for w in _sw)
    _all_warnings.extend(str(w) for w in (run_warnings or []))

    # Attach warnings BEFORE AI insights so analyze_conform_report can
    # narrate real pipeline degradations (not invent QC fluff).
    report_data["run_warnings"] = list(_all_warnings)

    run_warnings_section = _render_run_warnings_section(_all_warnings)
    placement_units_section = _render_placement_units_section(placement_units)

    # report-dashboard-v1 Item 1 — additive JSON blocks (units + run_warnings)
    # for the in-panel dashboard (docs/design/REPORT-DASHBOARD-UI-SPEC.md).
    # Assigned here (after `_all_warnings` is fully finalized, including the
    # AI-insights-omitted fallback message above) and BEFORE report_data_json
    # is serialized below, so the embedded <script> blob and the .json
    # sidecar file stay byte-consistent with each other. Purely additive —
    # every existing report_data key above is untouched.
    report_data["units"] = _build_units_json(placement_units, _layer_uid_lookup)
    report_data["run_warnings"] = list(_all_warnings)
    report_data_json = json.dumps(report_data, ensure_ascii=False)
    # `run_warnings` (and `units[].label`/member names) now carry
    # free-text/artist-authored content into this blob, which is embedded
    # verbatim inside a literal <script> tag below — unlike the HTML table
    # rows, nothing here runs through `_h()`. A warning or layer name
    # containing "<script>...</script>" would otherwise close the real
    # script tag early and let the rest render as markup (caught by
    # test_loud_failures.py::test_warning_text_is_html_escaped once
    # run_warnings started round-tripping through this blob). Standard
    # mitigation for embedding JSON inside HTML: escape the three HTML-
    # significant characters to \uXXXX — invisible to JSON.parse/JS string
    # literals, but they can no longer form "<", ">", or "&" byte sequences
    # in the rendered page.
    report_data_json = (
        report_data_json
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )

    abs_path = os.path.abspath(output_path)
    stem = os.path.splitext(abs_path)[0]
    json_filename = os.path.basename(stem) + ".json"
    csv_filename  = os.path.basename(stem) + ".csv"

    html = _HTML_TEMPLATE.format(
        session_id=session_id,
        source_name=source_name,
        target_w=target_w,
        target_h=target_h,
        scale_mode=scale_mode,
        aspect_strategy=conformed_manifest.get("aspect_strategy") or "—",
        orientation=_orientation_label(target_w, target_h),
        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        total_layers=total_layers,
        total_chunks=total_chunks,
        uniform_scale=round(uniform_scale, 4),
        collapse_count=len(collapsed_names),
        warn_class=warn_class,
        rows="\n".join(rows_html),
        soe_section=soe_section,
        variants_section=variants_section,
        collapse_section=collapse_section,
        duplication_section=duplication_section,
        performance_section=performance_section,
        run_warnings_section=run_warnings_section,
        placement_units_section=placement_units_section,
        report_data_json=report_data_json,
        json_filename=json_filename,
        csv_filename=csv_filename,
    )

    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Write JSON and CSV sidecars alongside the HTML so the ↓ JSON / ↓ CSV
    # buttons can use plain relative <a href> links (file:// relative links
    # always work in Chrome; programmatic blob/data downloads do not).
    stem = os.path.splitext(abs_path)[0]
    try:
        with open(stem + ".json", "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    try:
        csv_rows = [["#", "Layer Name", "Tag", "Tag Source", "Confidence",
                     "Archetype",
                     "Src Position", "Dst Position",
                     "Src Anchor", "Dst Anchor", "Scale", "Collapsed", "Brittle"]]
        for lyr in export_layers:
            # `lyr` is a dict from `export_layers` (built above), where
            # "archetype" is already unwrapped to a plain string — see the
            # `getattr(..., "value", ...)` there. No further unwrap needed.
            conf = lyr.get("tag_confidence")
            csv_rows.append([
                lyr["index"],
                '"' + str(lyr["name"]).replace('"', '""') + '"',
                lyr.get("content_tag") or "",
                lyr.get("tag_source") or "",
                f"{int(round(float(conf)*100))}%" if conf is not None else "",
                lyr.get("archetype") or "",
                '"' + ", ".join(str(v) for v in (lyr.get("src_pos") or [])) + '"',
                '"' + ", ".join(str(v) for v in (lyr.get("dst_pos") or [])) + '"',
                '"' + ", ".join(str(v) for v in (lyr.get("src_anc") or [])) + '"',
                '"' + ", ".join(str(v) for v in (lyr.get("dst_anc") or [])) + '"',
                '"' + ", ".join(str(v) for v in (lyr.get("dst_scale") or [])) + '"',
                "YES" if lyr.get("is_collapsed") else "",
                "YES" if lyr.get("is_brittle") else "",
            ])
        with open(stem + ".csv", "w", encoding="utf-8") as f:
            f.write("\n".join(",".join(str(c) for c in row) for row in csv_rows))
    except OSError:
        pass

    return abs_path


_BATCH_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Dimension Batch Conform Report</title>
<style>
  :root {{
    --bg: #1a1a1e; --surface: #23232a; --border: #2e2e38;
    --text: #e1e1e6; --muted: #888; --accent: #00ccff;
    --warn: #f5a623; --ok: #4caf50; --fail: #e53935;
    --mono: "SF Mono","Fira Code","Cascadia Code",monospace;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; font-size: 13px; padding: 24px; }}
  header {{ display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 24px; }}
  h1 {{ font-size: 20px; font-weight: 700; color: var(--accent); letter-spacing: .04em; }}
  .meta {{ color: var(--muted); font-size: 11px; text-align: right; line-height: 1.8; }}
  .summary {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 12px; margin-bottom: 24px; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 6px; padding: 14px 16px; }}
  .card-label {{ font-size: 10px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin-bottom: 6px; }}
  .card-value {{ font-size: 22px; font-weight: 700; color: var(--accent); }}
  .card-value.fail {{ color: var(--fail); }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ background: var(--surface); color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: .06em; padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); position: sticky; top: 0; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); font-family: var(--mono); font-size: 11px; vertical-align: middle; }}
  tr:hover td {{ background: rgba(255,255,255,.03); }}
  .badge {{ display: inline-block; padding: 3px 8px; border-radius: 3px; font-size: 10px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }}
  .badge-ok   {{ background: rgba(76,175,80,.15);  color: var(--ok); }}
  .badge-fail {{ background: rgba(229,57,53,.15);  color: var(--fail); }}
  .btn {{ display: inline-block; padding: 4px 10px; background: rgba(0,204,255,.12); color: var(--accent); text-decoration: none; border-radius: 4px; font-weight: 600; font-size: 10px; border: 1px solid rgba(0,204,255,.2); }}
  .btn:hover {{ background: rgba(0,204,255,.2); }}
  footer {{ margin-top: 40px; text-align: center; color: var(--muted); font-size: 11px; }}
  .error-msg {{ color: var(--fail); font-style: italic; font-size: 10px; }}
</style>
</head>
<body>
<header>
  <div>
    <h1>DIMENSION BATCH CONFORM REPORT</h1>
    <div style="color:var(--muted);font-size:11px;margin-top:4px;">Dimension Engine v5.0 — Session {batch_session_id}</div>
  </div>
  <div class="meta">
    <div>Source: <strong style="color:var(--text)">{source_name}</strong></div>
    <div>Generated: {timestamp}</div>
  </div>
</header>

<div class="summary">
  <div class="card"><div class="card-label">Total Targets</div><div class="card-value">{total_targets}</div></div>
  <div class="card"><div class="card-label">Successful</div><div class="card-value">{successful_targets}</div></div>
  <div class="card"><div class="card-label">Failed</div><div class="card-value {fail_class}">{failed_targets}</div></div>
</div>

<table>
<thead>
  <tr>
    <th>Format</th>
    <th>Resolution</th>
    <th>Aspect Strategy</th>
    <th>Layers</th>
    <th>Duplicates</th>
    <th>Status</th>
    <th>Action</th>
  </tr>
</thead>
<tbody>
{rows}
</tbody>
</table>

<footer>Dimension v5.0 — NeuralIO Pipeline Division</footer>
</body>
</html>
"""

def generate_batch_report(
    batch_session_id: str,
    source_name: str,
    batch_results: List[Dict[str, Any]],
    output_path: str,
    in_archive: bool = False,
) -> str:
    rows_html = []
    successful_targets = 0
    failed_targets = 0
    
    for res in batch_results:
        fmt_name = _h(str(res.get("target_name") or ""))
        w = res.get("width") or 0
        h = res.get("height") or 0
        res_str = f"{w} × {h}"
        strategy = _h(str(res.get("aspect_strategy") or "—"))
        layers = res.get("layers_count") or 0
        duplicates = res.get("duplicates_count") or 0
        status = res.get("status") or "SUCCESS"
        
        if status == "SUCCESS":
            successful_targets += 1
            status_badge = '<span class="badge badge-ok">SUCCESS</span>'
            
            # Resolve link path based on whether the report is in the archive folder or project root
            if in_archive:
                link = f"./{res.get('target_id')}/conform_report.html"
            else:
                link = f"./logs/archive/{batch_session_id}/{res.get('target_id')}/conform_report.html"
                
            action_html = f'<a class="btn" href="{link}">View Detailed Report</a>'
        else:
            failed_targets += 1
            err_msg = _h(str(res.get("error_message") or "Unknown error"))
            status_badge = f'<span class="badge badge-fail">FAILED</span>'
            action_html = f'<span class="error-msg">{err_msg}</span>'
            
        rows_html.append(
            f"<tr>"
            f"<td><strong>{fmt_name}</strong></td>"
            f"<td>{res_str}</td>"
            f"<td>{strategy}</td>"
            f"<td>{layers}</td>"
            f"<td>{duplicates}</td>"
            f"<td>{status_badge}</td>"
            f"<td>{action_html}</td>"
            f"</tr>"
        )
        
    fail_class = "fail" if failed_targets > 0 else ""
    
    html = _BATCH_HTML_TEMPLATE.format(
        batch_session_id=batch_session_id,
        source_name=_h(source_name),
        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        total_targets=len(batch_results),
        successful_targets=successful_targets,
        failed_targets=failed_targets,
        fail_class=fail_class,
        rows="\n".join(rows_html),
    )
    
    abs_path = os.path.abspath(output_path)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(html)
        
    return abs_path
