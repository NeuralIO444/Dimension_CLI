# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_repo_hygiene.py — CI guard against personal identifiers in source trees.

Bug AE: fail if disallowed personal strings reappear in production code paths.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_ROOTS = ("python", "cep/js", "Scripts", "tools", "tests")
_SCAN_EXTS = {
    ".py", ".js", ".jsx",
    ".sh", ".applescript", ".html", ".css", ".yaml", ".yml", ".json",
}
_DISALLOWED = ("Matt" + " Ciaglia", "/" + "Users" + "/" + "mattciaglia")
_ALLOWLIST = {
    "BUGS.md",
    "LICENSE",
    "CLAUDE.md",
    "CHANGELOG.md",
    "python/tests/test_repo_hygiene.py",
}


def _iter_source_files():
    # git-tracked files only — not a raw filesystem walk. A local,
    # gitignored runtime artifact (chunk_manifest.json, presets.json,
    # a log file) can legitimately contain a real developer's absolute
    # path; that's a local-machine fact, not something that ships or
    # gets committed. Scanning the filesystem directly made this test
    # flaky per-developer-machine instead of a clean repeatable guard
    # against what actually lands in the repo.
    try:
        result = subprocess.run(
            ["git", "ls-files"] + list(_SCAN_ROOTS),
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git ls-files unavailable — cannot enumerate tracked files")
        return
    for rel in result.stdout.splitlines():
        path = _REPO_ROOT / rel
        if path.suffix not in _SCAN_EXTS or not path.is_file():
            continue
        if path.name in _ALLOWLIST:
            continue
        yield path


@pytest.mark.parametrize("needle", _DISALLOWED)
def test_no_disallowed_personal_identifiers_in_source(needle: str):
    hits: list[str] = []
    for path in _iter_source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if needle in text:
            rel = path.relative_to(_REPO_ROOT)
            hits.append(str(rel))
    assert not hits, (
        f"Found disallowed string {needle!r} in: "
        + ", ".join(sorted(hits)[:20])
        + (" …" if len(hits) > 20 else "")
    )