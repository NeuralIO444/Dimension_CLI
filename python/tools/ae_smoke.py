#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/ae_smoke.py — live After Effects smoke harness.

WHY THIS EXISTS
---------------
Dimension's JSX side has had no mechanical verification of any kind. Python
has a reachability gate and a 3,200-test suite; ExtendScript had neither,
so JSX rotted silently. Two consequences, both confirmed 2026-09-04:

  - `Auditor.runFull` -- the entire post-inject QC suite -- has never run.
    The module is not even loaded into the AE session (`$.global.Auditor`
    is undefined). A 2026-06-05 audit found the same thing and the fix was
    never applied; nothing flagged it in the three months since (#427).
  - All four structured `ae_bridge_mcp` tools shipped with bare top-level
    `return`s, which ExtendScript rejects. Every call failed. The MCP was
    effectively non-functional from registration until it was fixed
    alongside this file.

Both are the same failure: nobody was looking. This harness looks.

WHAT IT CHECKS
--------------
`preflight` answers "is the AE side actually what we think it is" -- which
module namespaces are loaded, which expected entry points exist, and which
are missing. That check alone would have caught the Auditor gap on the day
it appeared rather than three months later.

`capture` snapshots the active comp and its layers into a JSON artifact a
test (or a later session) can assert against, so an AE round-trip produces
evidence instead of a human's recollection of what looked right.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not mutate the project. No layer creation, no conform, no inject.
Everything here is read-only introspection.

That is a deliberate v1 boundary, not laziness: this runs against whatever
project the operator happens to have open, and a harness that silently
edited a real client comp would be far worse than no harness. Driving a
full scrape/conform/inject round-trip needs a dedicated throwaway AEP and
an explicit opt-in flag, and belongs in a follow-up once this half is
trusted.

REQUIREMENTS
------------
macOS, After Effects running, the Dimension panel/launcher loaded. Not
usable from a cloud or headless session -- AE needs a real GUI login, which
is also why the autonomous loop can never run these checks itself.

USAGE
-----
    python -m tools.ae_smoke preflight
    python -m tools.ae_smoke preflight --json
    python -m tools.ae_smoke capture --out /tmp/ae_state.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.ae_eval import evaluate, wrap_iife  # noqa: E402

# Namespaces/entry points the panel is expected to have loaded, and why each
# matters. `required` entries failing means the pipeline stage they back
# cannot run at all -- which is precisely the Auditor situation.
EXPECTED_MODULES: List[Dict[str, Any]] = [
    {
        "probe": '$.global["com.neuralio444.dimension"]',
        "name": "DIMENSION namespace",
        "required": True,
        "note": "root namespace; scrape lives under .core.atoms",
    },
    {
        "probe": '$.global["com.neuralio444.dimension"] && '
                 '$.global["com.neuralio444.dimension"].core && '
                 '$.global["com.neuralio444.dimension"].core.atoms && '
                 '$.global["com.neuralio444.dimension"].core.atoms.scrapeUnified',
        "name": "Sovereign_Core scrapeUnified",
        "required": True,
        "note": "the scraper entry point -- stage 1 of the pipeline",
    },
    {
        "probe": "$.global.Babysitter",
        "name": "Babysitter",
        "required": True,
        "note": "the injector -- stage 5",
    },
    {
        "probe": "$.global.Babysitter && $.global.Babysitter.performAudit",
        "name": "Babysitter.performAudit",
        "required": True,
        "note": "Babysitter's own lightweight audit (layer count, relining)",
    },
    {
        "probe": "$.global.Auditor",
        "name": "Auditor",
        "required": False,
        "note": "post-inject QC suite. KNOWN MISSING (#427) -- not loaded, "
                "and Babysitter never calls it. Camera-drift and "
                "variant-visibility checks have therefore never run.",
    },
    {
        "probe": "$.global.Auditor && $.global.Auditor.runFull",
        "name": "Auditor.runFull",
        "required": False,
        "note": "see #427 -- depends on Auditor being loaded at all",
    },
]


def _probe_modules() -> Dict[str, Any]:
    """One round-trip that reports presence of every expected entry point.

    Batched into a single eval on purpose: each AppleScript round-trip costs
    a few hundred ms, so probing six things separately would make the
    harness feel broken even when AE is healthy.
    """
    checks = ",".join(
        '{{name: {n}, present: !!({p})}}'.format(
            n=json.dumps(m["name"]), p=m["probe"]
        )
        for m in EXPECTED_MODULES
    )
    script = wrap_iife(
        "var out = [" + checks + "];\n"
        "var comp = app.project.activeItem;\n"
        "return {\n"
        '  status: "OK",\n'
        "  modules: out,\n"
        "  aeVersion: app.version,\n"
        "  projectFile: (app.project && app.project.file) ? app.project.file.fsName : null,\n"
        "  activeComp: (comp && comp instanceof CompItem) ? comp.name : null\n"
        "};"
    )
    return evaluate(script)


def preflight() -> Dict[str, Any]:
    raw = _probe_modules()
    if raw.get("status") != "OK":
        return {
            "ok": False,
            "reachable": False,
            "error": raw.get("error") or "AE not reachable",
            "transport": raw.get("transport"),
        }

    payload = raw.get("result") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return {"ok": False, "reachable": True,
                    "error": f"unparseable AE response: {payload[:200]}"}

    found = {m["name"]: bool(m.get("present")) for m in payload.get("modules", [])}
    results: List[Dict[str, Any]] = []
    missing_required: List[str] = []
    missing_optional: List[str] = []

    for spec in EXPECTED_MODULES:
        present = found.get(spec["name"], False)
        results.append({
            "name": spec["name"],
            "present": present,
            "required": spec["required"],
            "note": spec["note"],
        })
        if not present:
            (missing_required if spec["required"] else missing_optional).append(spec["name"])

    return {
        "ok": not missing_required,
        "reachable": True,
        "transport": raw.get("transport"),
        "ae_version": payload.get("aeVersion"),
        "project_file": payload.get("projectFile"),
        "active_comp": payload.get("activeComp"),
        "modules": results,
        "missing_required": missing_required,
        "missing_optional": missing_optional,
    }


def capture() -> Dict[str, Any]:
    """Read-only snapshot of the active comp and its layers."""
    script = wrap_iife("""
    var comp = app.project.activeItem;
    if (!comp || !(comp instanceof CompItem)) {
        return { status: "NO_ACTIVE_COMP" };
    }
    var layers = [];
    for (var i = 1; i <= comp.numLayers; i++) {
        var L = comp.layer(i);
        var pos = null, scl = null;
        try { pos = L.transform.position.value; } catch (e) {}
        try { scl = L.transform.scale.value; } catch (e) {}
        layers.push({
            index: L.index,
            name: L.name,
            enabled: L.enabled,
            label: L.label,
            comment: L.comment,
            threeD: L.threeDLayer,
            guide: (typeof L.guideLayer !== "undefined") ? L.guideLayer : null,
            locked: L.locked,
            position: pos,
            scale: scl
        });
    }
    return {
        status: "OK",
        comp: { name: comp.name, id: comp.id, width: comp.width,
                height: comp.height, fps: comp.frameRate,
                duration: comp.duration, numLayers: comp.numLayers },
        layers: layers
    };
    """)
    raw = evaluate(script, timeout=30.0)
    if raw.get("status") != "OK":
        return {"ok": False, "error": raw.get("error"), "transport": raw.get("transport")}
    payload = raw.get("result")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return {"ok": False, "error": "unparseable AE response"}
    return {"ok": payload.get("status") == "OK", "transport": raw.get("transport"), **(payload or {})}


def _render_preflight(rep: Dict[str, Any]) -> str:
    out: List[str] = []
    out.append("Dimension AE smoke — preflight")
    out.append("=" * 58)
    if not rep.get("reachable"):
        out.append(f"  AE NOT REACHABLE: {rep.get('error')}")
        out.append("")
        out.append("  Check: is After Effects running, with the Dimension panel open?")
        out.append("  This harness cannot run headless — AE needs a real GUI login.")
        return "\n".join(out)

    out.append(f"  transport      : {rep.get('transport')}")
    out.append(f"  AE version     : {rep.get('ae_version')}")
    out.append(f"  project        : {rep.get('project_file') or '(unsaved)'}")
    out.append(f"  active comp    : {rep.get('active_comp') or '(none)'}")
    out.append("")
    for m in rep.get("modules", []):
        mark = "ok  " if m["present"] else ("MISS" if m["required"] else "--  ")
        req = "required" if m["required"] else "optional"
        out.append(f"  [{mark}] {m['name']:<32} ({req})")
        if not m["present"]:
            out.append(f"         {m['note']}")
    out.append("")
    if rep["ok"]:
        out.append("  PASS — every required module is loaded.")
    else:
        out.append(f"  FAIL — missing required: {', '.join(rep['missing_required'])}")
    if rep.get("missing_optional"):
        out.append(f"  Missing optional: {', '.join(rep['missing_optional'])}")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="ae_smoke")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("preflight", help="verify AE is reachable and Dimension modules are loaded")
    p1.add_argument("--json", action="store_true")

    p2 = sub.add_parser("capture", help="snapshot the active comp and layers (read-only)")
    p2.add_argument("--json", action="store_true")
    p2.add_argument("--out", default=None, help="write the snapshot to this path")

    args = ap.parse_args(argv)

    if args.cmd == "preflight":
        rep = preflight()
        print(json.dumps(rep, indent=2) if args.json else _render_preflight(rep))
        return 0 if rep.get("ok") else 1

    rep = capture()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2)
        print(f"wrote {args.out}")
    else:
        print(json.dumps(rep, indent=2))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
