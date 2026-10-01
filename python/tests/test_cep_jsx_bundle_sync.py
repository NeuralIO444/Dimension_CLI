# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_cep_jsx_bundle_sync.py

The CEP panel loads its JSX siblings from `cep/jsx/` (see
`cep/jsx/host.jsx::_resolveAsset` — it checks the bundled copy FIRST
and only falls back to a live dev-repo checkout if the bundled file
is *missing*, never if it's merely stale). `cep/jsx/*.jsx` is
gitignored and is meant to be refreshed by `package.sh`'s bundling
loop from the source of truth, `Scripts/Dimension_Assets/`.

Nothing previously verified that refresh actually happened. Found
2026-07-03 investigating PERF-1 (inject tick overhead): 7 of the 10
bundled files had silently drifted (some since mid-June), so AE was
running weeks-old scrape/tag/inject code regardless of what shipped
in `Scripts/Dimension_Assets/`. This test parses `package.sh`'s own
`JSX_BUNDLED` array (the single source of truth for which files must
be bundled) and asserts every one of them is present and
byte-identical between the two locations, so this class of drift
fails CI/pytest instead of requiring a live AE measurement to notice.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PACKAGE_SH = os.path.join(_REPO_ROOT, "package.sh")
_SOURCE_DIR = os.path.join(_REPO_ROOT, "Scripts", "Dimension_Assets")
_BUNDLE_DIR = os.path.join(_REPO_ROOT, "cep", "jsx")


def _parse_jsx_bundled_list() -> list:
    """Extract the JSX_BUNDLED=(...) array entries from package.sh.

    Deliberately parses the real script rather than hardcoding the
    file list here — a second, independent list would just be a new
    place for drift to hide.
    """
    with open(_PACKAGE_SH, "r", encoding="utf-8") as f:
        content = f.read()
    match = re.search(r'JSX_BUNDLED=\((.*?)\)', content, re.DOTALL)
    assert match, (
        "package.sh's JSX_BUNDLED array not found — has the bundling "
        "step been renamed or restructured? This test needs updating "
        "to match."
    )
    return re.findall(r'"([^"]+)"', match.group(1))


class TestCepJsxBundleSync:
    def test_jsx_bundled_list_is_non_empty(self):
        """Sanity check the parser itself found real entries."""
        files = _parse_jsx_bundled_list()
        assert len(files) >= 5, (
            f"Only found {len(files)} entries in package.sh's "
            "JSX_BUNDLED array — parser may be broken."
        )

    @pytest.mark.parametrize("filename", _parse_jsx_bundled_list())
    def test_bundled_copy_matches_source(self, filename):
        """Every file package.sh bundles into cep/jsx/ must already be
        there and byte-identical to Scripts/Dimension_Assets/ right
        now — not just "correct after the next package.sh run".

        AE loads whatever is currently sitting in cep/jsx/. A stale or
        missing bundled copy is a silent, unmeasurable-by-pytest bug
        that only shows up as a mysterious behavior/performance
        regression in a live AE session.
        """
        src_path = os.path.join(_SOURCE_DIR, filename)
        bundled_path = os.path.join(_BUNDLE_DIR, filename)

        assert os.path.isfile(src_path), (
            f"Source JSX missing: Scripts/Dimension_Assets/{filename} "
            "— package.sh's JSX_BUNDLED array references a file that "
            "doesn't exist."
        )
        assert os.path.isfile(bundled_path), (
            f"cep/jsx/{filename} is missing. Run package.sh's bundling "
            f"step (or manually re-copy from Scripts/Dimension_Assets/) "
            f"before testing in AE — the CEP panel cannot fall back to "
            f"the live source when the bundled copy is absent, it will "
            f"simply fail to load this module."
        )

        with open(src_path, "rb") as f:
            src_bytes = f.read()
        with open(bundled_path, "rb") as f:
            bundled_bytes = f.read()

        assert src_bytes == bundled_bytes, (
            f"cep/jsx/{filename} is STALE — it does not match "
            f"Scripts/Dimension_Assets/{filename}. The CEP panel in AE "
            f"is running the old bundled version, not whatever you just "
            f"edited/committed. Re-run package.sh's bundling step (or "
            f"manually re-copy this file) before testing in AE."
        )
