#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""python/scripts/generate_babysitter.py

Track A Stage 3 Option B (Build-time concatenation generator).

Reads modular ExtendScript source parts from:
    Scripts/Dimension_Assets/Babysitter_src/*.jsx

Concatenates them in sorted order and emits the single committed runtime monolith:
    Scripts/Dimension_Assets/Babysitter.jsx
    (and syncs to cep/jsx/Babysitter.jsx)

Usage:
    python python/scripts/generate_babysitter.py          # Regenerates on disk
    python python/scripts/generate_babysitter.py --check  # CI verification
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PARTS_DIR = _REPO_ROOT / "Scripts" / "Dimension_Assets" / "Babysitter_src"
_OUTPUT_PATH = _REPO_ROOT / "Scripts" / "Dimension_Assets" / "Babysitter.jsx"
_CEP_JSX_PATH = _REPO_ROOT / "cep" / "jsx" / "Babysitter.jsx"


def get_part_files() -> list[Path]:
    """Return list of .jsx part files sorted alphabetically by filename."""
    if not _PARTS_DIR.is_dir():
        return []
    return sorted(
        [p for p in _PARTS_DIR.glob("*.jsx") if p.is_file()],
        key=lambda p: p.name,
    )


def render() -> str:
    """Concatenate all part files in deterministic order."""
    parts = get_part_files()
    if not parts:
        raise RuntimeError(f"No .jsx part files found in {_PARTS_DIR}")

    chunks = []
    for part in parts:
        content = part.read_text(encoding="utf-8")
        chunks.append(content)

    return "".join(chunks)


def main() -> int:
    check_mode = "--check" in sys.argv

    try:
        rendered = render()
    except Exception as err:
        print(f"Error during Babysitter render: {err}", file=sys.stderr)
        return 1

    if check_mode:
        if not _OUTPUT_PATH.exists():
            print(f"ERROR: {_OUTPUT_PATH} does not exist. Run: python python/scripts/generate_babysitter.py", file=sys.stderr)
            return 1
        current = _OUTPUT_PATH.read_text(encoding="utf-8")
        if current != rendered:
            print(
                "ERROR: Scripts/Dimension_Assets/Babysitter.jsx is out of sync with Babysitter_src/*.jsx.\n"
                "Run: python python/scripts/generate_babysitter.py",
                file=sys.stderr,
            )
            return 1
        print("OK: Babysitter.jsx is in sync with Babysitter_src/*.jsx")
        return 0

    _OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    part_count = len(get_part_files())
    print(f"Generated {_OUTPUT_PATH} from {part_count} source part(s).")

    if _CEP_JSX_PATH.parent.is_dir():
        _CEP_JSX_PATH.write_text(rendered, encoding="utf-8")
        print(f"Synced {_CEP_JSX_PATH}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
