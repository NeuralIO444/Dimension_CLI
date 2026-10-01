#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/scripts/derive_smart_match.py — Horizon Color Planner (issue #479).

Companion to derive_lut.py / derive_parametric_match.py, but instead of
the artist manually choosing "always burn a 3D LUT" vs. "always do a
Levels-only fit", this calls core.color.planner.plan() to MEASURE how
close a Levels adjustment gets (against a real ADBE Pro Levels2
simulation, not an idealized one) and only reaches for a full LUT mesh
when Levels genuinely isn't enough.

Naming trap worth knowing before touching this file: plan()'s own
`src_input`/`ref_input` parameters are the OPPOSITE of this script's
(and the two sibling scripts') `ref_image`/`graded_image` CLI argument
names. `ref_image` here is the ungraded AE-rendered frame (plan()'s
`src_input`); `graded_image` here is the colorist's desired look
(plan()'s `ref_input`). Confirmed against core/color/levels_spline.py's
own fit(ref_input, graded_input) contract, which plan() calls as
`levels_spline.fit(src_img, ref_img)` -- i.e. plan()'s `src` becomes
levels_spline's `ref` (the baseline) and plan()'s `ref` becomes
levels_spline's `graded` (the target look).

core/color/ is deliberately pure Python with no file I/O of its own
(test_12_ast_import_and_file_write_guard enforces this) -- writing the
actual .cube file for a LUT_MESH result happens here, outside that
package, using the affine matrix plan() attaches to
ColorRecipe.extra[0]["lut_affine_matrix"] plus
core.color.lut_mesh.generate_cube_text().
"""

import argparse
import json
import sys
from pathlib import Path

# Add repo root to sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np

from core.color.planner import plan
from core.color.recipe import Backend
from core.color import lut_mesh


def _reason_for(recipe) -> str:
    m = recipe.metrics or {}
    if recipe.backend == Backend.LEVELS:
        return (
            f"Levels adjustment matched within tolerance "
            f"(R={m.get('R', 0):.3f}, dE_mean={m.get('dE_mean', 0):.2f}) — no LUT needed."
        )
    if recipe.backend == Backend.LUT_ELIDE:
        return (
            f"A full 3D LUT barely improved on Levels alone "
            f"(dE_mean {m.get('dE_mean', 0):.2f} vs {m.get('dE_mean_lut', 0):.2f}) — using Levels."
        )
    return (
        f"Color shift too complex for Levels alone "
        f"(dE_mean={m.get('dE_mean', 0):.2f}) — applied a full 3D LUT."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure the color shift and automatically pick Levels vs. a full 3D LUT (Horizon Color Planner)."
    )
    parser.add_argument("ref_image", help="Path to reference frame (ungraded, e.g. from AE)")
    parser.add_argument("graded_image", help="Path to colorist graded still (desired look)")
    parser.add_argument("output_cube", help="Path to write a .cube file, used only if a full LUT is needed")
    parser.add_argument("--size", type=int, default=33, help="3D LUT size if a LUT is needed (default: 33)")
    parser.add_argument("--comp-id", default="comp", help="Comp identifier, used only in the recipe's own lut_path label")

    args = parser.parse_args()

    try:
        recipe = plan(args.ref_image, args.graded_image, comp_id=args.comp_id)

        lut_path = None
        if recipe.backend == Backend.LUT_MESH:
            affine_entry = next(
                (e.get("lut_affine_matrix") for e in recipe.extra if "lut_affine_matrix" in e),
                None,
            )
            if affine_entry is None:
                raise RuntimeError("LUT_MESH backend chosen but no affine matrix was attached — this is a planner bug, not a color-match failure.")
            M_affine = np.asarray(affine_entry, dtype=np.float64)
            cube_text = lut_mesh.generate_cube_text(M_affine, size=args.size)
            Path(args.output_cube).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output_cube).write_text(cube_text, encoding="utf-8")
            lut_path = args.output_cube

        print(json.dumps({
            "status": "OK",
            "backend": recipe.backend.value,
            "mode": recipe.mode,
            "levels": recipe.levels if recipe.backend != Backend.LUT_MESH else None,
            "lut_path": lut_path,
            "reason": _reason_for(recipe),
            "metrics": recipe.metrics,
            "signatures": recipe.signatures,
            "warnings": recipe.warnings,
        }))
        return 0
    except Exception as e:
        print(json.dumps({"status": "ERROR", "error": str(e)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
