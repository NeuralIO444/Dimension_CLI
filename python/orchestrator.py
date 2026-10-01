# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.
"""
python/orchestrator.py
Thin CLI dispatcher for the Dimension conform pipeline.

Stage implementations live in `python/stages/`:
  - survey.py     — classify layers on a scraped manifest
  - conform_io.py — manifest load/save, target resolution
  - tag.py        — unit override I/O
  - conform.py    — scale → lerp → SOE → slice → report
  - duplication.py — shared-precomp fork planning

Usage:
    python3 orchestrator.py --source scrape_manifest.json --preset youtube

Exit codes mirror `stages.conform_io.ConformError.exit_code`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from stages.conform import (  # noqa: E402
    build_layer_payloads,
    emit_run_warning,
    run_conform,
)
from stages.conform_io import ConformConfig, ConformError, SourceNotFoundError  # noqa: E402

# Back-compat re-exports — tests import these from orchestrator.
_build_layer_payloads = build_layer_payloads
_emit_run_warning = emit_run_warning


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dimension",
        description="Dimension Engine — headless conform pipeline",
    )
    p.add_argument("--source", required=True, help="Path to scrape_manifest.json")
    p.add_argument("--preset", default=None, help="Preset ID (tiktok|youtube|ooh|social)")
    p.add_argument("--profile", default=None, help="Studio Profile ID (e.g. default, social)")
    p.add_argument("--width", type=int, help="Target width (overrides preset)")
    p.add_argument("--height", type=int, help="Target height (overrides preset)")
    p.add_argument("--duration", type=float, help="Target duration seconds (arbitrary/Stage 4)")
    p.add_argument("--fps", type=float, help="Target fps (arbitrary/Stage 4)")
    p.add_argument("--mode", default="Auto", choices=["Auto", "Fit", "Fill", "Stretch", "auto", "fit", "fill", "stretch"])
    p.add_argument(
        "--layout",
        default="tags",
        choices=["tags", "scene", "auto"],
        help="Layer layout strategy",
    )
    p.add_argument("--bleed", type=float, default=0.0, help="Bleed %% 0–25")
    p.add_argument("--output", default="Chunks", help="Output directory")
    p.add_argument("--no-report", action="store_true", help="Skip HTML report")
    p.add_argument(
        "--allow-state-hash-bypass",
        action="store_true",
        help="Allow inject when comp structure drifted since conform",
    )
    return p.parse_args()


def config_from_args(args: argparse.Namespace) -> ConformConfig:
    return ConformConfig(
        source=args.source,
        preset=args.preset,
        profile=args.profile,
        width=args.width,
        height=args.height,
        duration=args.duration,
        fps=args.fps,
        mode=args.mode,
        layout=args.layout,
        bleed=args.bleed,
        output=args.output,
        no_report=args.no_report,
        allow_state_hash_bypass=args.allow_state_hash_bypass,
    )


def main() -> None:
    args = parse_args()
    try:
        run_conform(config_from_args(args))
    except SourceNotFoundError as e:
        print(json.dumps({"type": "error", "msg": str(e)}), flush=True)
        sys.exit(e.exit_code)
    except ConformError as e:
        sys.exit(e.exit_code)


if __name__ == "__main__":
    main()