#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/scripts/generate_js_tag_mirror.py
v6.0 — generates the CEP-side mirror of the canonical tag registry.

Sister to generate_jsx_tag_mirror.py. The CEP panel runs in Chromium
(ES6+), so the panel's HTML/JS layer needs its own copy of the
registry that is kept in sync with config/tag_registry.yaml.

Run:

    python python/scripts/generate_js_tag_mirror.py

Then commit cep/js/tag_registry.js alongside the YAML edit. Tests
in python/tests/test_tag_registry.py enforce that the generated file
matches the current YAML — CI fails on drift, same as the JSX mirror.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_ROOT = _REPO_ROOT / "python"
sys.path.insert(0, str(_PY_ROOT))

from core.tag_registry import REGISTRY  # noqa: E402


_OUTPUT_PATH: Path = _REPO_ROOT / "cep" / "js" / "tag_registry.js"


_HEADER = """\
/*
 * (c) 2026 NeuralIO 444
 * Licensed under NeuralIO Shared Source License (NSSL).
 * See LICENSE for full terms.
 */

/**
 * cep/js/tag_registry.js
 *
 * GENERATED FILE — DO NOT EDIT BY HAND.
 *
 * Source:    config/tag_registry.yaml
 * Regenerate: python python/scripts/generate_js_tag_mirror.py
 *
 * The Python registry (python/core/tag_registry.py) is the source
 * of truth. The CEP panel's Chromium context cannot parse YAML
 * directly without bundling a YAML library, so this mirror is
 * generated and committed alongside YAML edits. Tests at
 * python/tests/test_tag_registry.py enforce the two files stay
 * synchronised — CI will fail if you edit the YAML without
 * regenerating this file.
 *
 * Public globals (attached to window for main.js to consume):
 *
 *   window.DIMENSION_TAG_REGISTRY         — { tagId: {abbrev, name, ae_label_color, default_gravity, color_hex, semantic_class}, ... }
 *   window.DIMENSION_VALID_TAGS           — [tagId, ...]   — canonical ids only
 *   window.DIMENSION_TAG_ALIASES          — { alias: canonicalId, ... }
 *   window.DIMENSION_TAG_REGISTRY_VERSION — schema_version from the YAML
 *   window.normalizeTag(s)                — string → canonical id (or null)
 */

(function () {
"""

_FOOTER = """\

    // Source palette (mirror of python/ui/tag_colors.py::SOURCE_COLOR /
    // SOURCE_LABEL). Keyed by surveyor TagResult.source.
    var ACCENT = "#7eb8d4";
    var TEXT_SECONDARY = "#aaaaaa";
    var TEXT_MUTED = "#555555";
    var TEXT_GHOST = "#333333";

    var SOURCE_COLOR = {
        "manual_user":    ACCENT,
        "manual_comment": ACCENT,
        "manual_name":    ACCENT,
        "manual_label":   ACCENT,
        "profile":        "#b07ed4",
        "heuristic":      TEXT_SECONDARY,
        "structural":     TEXT_MUTED,
        "":               TEXT_GHOST,
        "null":           TEXT_GHOST
    };

    var SOURCE_LABEL = {
        "manual_user":    "MANUAL",
        "manual_comment": "MANUAL",
        "manual_name":    "MANUAL",
        "manual_label":   "MANUAL",
        "profile":        "PROFILE",
        "heuristic":      "HEURISTIC",
        "structural":     "STRUCTURAL",
        "":               "NONE",
        "null":           "NONE"
    };

    var SOURCE_LETTER = {
        "manual_user":    "M",
        "manual_comment": "M",
        "manual_name":    "M",
        "manual_label":   "M",
        "profile":        "P",
        "heuristic":      "H",
        "structural":     "S",
        "":               "·",
        "null":           "·"
    };

    // Gravity HUD (mirror of GRAVITY_ICON / GRAVITY_COLOR).
    var GRAVITY_ICON = {
        "top": "\\u2191", "topC": "\\u2191",
        "bottom": "\\u2193", "bottomC": "\\u2193",
        "center": "\\u25ce", "centerH": "\\u25ce",
        "leftMid": "\\u25c0", "rightMid": "\\u25b6",
        "fill": "\\u229e", "none": "\\u00b7", "": "\\u00b7"
    };

    var GRAVITY_COLOR = {
        "top": ACCENT, "topC": ACCENT,
        "bottom": "#c8820a", "bottomC": "#c8820a",
        "center": "#7ed4b0", "centerH": "#7ed4b0",
        "leftMid": TEXT_SECONDARY, "rightMid": TEXT_SECONDARY,
        "fill": TEXT_MUTED, "none": TEXT_GHOST, "": TEXT_GHOST
    };

    function normalizeTag(s) {
        if (!s) return null;
        var k = String(s);
        if (TAG_REGISTRY.hasOwnProperty(k)) return k;
        if (TAG_ALIASES.hasOwnProperty(k)) return TAG_ALIASES[k];
        return null;
    }

    window.DIMENSION_TAG_REGISTRY         = TAG_REGISTRY;
    window.DIMENSION_VALID_TAGS           = VALID_TAGS;
    window.DIMENSION_TAG_ALIASES          = TAG_ALIASES;
    window.DIMENSION_SOURCE_COLOR         = SOURCE_COLOR;
    window.DIMENSION_SOURCE_LABEL         = SOURCE_LABEL;
    window.DIMENSION_SOURCE_LETTER        = SOURCE_LETTER;
    window.DIMENSION_GRAVITY_ICON         = GRAVITY_ICON;
    window.DIMENSION_GRAVITY_COLOR        = GRAVITY_COLOR;
    window.normalizeTag                   = normalizeTag;
})();
"""


def _emit_registry_object(indent: str = "    ") -> str:
    lines = [f"{indent}var TAG_REGISTRY = {{"]
    for i, t in enumerate(REGISTRY.tags):
        comma = "," if i < len(REGISTRY.tags) - 1 else ""
        lines.append(
            f'{indent}    "{t.id}": {{ '
            f'"abbrev": {json.dumps(t.abbrev)}, '
            f'"name": {json.dumps(t.display_name or t.id)}, '
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


def _emit_schema_version(indent: str = "    ") -> str:
    return (
        f"{indent}window.DIMENSION_TAG_REGISTRY_VERSION = "
        f"{json.dumps(REGISTRY.schema_version)};"
    )


def render() -> str:
    """Return the full JS file content as a string. Pure function —
    used by tests to compare against the on-disk file."""
    body = "\n\n".join([
        _emit_registry_object(),
        _emit_valid_tags(),
        _emit_aliases(),
        _emit_schema_version(),
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
