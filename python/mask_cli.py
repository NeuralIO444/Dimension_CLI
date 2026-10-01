"""Safe-zone mask overlay CLI shim (#400) — lets the CEP panel drive
bridge.SovereignBridge.toggle_safe_zone_mask(), which was fully built
(the file-bridge/socket job, the JSX importSafeZoneMask/removeSafeZoneMask
pair in Babysitter) but had no CLI or UI caller anywhere in the product.

Usage:
    mask_cli.py show <preset_id>   → import config/safe_zones/<preset_id>.png
                                      as the debug overlay in the active comp
    mask_cli.py hide               → remove the debug overlay

Follows prefs_cli.py's shape: stdlib argv parsing, one JSON object on
stdout, exit 0 on success / 1 on failure — mirroring what
cep/js/backend_bridge.js's execFile callers already expect from every
other dimension_engine subcommand.
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from bridge.sovereign_bridge import SovereignBridge, ScrapeEngineError


def _safe_zone_mask_path(project_root: str, preset_id: str) -> str:
    return os.path.join(project_root, "config", "safe_zones", f"{preset_id}.png")


def main():
    if len(sys.argv) < 2:
        print('Usage: mask_cli.py {show <preset_id>|hide}', file=sys.stderr)
        sys.exit(1)

    op = sys.argv[1]
    project_root = os.path.abspath(os.getcwd())
    bridge = SovereignBridge(project_root=project_root)

    if op == 'show':
        if len(sys.argv) < 3:
            print('Usage: mask_cli.py show <preset_id>', file=sys.stderr)
            sys.exit(1)
        preset_id = sys.argv[2]
        mask_path = _safe_zone_mask_path(project_root, preset_id)
        if not os.path.isfile(mask_path):
            print(json.dumps({
                "status": "ERROR",
                "error": f"MASK_MISSING — no safe-zone mask for preset '{preset_id}'"
            }))
            sys.exit(1)
        try:
            result = bridge.toggle_safe_zone_mask(action="import", mask_path=mask_path)
        except ScrapeEngineError as e:
            print(json.dumps({"status": "ERROR", "error": str(e)}))
            sys.exit(1)
        print(json.dumps(result))
        return

    if op == 'hide':
        try:
            result = bridge.toggle_safe_zone_mask(action="remove")
        except ScrapeEngineError as e:
            print(json.dumps({"status": "ERROR", "error": str(e)}))
            sys.exit(1)
        print(json.dumps(result))
        return

    print(f'Unknown op: {op}', file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
