#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/scripts/derive_lut.py — Derive 3D .cube LUT from reference frame and colorist graded still.
"""

import argparse
import json
import sys
from pathlib import Path

# Add repo root to sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.horizon_color import HorizonColorGateway


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive 3D .cube LUT from reference and graded stills.")
    parser.add_argument("ref_image", help="Path to reference frame (e.g. from AE)")
    parser.add_argument("graded_image", help="Path to colorist graded still (Flame/Resolve)")
    parser.add_argument("output_cube", help="Path to output .cube file")
    parser.add_argument("--size", type=int, default=33, help="3D LUT size (default: 33)")
    parser.add_argument("--convert-png", help="Optional path to convert input still to web-friendly PNG")

    args = parser.parse_args()

    try:
        if args.convert_png:
            HorizonColorGateway.convert_image_to_png(args.graded_image, args.convert_png)

        res = HorizonColorGateway.derive_3d_lut_from_images(
            ref_path=args.ref_image,
            graded_path=args.graded_image,
            output_cube_path=args.output_cube,
            size=args.size,
        )
        print(json.dumps({
            "status": "OK",
            "lut_path": args.output_cube,
            "lut_size": res.lut_size,
            "preview_png": args.convert_png or None
        }))
        return 0
    except Exception as e:
        print(json.dumps({"status": "ERROR", "error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
