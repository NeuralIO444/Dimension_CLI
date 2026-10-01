# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/logic/manifest_source.py
Resolve the scrape-manifest path the Tagging Manager (and future
panels) should read.

Resolution order (spec-locked, v5.2.4):
  1. Pointer file at
       ~/Library/Application Support/NeuralIO_Dimension/current_manifest.txt
     Contains an absolute path to the most recent manifest. Written
     by `Sovereign_Core.jsx :: writePointerFile` every time a scrape
     lands. Defensive: if the pointer file is present but points at a
     path that doesn't exist (dangling — user deleted the file, or
     points at a previous project), we fall through rather than error.
  2. Repo-root fallback — `<project_root>/scrape_manifest.json`.
     What `qt_controller._refresh_comp_info` has always read.
  3. None — caller renders the "no manifest available" empty state.

The current architecture has `qt_controller.manifest_path` hardcoded
to repo-root, which means the Qt path currently IGNORES the pointer
file even when JSX writes one. That's a separate bug (filed in
BUGS.md as "manifest pointer ignored by qt_controller") — the
Tagging Manager uses this resolver so its behaviour is correct
independent of the controller fix.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.logger import log


_POINTER_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "NeuralIO_Dimension"
    / "current_manifest.txt"
)


# Aggregate-once-per-session for the "pointer dangles" warning. The
# resolver is called from multiple paths every session boot
# (manifest_path property + QFileSystemWatcher init + TaggingPage
# load), and the pointer state is the same at every call. Set is
# keyed by `(pointer, target)` so a pointer flip to a *different*
# stale target re-warns.
_dangling_logged: set = set()


def _read_pointer() -> Optional[Path]:
    """Return the path referenced by the pointer file, or None."""
    if not _POINTER_PATH.exists():
        return None
    try:
        target = _POINTER_PATH.read_text(encoding="utf-8").strip()
    except OSError as e:
        log.warning("manifest pointer unreadable",
                    extra={"pointer": str(_POINTER_PATH), "error": str(e)})
        return None
    if not target:
        return None
    p = Path(target)
    if not p.exists():
        key = (str(_POINTER_PATH), target)
        if key not in _dangling_logged:
            _dangling_logged.add(key)
            log.warning("manifest pointer dangles — falling through",
                        extra={"pointer": str(_POINTER_PATH), "target": target})
        return None
    return p


def resolve_manifest_path(project_root: Optional[Path] = None) -> Optional[Path]:
    """Resolve the best available manifest path.

    `project_root` is the repo root; defaults to the current working
    directory. When given, the repo-root fallback (#2) looks for
    `<project_root>/scrape_manifest.json`.

    Returns None only when neither the pointer file nor the
    repo-root manifest exist. The Tagging Manager renders an empty
    state in that case.
    """
    pointer = _read_pointer()
    if pointer is not None:
        return pointer

    root = Path(project_root) if project_root else Path.cwd()
    fallback = root / "scrape_manifest.json"
    if fallback.exists():
        return fallback

    return None
