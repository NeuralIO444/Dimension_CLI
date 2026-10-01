#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/scripts/derive_parametric_match.py — Derive a per-channel
Slope/Offset color match (no LUT file) from a reference frame and a
colorist graded still.

Companion to derive_lut.py's 3D LUT synthesis: this path never writes
a .cube/.3dl file. The (slope, offset) pairs are applied directly onto
AE's native Levels (Individual Controls) effect by
injectParametricColorMatch() in cep/jsx/host.jsx.
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
    parser = argparse.ArgumentParser(
        description="Derive a per-channel Slope/Offset color match (no LUT file)."
    )
    parser.add_argument("ref_image", help="Path to reference frame (e.g. from AE)")
    parser.add_argument("graded_image", help="Path to colorist graded still (Flame/Resolve)")

    args = parser.parse_args()

    try:
        res = HorizonColorGateway.derive_parametric_match_from_images(
            ref_path=args.ref_image,
            graded_path=args.graded_image,
        )
        print(json.dumps({
            "status": "OK",
            "red": res.red,
            "green": res.green,
            "blue": res.blue,
        }))
        return 0
    except Exception as e:
        print(json.dumps({"status": "ERROR", "error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
