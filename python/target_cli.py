"""Target-store CLI shim — lets the CEP panel persist user-created
custom targets (the "Save as Preset" affordance on the configure
screen).

Wraps TargetStoreManager.add_custom_target() so the panel doesn't
have to know the YAML schema or file path.

Usage:
    target_cli.py add --label "My Format" --width 1080 --height 1080
                      [--subcategory user]

Output:
    JSON object: {"id": "custom:my_format", "label": "...",
                  "width": 1080, "height": 1080,
                  "subcategory": "user"}
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from logic.target_store import TargetStoreManager


def main():
    parser = argparse.ArgumentParser(prog="target")
    sub = parser.add_subparsers(dest="op", required=True)

    add = sub.add_parser("add", help="Add a user custom target")
    add.add_argument("--label", required=True)
    add.add_argument("--width", type=int, required=True)
    add.add_argument("--height", type=int, required=True)
    add.add_argument("--duration", type=float, default=None)
    add.add_argument("--fps", type=float, default=None)
    add.add_argument("--subcategory", default="user",
                     help='"user" (default) or "dooh"')
    add.add_argument("--output-name-template", default=None,
                     help='Output comp naming template, e.g. "{source}_{preset}" '
                          '(default: Target schema default). Track B / B2.')

    # QA sweep (2026-09-03) -- add_custom() had no CLI counterpart to
    # remove a custom target, matching the same "create exists, delete
    # doesn't" gap found in profile_cli.py (see issue #401).
    # TargetStoreManager.remove_custom() was fully implemented (correctly
    # cleans up favorites/last_used references too) but unreachable from
    # anywhere.
    rm = sub.add_parser("remove", help="Remove a user custom target by id")
    rm.add_argument("--id", required=True, help='Target id, e.g. "custom:my_format"')

    args = parser.parse_args()

    if args.op == "add":
        tsm = TargetStoreManager()
        target = tsm.add_custom(
            label=args.label,
            width=args.width,
            height=args.height,
            subcategory=args.subcategory,
            duration=getattr(args, 'duration', None),
            fps=getattr(args, 'fps', None),
            output_name_template=getattr(args, 'output_name_template', None),
        )
        out = {
            "id":          target.id,
            "label":       target.label,
            "width":       target.width,
            "height":      target.height,
            "duration":    target.duration,
            "fps":         target.fps,
            "subcategory": target.subcategory,
            "aspect_label": target.aspect_label,
            "output_name_template": target.output_name_template,
        }
        print(json.dumps(out))
        return

    if args.op == "remove":
        tsm = TargetStoreManager()
        removed = tsm.remove_custom(args.id)
        print(json.dumps({"id": args.id, "removed": removed}))
        return


if __name__ == "__main__":
    main()
