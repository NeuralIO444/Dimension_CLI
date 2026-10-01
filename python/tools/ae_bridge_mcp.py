#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/ae_bridge_mcp.py
Model Context Protocol (MCP) Server for Adobe After Effects.

Exposes native tool endpoints over stdio JSON-RPC to inspect, query, and
evaluate ExtendScript in live After Effects sessions.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

# Ensure python/ is on sys.path
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.ae_eval import (
    evaluate,
    get_active_comp_info,
    get_selected_layers,
    wrap_iife,
)


def handle_call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an MCP tool call."""
    if name == "ae_eval_script":
        script = arguments.get("script", "")
        timeout = float(arguments.get("timeout", 10.0))
        res = evaluate(script, timeout=timeout)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(res, indent=2),
                }
            ],
            "isError": res.get("status") != "OK",
        }

    elif name == "ae_get_active_comp":
        res = get_active_comp_info()
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(res, indent=2),
                }
            ],
            "isError": res.get("status") != "OK",
        }

    elif name == "ae_get_selected_layers":
        res = get_selected_layers()
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(res, indent=2),
                }
            ],
            "isError": res.get("status") != "OK",
        }

    elif name == "ae_get_layer_properties":
        idx = int(arguments.get("layer_index", 1))
        script = wrap_iife(f"""
        var comp = app.project.activeItem;
        if (!comp || !(comp instanceof CompItem)) {{
            return {{ status: "NO_ACTIVE_COMP" }};
        }}
        if ({idx} < 1 || {idx} > comp.numLayers) {{
            return {{ status: "LAYER_OUT_OF_BOUNDS", layerIndex: {idx}, maxLayers: comp.numLayers }};
        }}
        var L = comp.layer({idx});
        var props = {{
            index: L.index,
            name: L.name,
            matchName: L.matchName,
            comment: L.comment,
            label: L.label,
            position: L.transform.position ? L.transform.position.value : null,
            scale: L.transform.scale ? L.transform.scale.value : null,
            rotation: L.transform.rotation ? L.transform.rotation.value : null,
            opacity: L.transform.opacity ? L.transform.opacity.value : null,
            anchorPoint: L.transform.anchorPoint ? L.transform.anchorPoint.value : null,
            sourceRect: (typeof L.sourceRectAtTime === "function") ? (function() {{
                var r = L.sourceRectAtTime(comp.time, false);
                return [r.left, r.top, r.width, r.height];
            }})() : null,
            effects: []
        }};
        var fxGroup = L.property("ADBE Effect Parade");
        if (fxGroup) {{
            for (var f = 1; f <= fxGroup.numProperties; f++) {{
                var fx = fxGroup.property(f);
                props.effects.push({{
                    index: f,
                    name: fx.name,
                    matchName: fx.matchName,
                    enabled: fx.enabled
                }});
            }}
        }}
        return {{ status: "OK", layer: props }};
        """)
        res = evaluate(script)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(res, indent=2),
                }
            ],
            "isError": res.get("status") != "OK",
        }

    elif name == "ae_scrape_active_comp":
        script = wrap_iife("""
        var D = $.global["com.neuralio444.dimension"];
        if (D && D.core && D.core.atoms && D.core.atoms.scrapeUnified) {
            return JSON.parse(D.core.atoms.scrapeUnified("standard"));
        }
        return { status: "ERROR", error: "Sovereign_Core scraper module not loaded" };
        """)
        res = evaluate(script)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(res, indent=2),
                }
            ],
            "isError": res.get("status") != "OK",
        }

    else:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Unknown tool: {name}",
                }
            ],
            "isError": True,
        }


def get_tools_list() -> List[Dict[str, Any]]:
    """Return tool schemas."""
    return [
        {
            "name": "ae_eval_script",
            "description": "Evaluates arbitrary ExtendScript (JSX) in Adobe After Effects and returns result or errors.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "The ExtendScript (JSX) code string to evaluate."
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Evaluation timeout in seconds (default 10.0)."
                    }
                },
                "required": ["script"]
            }
        },
        {
            "name": "ae_get_active_comp",
            "description": "Returns metadata (name, id, width, height, fps, duration, numLayers) for the active composition in After Effects.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "ae_get_selected_layers",
            "description": "Returns currently selected layers in the active comp with indices, names, comments, and labels.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        },
        {
            "name": "ae_get_layer_properties",
            "description": "Returns transform coordinates, bounding box sourceRect, and applied effects list for a specific layer index in the active comp.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "layer_index": {
                        "type": "integer",
                        "description": "1-based layer index in active comp."
                    }
                },
                "required": ["layer_index"]
            }
        },
        {
            "name": "ae_scrape_active_comp",
            "description": "Executes Sovereign_Core.jsx to produce a complete ScrapeManifest JSON for the active composition in After Effects.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        }
    ]


def run_stdio_server():
    """Main JSON-RPC stdio loop."""
    # Ensure unbuffered line-oriented stdout
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {},
                        "resources": {},
                        "prompts": {}
                    },
                    "serverInfo": {
                        "name": "ae-bridge",
                        "version": "1.0.0"
                    }
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method in ("notifications/initialized", "initialized"):
            pass

        elif method == "tools/list":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": get_tools_list()
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            call_res = handle_call_tool(tool_name, tool_args)
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": call_res
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "resources/list":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resources": []
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "prompts/list":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "prompts": []
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "resources/templates/list":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resourceTemplates": []
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "ping":
            res = {"jsonrpc": "2.0", "id": req_id, "result": {}}
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif req_id is not None:
            # Respond to any unknown request so the client never hangs
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32601,
                    "message": f"Method '{method}' not found"
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio_server()
