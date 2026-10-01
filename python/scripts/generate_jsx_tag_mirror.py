#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/scripts/generate_jsx_tag_mirror.py
v5.10 — generates the JSX-side mirror of the canonical tag registry.

JSX in After Effects runs on ES3, which has no YAML parser. So the
build/dev workflow has Python read `config/tag_registry.yaml` and
emit a hand-readable JSX object literal at
`Scripts/Dimension_Assets/tag_registry.jsx`.

Run:

    python python/scripts/generate_jsx_tag_mirror.py

Then commit BOTH files together. Tests in
`python/tests/test_tag_registry.py` enforce that the generated file
is in sync with the YAML — CI fails if they drift.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


# Make `core.tag_registry` importable when this script is run as
# `python python/scripts/generate_jsx_tag_mirror.py` from the repo
# root (the conventional invocation).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_ROOT = _REPO_ROOT / "python"
sys.path.insert(0, str(_PY_ROOT))

from core.tag_registry import LEGACY_LABEL_COLOR_MAP, REGISTRY  # noqa: E402


_OUTPUT_PATH: Path = (
    _REPO_ROOT / "Scripts" / "Dimension_Assets" / "tag_registry.jsx"
)


_HEADER = """\
/*
 * (c) 2026 NeuralIO 444
 * Licensed under NeuralIO Shared Source License (NSSL).
 * See LICENSE for full terms.
 */

/**
 * Scripts/Dimension_Assets/tag_registry.jsx
 *
 * GENERATED FILE — DO NOT EDIT BY HAND.
 *
 * Source:    config/tag_registry.yaml
 * Regenerate: python python/scripts/generate_jsx_tag_mirror.py
 *
 * The Python registry (python/core/tag_registry.py) is the source
 * of truth. JSX cannot parse YAML in ES3, so this mirror is
 * generated and committed alongside YAML edits. Tests at
 * python/tests/test_tag_registry.py enforce the two files stay
 * synchronised — CI will fail if you edit the YAML without
 * regenerating this file.
 *
 * Public globals:
 *
 *   $.global.DIMENSION_TAG_REGISTRY        // { tagId: {abbrev, ae_label_color, default_gravity, color_hex, semantic_class}, ... }
 *   $.global.DIMENSION_VALID_TAGS          // [tagId, ...]   — canonical ids only
 *   $.global.DIMENSION_TAG_ALIASES         // { alias: canonicalId, ... }
 *   $.global.DIMENSION_TAG_REGISTRY_VERSION // schema_version string from the YAML
 *   $.global.DIMENSION_LEGACY_LABEL_MAP     // { labelIndex: canonicalId, ... }
 *                                           pre-#117 colours no longer in YAML
 *
 *   normalizeTag(s): given a string, returns the canonical id (resolving
 *                   aliases) or null for unknown tags.
 *   labelForTag(s): AE label index for the tag (0 if unknown).
 */

if (typeof $.global.DIMENSION_TAG_REGISTRY !== "undefined") {
    // Already loaded — no-op (safe re-eval guard).
} else {
"""

_FOOTER = """\

    function normalizeTag(s) {
        if (!s) return null;
        var k = String(s);
        if ($.global.DIMENSION_TAG_REGISTRY.hasOwnProperty(k)) return k;
        if ($.global.DIMENSION_TAG_ALIASES.hasOwnProperty(k))
            return $.global.DIMENSION_TAG_ALIASES[k];
        return null;
    }

    function labelForTag(s) {
        var canon = normalizeTag(s);
        if (!canon) return 0;
        var def = $.global.DIMENSION_TAG_REGISTRY[canon];
        return def ? def.ae_label_color : 0;
    }

    $.global.normalizeTag = normalizeTag;
    $.global.labelForTag  = labelForTag;
}
"""


def _emit_registry_object(indent: str = "    ") -> str:
    """Build the `var TAG_REGISTRY = { ... };` block."""
    lines = [f"{indent}var TAG_REGISTRY = {{"]
    for i, t in enumerate(REGISTRY.tags):
        comma = "," if i < len(REGISTRY.tags) - 1 else ""
        lines.append(
            f'{indent}    "{t.id}": {{ '
            f'"abbrev": {json.dumps(t.abbrev)}, '
            f'"ae_label_color": {t.ae_label_color}, '
            f'"default_gravity": {json.dumps(t.default_gravity)}, '
            f'"color_hex": {json.dumps(t.color_hex)}, '
            f'"semantic_class": {json.dumps(t.semantic_class)} '
            f"}}{comma}"
        )
    lines.append(f"{indent}}};")
    return "\n".join(lines)


def _emit_valid_tags(indent: str = "    ") -> str:
    ids = ", ".join(json.dumps(t.id) for t in REGISTRY.tags)
    return f"{indent}var VALID_TAGS = [{ids}];"


def _emit_aliases(indent: str = "    ") -> str:
    lines = [f"{indent}var TAG_ALIASES = {{"]
    pairs = []
    for t in REGISTRY.tags:
        for a in t.aliases:
            pairs.append((a, t.id))
    for i, (alias, canon) in enumerate(pairs):
        comma = "," if i < len(pairs) - 1 else ""
        lines.append(
            f"{indent}    {json.dumps(alias)}: {json.dumps(canon)}{comma}"
        )
    lines.append(f"{indent}}};")
    return "\n".join(lines)


def _emit_legacy_label_map(indent: str = "    ") -> str:
    lines = [f"{indent}var LEGACY_LABEL_MAP = {{"]
    items = sorted(LEGACY_LABEL_COLOR_MAP.items())
    for i, (label, tag_id) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        lines.append(
            f"{indent}    {label}: {json.dumps(tag_id)}{comma}"
        )
    lines.append(f"{indent}}};")
    return "\n".join(lines)


def _emit_global_assignments(indent: str = "    ") -> str:
    return (
        f"{indent}$.global.DIMENSION_TAG_REGISTRY         = TAG_REGISTRY;\n"
        f"{indent}$.global.DIMENSION_VALID_TAGS           = VALID_TAGS;\n"
        f"{indent}$.global.DIMENSION_TAG_ALIASES          = TAG_ALIASES;\n"
        f"{indent}$.global.DIMENSION_LEGACY_LABEL_MAP     = LEGACY_LABEL_MAP;\n"
        f"{indent}$.global.DIMENSION_TAG_REGISTRY_VERSION = "
        f"{json.dumps(REGISTRY.schema_version)};"
    )


def render() -> str:
    """Return the full JSX file content as a string. Pure function —
    no I/O. Used by tests to compare against the on-disk file."""
    body = "\n\n".join([
        _emit_registry_object(),
        _emit_valid_tags(),
        _emit_aliases(),
        _emit_legacy_label_map(),
        _emit_global_assignments(),
    ])
    return _HEADER + body + _FOOTER


def main() -> int:
    text = render()
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT_PATH.write_text(text, encoding="utf-8")
    sys.stdout.write(
        f"Wrote {_OUTPUT_PATH.relative_to(_REPO_ROOT)} "
        f"({len(REGISTRY.tags)} tags, schema_version "
        f"{REGISTRY.schema_version})\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
