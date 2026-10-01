#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/ae_eval.py
Direct ExtendScript Terminal & CLI Evaluator for Adobe After Effects.

Evaluates arbitrary ExtendScript (JSX) code in a live After Effects instance.
Two working transports, tried in order:
  1. Direct TCP Socket IPC (port 45445) — ultra-fast (<5ms); socket_server.jsx
     has a real "eval" case for this.
  2. macOS AppleScript / osascript — OS-level fallback.

A third, file-bridge transport (.dimension_inbox/) is NOT implemented:
Dimension_Launcher.jsx's inbox job dispatcher and the Pydantic schema that
validates it (python/models/bridge_jobs.py) have no "eval" job type, only
scrape/tag-write/select-layer/query-layer-state/duplicate-plan/panel-slicing-plan/recon-plan/
mask-toggle/color-match-render/inject. Adding one is a deliberate addition
to the bridge contract (new job/result model + JSX dispatcher case + live
AE verification), not a quick fix -- see
eval_extendscript_file_bridge()'s docstring.

Usage:
  # Evaluate a direct JS expression
  python3 python/tools/ae_eval.py "app.project.activeItem ? app.project.activeItem.name : 'No Comp'"

  # Evaluate a script file
  python3 python/tools/ae_eval.py -f path/to/script.jsx

  # Inspect active comp
  python3 python/tools/ae_eval.py --active-comp

  # Inspect selected layers
  python3 python/tools/ae_eval.py --selected-layers

  # Trigger Sovereign Scrape
  python3 python/tools/ae_eval.py --scrape
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Optional

# Ensure python/ is on sys.path
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.ipc_client import execute_job


def eval_extendscript_socket(script_code: str, port: int = 45445, timeout: float = 10.0) -> Dict[str, Any]:
    """Execute ExtendScript via Dimension TCP Socket IPC on port 45445."""
    job = {
        "type": "eval",
        "script": script_code,
        "session_id": f"eval_{int(time.time()*1000)}",
    }
    return execute_job(job, port=port, timeout=timeout)


def eval_extendscript_file_bridge(script_code: str, inbox_dir: Optional[str] = None, timeout: float = 10.0) -> Dict[str, Any]:
    """Execute ExtendScript via Dimension Inbox File Bridge.

    NOT IMPLEMENTED. Dimension_Launcher.jsx's inbox job dispatcher (and the
    Pydantic discriminated union in python/models/bridge_jobs.py that
    validates outgoing jobs on the real bridge path) has no "eval" job
    type -- only scrape, tag-write, select-layer, query-layer-state,
    duplicate-plan, panel-slicing-plan, recon-plan, mask-toggle, color-match-render, and
    inject. This function used to write an ad hoc {"type": "eval", ...}
    job file that nothing on the JSX side would ever pick up or answer --
    it would sit in .dimension_inbox/ until the caller's `timeout` elapsed,
    then raise a generic TimeoutError that looked like "AE was slow" rather
    than "this transport doesn't exist for eval." Failing immediately
    instead, per this project's own "honest failure beats clever recovery"
    principle.

    Real support would mean a new Pydantic job/result model, a new JSX
    dispatcher case, and live AE verification (the kind of JSX<->Python
    wire-contract change synthetic tests can't catch) -- a deliberate
    addition to the bridge contract, not a bug fix. Until then, use the
    TCP socket transport (eval_extendscript_socket / socket_server.jsx's
    working "eval" case) or the AppleScript transport.
    """
    raise NotImplementedError(
        "File-bridge transport has no \"eval\" job type -- "
        "Dimension_Launcher.jsx's inbox dispatcher doesn't handle it. "
        "Use the TCP socket transport (open AE + LAUNCH ENGINE, listens "
        "on :45445) or the AppleScript transport instead."
    )


def eval_extendscript_applescript(script_code: str, timeout: float = 10.0) -> str:
    """Execute ExtendScript via macOS AppleScript osascript."""
    # Write to temp file to prevent escaping issues. mkstemp() creates the
    # file atomically (O_EXCL) with a random name, unlike a hand-built
    # timestamp path -- avoids a predictable-filename symlink race in /tmp.
    temp_fd, temp_jsx = tempfile.mkstemp(suffix=".jsx", prefix="ae_eval_")
    temp_out = f"{temp_jsx}.out.json"

    wrapped = f"""
try {{
    var __toJson = function(v) {{
        if (typeof v === "undefined") return "null";
        if (v === null) return "null";
        if (typeof JSON !== "undefined" && typeof JSON.stringify === "function") {{
            try {{
                var s = JSON.stringify(v);
                if (typeof s === "string") return s;
            }} catch(e) {{}}
        }}
        if (typeof v === "number" || typeof v === "boolean") return String(v);
        var s = String(v);
        return '"' + s.replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"').replace(/\\n/g, '\\\\n') + '"';
    }};
    var __res = eval({json.dumps(script_code)});
    var __f = new File('{temp_out}');
    __f.encoding = 'UTF-8';
    __f.open('w');
    __f.write('{{"status": "OK", "result": ' + __toJson(__res) + '}}');
    __f.close();
}} catch(__e) {{
    try {{
        var __f = new File('{temp_out}');
        __f.encoding = 'UTF-8';
        __f.open('w');
        var __errMsg = String(__e ? (__e.message || __e) : "Unknown error");
        __f.write('{{"status": "ERROR", "error": "' + __errMsg.replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"').replace(/\\n/g, '\\\\n') + '"}}');
        __f.close();
    }} catch(__e2) {{}}
}}
"""
    with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
        f.write(wrapped)

    osa_script = f'''
tell application id "com.adobe.AfterEffects.application"
    DoScriptFile (POSIX file "{temp_jsx}") with override
end tell
'''
    try:
        proc = subprocess.run(["osascript", "-e", osa_script], capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"osascript error: {proc.stderr}")
        
        # Read output file
        if os.path.exists(temp_out):
            with open(temp_out, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"status": "OK", "result": proc.stdout.strip()}
    finally:
        for p in (temp_jsx, temp_out):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def evaluate(script: str, timeout: float = 10.0) -> Dict[str, Any]:
    """
    Evaluate ExtendScript with automatic transport fallback.
    Returns dict with status, result, and transport used.
    """
    errors = []

    # 1. Try TCP Socket
    try:
        res = eval_extendscript_socket(script, timeout=min(timeout, 2.0))
        res["transport"] = "socket_ipc"
        return res
    except Exception as e:
        errors.append(f"Socket: {e}")

    # 2. Try File Bridge
    try:
        res = eval_extendscript_file_bridge(script, timeout=min(timeout, 3.0))
        res["transport"] = "file_bridge"
        return res
    except Exception as e:
        errors.append(f"FileBridge: {e}")

    # 3. Try AppleScript
    if sys.platform == "darwin":
        try:
            res = eval_extendscript_applescript(script, timeout=timeout)
            res["transport"] = "applescript"
            return res
        except Exception as e:
            errors.append(f"AppleScript: {e}")

    return {
        "status": "ERROR",
        "error": "All After Effects bridges failed to respond.",
        "details": errors,
        "hint": "Ensure Adobe After Effects is running and the Dimension panel or launcher is loaded."
    }


# ── High-Level Convenience Queries ──────────────────────────────────────────

def wrap_iife(body: str) -> str:
    """Wrap a script body in an IIFE so its top-level `return` is legal.

    ExtendScript rejects `return` outside a function body — "Illegal 'return'
    outside of a function body" — and `evaluate()` deliberately does NOT wrap
    what it is given: blindly wrapping an already-wrapped script would swallow
    its value (`(function(){ (function(){return 5})(); })()` is undefined, not
    5), silently turning every working caller's result into nothing.

    So wrapping is the individual script's job, and this is the helper for it.
    Every structured helper below and in `ae_bridge_mcp.py` must use it.

    This was not theoretical: all four structured MCP tools shipped with bare
    top-level returns and failed on every call, leaving only the raw
    `ae_eval_script` tool usable — and only for callers who happened to wrap
    their own script. Found 2026-09-04 by calling `ae_get_active_comp` against
    a live AE session.
    """
    return "(function () {\n" + body + "\n})();"


def get_active_comp_info() -> Dict[str, Any]:
    script = wrap_iife("""
    var comp = app.project.activeItem;
    if (!comp || !(comp instanceof CompItem)) {
        return { status: "NO_ACTIVE_COMP" };
    }
    return {
        status: "OK",
        id: comp.id,
        name: comp.name,
        width: comp.width,
        height: comp.height,
        fps: comp.frameRate,
        duration: comp.duration,
        numLayers: comp.numLayers,
        selectedLayersCount: comp.selectedLayers.length
    };
    """)
    return evaluate(script)


def get_selected_layers() -> Dict[str, Any]:
    script = wrap_iife("""
    var comp = app.project.activeItem;
    if (!comp || !(comp instanceof CompItem)) {
        return { status: "NO_ACTIVE_COMP" };
    }
    var sel = comp.selectedLayers;
    var list = [];
    for (var i = 0; i < sel.length; i++) {
        var L = sel[i];
        list.push({
            index: L.index,
            name: L.name,
            label: L.label,
            comment: L.comment,
            enabled: L.enabled,
            threeD: L.threeDLayer,
            hasVideo: L.hasVideo
        });
    }
    return { status: "OK", count: list.length, compName: comp.name, layers: list };
    """)
    return evaluate(script)


# ── CLI Interface ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Dimension Direct ExtendScript Terminal & AE Evaluator")
    parser.add_argument("script", nargs="?", help="ExtendScript code expression to evaluate")
    parser.add_argument("-f", "--file", type=str, help="Path to .jsx file to execute")
    parser.add_argument("--active-comp", action="store_true", help="Inspect active comp metadata")
    parser.add_argument("--selected-layers", action="store_true", help="Inspect selected layers")
    parser.add_argument("--scrape", action="store_true", help="Trigger Sovereign_Core scrape")
    parser.add_argument("--json", action="store_true", help="Output purely JSON")
    parser.add_argument("--timeout", type=float, default=10.0, help="Timeout in seconds")

    args = parser.parse_args()

    if args.active_comp:
        res = get_active_comp_info()
        print(json.dumps(res, indent=2))
        sys.exit(0 if res.get("status") == "OK" else 1)

    if args.selected_layers:
        res = get_selected_layers()
        print(json.dumps(res, indent=2))
        sys.exit(0 if res.get("status") == "OK" else 1)

    if args.scrape:
        from bridge.sovereign_bridge import SovereignBridge
        bridge = SovereignBridge(os.getcwd())
        manifest_path = bridge.trigger_scrape(timeout_s=args.timeout)
        print(f"Scrape completed: {manifest_path}")
        sys.exit(0)

    code = args.script
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            code = f.read()

    if not code:
        parser.print_help()
        sys.exit(1)

    res = evaluate(code, timeout=args.timeout)
    if args.json:
        print(json.dumps(res))
    else:
        print(json.dumps(res, indent=2))

    sys.exit(0 if res.get("status") == "OK" else 1)


if __name__ == "__main__":
    main()
