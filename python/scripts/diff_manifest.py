#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Compare two manifest JSON files and print divergences.

Skips static-value differences when keyframe streams and expressions
are byte-identical (playhead-sampling noise).

Usage:
    python3 python/scripts/diff_manifest.py REF.json NEW.json
    python3 python/scripts/diff_manifest.py REF.json NEW.json --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "python"))

from core.manifest_diff import (  # noqa: E402
    compare_manifest_files,
    material_divergences,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Diff two manifest JSON files")
    ap.add_argument("ref", help="Reference manifest path")
    ap.add_argument("new", help="New manifest path")
    ap.add_argument("--json", action="store_true", help="Emit JSON output")
    ap.add_argument(
        "--include-noise",
        action="store_true",
        help="Include playhead-noise false positives",
    )
    ap.add_argument(
        "--conformed",
        action="store_true",
        help="Compare only conformed_transforms / conformed_keys per layer",
    )
    args = ap.parse_args()

    all_divs = compare_manifest_files(
        args.ref, args.new, conformed_only=args.conformed,
    )
    divs = all_divs if args.include_noise else material_divergences(all_divs)
    skipped = sum(1 for d in all_divs if d.skipped_playhead_noise)

    if args.json:
        payload = {
            "divergence_count": len(divs),
            "skipped_playhead_noise": skipped,
            "divergences": [
                {
                    "path": d.path,
                    "ref": d.ref_value,
                    "new": d.new_value,
                    "layer_name": d.layer_name,
                    "layer_index": d.layer_index,
                }
                for d in divs
            ],
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0 if not divs else 1

    print(f"Divergences: {len(divs)}")
    if skipped:
        print(f"Skipped playhead-noise false positives: {skipped}")
    for d in divs[:50]:
        layer = ""
        if d.layer_name:
            layer = f" ({d.layer_name})"
        print(f"  {d.path}{layer}")
        print(f"    ref: {d.ref_value!r}")
        print(f"    new: {d.new_value!r}")
    if len(divs) > 50:
        print(f"  ... and {len(divs) - 50} more")
    return 0 if not divs else 1


if __name__ == "__main__":
    raise SystemExit(main())