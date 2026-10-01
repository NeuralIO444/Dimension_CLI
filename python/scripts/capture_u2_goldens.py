#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
capture_u2_goldens.py — U2 Phase 0 golden capture (2026-07-02).

Captures the conform output of the CURRENT code for the two AE-verified
real fixtures (87N fresh session scrape, Free Parallax) across the six
run combinations U2's refactor must not change:

    87N      1080x1920 Fit   layout=auto / layout=tags
    Parallax 2048x858  Fit   layout=auto / layout=tags
    Parallax 2048x1080 Fit   layout=auto / layout=tags

Each run is a full `orchestrator.py` subprocess (same production path
the CEP panel drives), NOT an in-process ScaleEngine call — per the
"never trust synthetic fixtures / partial paths for layout behavior"
anti-pattern. The stored golden is a canonical projection of
`conformed_manifest.json`: per-layer conformed_transforms /
conformed_keys / gravity_group_size / is_root keyed by the comp-scoped
"(containing_comp_id):(index)" identity, plus top-level scale,
aspect_strategy and warnings. Every field the conform WRITES is
covered; source-carryover bytes are the fixture's own and cannot drift.

Run once, check the goldens in, never re-capture unless Matt approves a
deliberate behavior change:

    python3 python/scripts/capture_u2_goldens.py

`python/tests/test_u2_goldens.py` re-runs the same six conforms and
asserts exact equality against these files.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_PY_DIR = Path(__file__).resolve().parents[1]          # .../Dimension/python
_FIXTURES = _PY_DIR / "tests" / "fixtures" / "session_2026_07_02"
GOLDEN_DIR = _PY_DIR / "tests" / "goldens" / "u2"
_ORCHESTRATOR = _PY_DIR / "orchestrator.py"

# (golden_name, fixture_manifest, project_structure_or_None, W, H, layout)
RUNS = [
    ("87n_1080x1920_fit_auto",
     "87n_fresh_manifest.json", None, 1080, 1920, "auto"),
    ("87n_1080x1920_fit_tags",
     "87n_fresh_manifest.json", None, 1080, 1920, "tags"),
    ("parallax_2048x858_fit_auto",
     "parallax_manifest.json", "parallax_project_structure.json",
     2048, 858, "auto"),
    ("parallax_2048x858_fit_tags",
     "parallax_manifest.json", "parallax_project_structure.json",
     2048, 858, "tags"),
    ("parallax_2048x1080_fit_auto",
     "parallax_manifest.json", "parallax_project_structure.json",
     2048, 1080, "auto"),
    ("parallax_2048x1080_fit_tags",
     "parallax_manifest.json", "parallax_project_structure.json",
     2048, 1080, "tags"),
]


def project_conformed(conformed: dict) -> dict:
    """Canonical projection of a conformed manifest.

    Keeps every field the conform WRITES (conformed_transforms,
    conformed_keys, gravity_group_size, is_root) keyed by the
    comp-scoped layer identity, plus the run-level scale /
    aspect_strategy / warnings. Drops source carryover (the fixture's
    own bytes) so the golden stays small and meaningful.
    """
    layers_proj: dict = {}
    for lay in conformed.get("layers", []):
        base = f"{lay.get('containing_comp_id')}:{lay.get('index')}"
        key = base
        n = 1
        while key in layers_proj:  # defensive — comp-scoped ids are unique
            n += 1
            key = f"{base}#{n}"
        ct = lay.get("conformed_transforms")
        layers_proj[key] = {
            "conformed_transforms": ct,
            "conformed_keys": lay.get("conformed_keys"),
            "gravity_group_size": lay.get("gravity_group_size"),
            "is_root": (ct or {}).get("is_root"),
        }
    return {
        "layers": layers_proj,
        "scale": conformed.get("scale"),
        "aspect_strategy": conformed.get("aspect_strategy"),
        "warnings": conformed.get("warnings"),
    }


def run_conform_projection(
    fixture_name: str,
    structure_name,
    width: int,
    height: int,
    layout: str,
    work_dir,
) -> dict:
    """Run one full orchestrator subprocess conform; return the projection.

    Copies the fixture into `work_dir` as scrape_manifest.json (no hash
    sidecar → the legacy AE-panel-OK integrity path) and, when the run
    has a project structure, copies it alongside as
    project_structure.json so comp_dims + the mirror tree load exactly
    as in production.
    """
    work = Path(work_dir)
    src = work / "scrape_manifest.json"
    shutil.copyfile(_FIXTURES / fixture_name, src)
    if structure_name:
        shutil.copyfile(_FIXTURES / structure_name,
                        work / "project_structure.json")
    proc = subprocess.run(
        [sys.executable, str(_ORCHESTRATOR),
         "--source", str(src),
         "--width", str(width), "--height", str(height),
         "--mode", "Fit", "--layout", layout, "--no-report",
         "--output", str(work / "chunks")],
        cwd=str(work), capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"conform failed ({fixture_name} {width}x{height} {layout}): "
            f"{proc.stderr[-2000:]}")
    with open(work / "conformed_manifest.json", encoding="utf-8") as f:
        conformed = json.load(f)
    return project_conformed(conformed)


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json.gz"


def load_golden(name: str) -> dict:
    with gzip.open(golden_path(name), "rt", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, fixture, structure, w, h, layout in RUNS:
        print(f"capturing {name} …", flush=True)
        with tempfile.TemporaryDirectory() as tmp:
            proj = run_conform_projection(fixture, structure, w, h,
                                          layout, tmp)
        canonical = json.dumps(proj, sort_keys=True)
        with gzip.open(golden_path(name), "wt", encoding="utf-8") as f:
            f.write(canonical)
        print(f"  wrote {golden_path(name)} "
              f"({os.path.getsize(golden_path(name))} bytes, "
              f"{len(proj['layers'])} layers)")
    print("done — 6 goldens captured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
