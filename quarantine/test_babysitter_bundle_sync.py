# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""test_babysitter_bundle_sync.py — Track A Stage 3 Option B.

Enforces that the committed runtime artifact `Scripts/Dimension_Assets/Babysitter.jsx`
and its CEP mirror `cep/jsx/Babysitter.jsx` are strictly in sync with the
modular source parts in `Scripts/Dimension_Assets/Babysitter_src/*.jsx`.
"""

from __future__ import annotations

import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_ROOT = _REPO_ROOT / "python"
sys.path.insert(0, str(_PY_ROOT))

from scripts.generate_babysitter import get_part_files, render  # noqa: E402

_BABYSITTER_PATH = _REPO_ROOT / "Scripts" / "Dimension_Assets" / "Babysitter.jsx"
_CEP_BABYSITTER_PATH = _REPO_ROOT / "cep" / "jsx" / "Babysitter.jsx"


class TestBabysitterBundleSync:
    def test_parts_exist(self):
        parts = get_part_files()
        assert len(parts) >= 1, "Babysitter_src must contain at least one .jsx part file"

    def test_babysitter_jsx_matches_rendered_parts(self):
        assert _BABYSITTER_PATH.exists(), f"{_BABYSITTER_PATH} must exist"
        disk_content = _BABYSITTER_PATH.read_text(encoding="utf-8")
        rendered = render()
        assert disk_content == rendered, (
            "Scripts/Dimension_Assets/Babysitter.jsx is out of sync with Babysitter_src/*.jsx.\n"
            "Run: python python/scripts/generate_babysitter.py"
        )

    def test_cep_jsx_babysitter_matches_rendered_parts(self):
        if _CEP_BABYSITTER_PATH.exists():
            disk_content = _CEP_BABYSITTER_PATH.read_text(encoding="utf-8")
            rendered = render()
            assert disk_content == rendered, (
                "cep/jsx/Babysitter.jsx is out of sync with Babysitter_src/*.jsx.\n"
                "Run: python python/scripts/generate_babysitter.py"
            )
