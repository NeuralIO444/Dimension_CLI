# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Unit-override CLI shim — lets the CEP panel create/dissolve manual
placement units via CLI spawn (child_process.execFile), the same
pattern `BB.runSurvey` uses (survey subcommand), rather than the
`.dimension_inbox` file-drop mechanism.

Why not the inbox: `.dimension_inbox` is polled by JSX
(`Dimension_Launcher.jsx`), which has no notion of "create-unit" /
"dissolve-unit" jobs — those job types were only ever consumed by
`sovereign_bridge`'s socket-mode handlers. A CEP panel not running in
socket mode had no live consumer for an inbox-dropped unit-override
job, which is why "Merge into Unit" and "Dissolve" were dead in the
default (non-socket) CEP flow. This subcommand gives the panel a
consumer that always runs, regardless of socket mode.

Subcommands (collapsed into one invocation):
    unit-override --manifest <path> --create uid1,uid2,...
    unit-override --manifest <path> --dissolve <unit_id>

Reuses `stages.tag.load_unit_overrides` / `save_unit_overrides` so the
sidecar (`unit_overrides.json`) stays byte-identical in shape to what
`sovereign_bridge.handle_create_unit_job` / `handle_dissolve_unit_job`
already write (socket-mode handlers are left untouched — this is a
second, independent writer of the same on-disk contract, same shape).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from stages.tag import load_unit_overrides, save_unit_overrides  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="unit-override")
    parser.add_argument("--manifest", required=True,
                         help="Path to scrape_manifest.json")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--create",
        help="Comma-separated uid list (>=2) to bind into one manual unit")
    group.add_argument(
        "--dissolve",
        help="Unit id to dissolve back to singletons")
    return parser


def _emit(payload: dict) -> None:
    print(json.dumps(payload))


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        overrides = load_unit_overrides(args.manifest) or {}
        if "unit_overrides" not in overrides:
            overrides["unit_overrides"] = {}

        if args.create is not None:
            uids = [u.strip() for u in args.create.split(",") if u.strip()]
            if len(uids) < 2:
                raise ValueError(
                    "--create requires at least 2 comma-separated uids "
                    f"(got {len(uids)})")
            unit_id = f"manual_{uuid.uuid4().hex[:8]}"
            overrides["unit_overrides"][unit_id] = {
                "action": "create",
                "members": uids,
            }
            save_unit_overrides(args.manifest, overrides)
            _emit({"status": "OK", "unit_id": unit_id})
        else:
            unit_id = args.dissolve
            overrides["unit_overrides"][unit_id] = {"action": "dissolve"}
            save_unit_overrides(args.manifest, overrides)
            _emit({"status": "OK", "unit_id": unit_id})
    except Exception as e:  # noqa: BLE001 — CLI boundary, report don't crash
        _emit({"status": "ERROR", "error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
