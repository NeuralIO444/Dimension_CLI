# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/jsx_reachability.py — dead-code detector for ExtendScript (issue #419).

WHY THIS TOOL EXISTS
--------------------
`tools/reachability.py` analyzes Python only. Nothing anywhere looks at
ExtendScript. JSX can therefore be fully built, eyeballed once, and sit
dead indefinitely with zero signal — which is exactly what happened:

  - `Babysitter.jsx`'s effect-property writer, dead since its Python
    caller (`effect_conformer.py`) was deleted 2026-08-19 (#391).
  - `Babysitter_src/95_render_queue.jsx::configureRenderQueueItem`, fully
    built, never called from anywhere but a bundle-presence test (#417).

Both were found by hand, in separate sessions, months apart. Neither was
caught by any gate. Three of the five "claimed but never built" findings
in the 2026-09-03 claim audit were JSX-side, for precisely this reason:
Python has a gate and doesn't rot; JSX has none and does.

THE CROSS-LANGUAGE RULE (why a JSX-only parse would be useless)
---------------------------------------------------------------
A JSX function here is frequently invoked by a STRING from another
language, never by JSX-side call syntax:

  - Python drops a job file whose `type` the JSX poller dispatches on.
  - CEP JS calls `csInterface.evalScript("Babysitter.foo()")`.
  - `Dimension_Launcher.jsx` dispatches handlers by name.

So a naive JSX-only scan would report as dead nearly everything the
bridge actually invokes. Call sites are therefore collected from JSX,
Python, and CEP JS alike.

JSX and JS are scanned as text (no ES3 parser in the stdlib). Python is
parsed with `ast` so that DOCSTRINGS and comments — prose discussing a
name rather than calling it — cannot masquerade as call sites.

DELIBERATE CONSERVATISM
-----------------------
Same asymmetry as `tools/field_reachability.py`, for the same reason:
false NEGATIVES (dead code reported live) are acceptable; false POSITIVES
(live code reported dead) are not. Someone acting on a false positive
would delete a JSX function the bridge invokes by string — an outage that
no Python test would catch, in the exact code path where this repo has
already been burned.

Consequently: name matching is global and unqualified. If any file
anywhere mentions `scrapeLayer`, every `scrapeLayer` definition counts as
reached. This tool finds the obviously-dead, not the subtly-dead.

ES3 CAVEAT
----------
There is no ExtendScript parser in the stdlib, so this is regex-based
rather than AST-based. It recognizes the three definition styles this
codebase actually uses (dotted assignment, object-literal method, plain
declaration) and will silently miss anything exotic. That is a known
limitation, not an oversight — and it is why this ships report-only.

USAGE
-----
    python -m tools.jsx_reachability          # human-readable
    python -m tools.jsx_reachability --json   # machine-readable
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

REPO_ROOT = Path(__file__).resolve().parents[2]
PY_ROOT = REPO_ROOT / "python"

# JSX we analyze for dead definitions.
JSX_SOURCE_ROOTS = (
    REPO_ROOT / "Scripts" / "Dimension_Assets",
)
# `cep/jsx/` is a generated bundle (see CLAUDE.md — build artifact, not
# source). Analyzing it would double-count every definition.
EXCLUDED_DIR_PARTS = frozenset({"cep"})

# Where a call site may live.
#
# `Scripts/` (not `Scripts/Dimension_Assets/`) is the root on purpose:
# `Dimension_Launcher.jsx` — the panel's main dispatcher, which invokes
# most of the Dimension_Assets modules — sits at `Scripts/` top level, one
# directory ABOVE the code it calls. Scoping callers to Dimension_Assets/
# missed it entirely and produced false "dead" reports for `runFull`,
# `exportFrame` and others on the first run of this tool. Caught during
# development, but it is exactly the false-positive class the module
# docstring calls unacceptable, so the root stays wide deliberately.
#
# `cep/tests/` and `tests/jsx/` are included for the same reason: a
# function whose only caller is a test is not *reached in production*, but
# reporting it as having no caller at all would be wrong. This tool
# answers "does anything call this," not "is this production-reachable" —
# the Python gate answers the latter, and conflating them here would
# overstate what a regex can actually prove.
#
# `cep/jsx/` is excluded from JSX_SOURCE_ROOTS (definitions) because it is
# a generated bundle for the ~13 files package.sh copies verbatim from
# Scripts/Dimension_Assets/ — scanning those for definitions would double
# count. But `host.jsx` lives ONLY under cep/jsx/ (no
# Scripts/Dimension_Assets/host.jsx exists; confirmed via
# tools/bundle_jsx.sh --list, which does not name it) — it is genuine,
# hand-authored CEP source, not a copy of anything. Excluding cep/jsx/
# from CALLER_ROOTS therefore made every call host.jsx makes into
# Scripts/Dimension_Assets invisible. Found via issue #478: host.jsx's
# real call to `B.hasSafeZoneMask()` was reported as a false "no caller"
# dead symbol. Caller-scanning does not collect definitions, so including
# cep/jsx/ here does not reintroduce the double-counting problem above —
# at worst it adds a redundant (but harmless, since callers are a set)
# site for the ~13 files that ARE copies.
CALLER_ROOTS = (
    REPO_ROOT / "Scripts",
    REPO_ROOT / "cep" / "js",
    REPO_ROOT / "cep" / "jsx",
    REPO_ROOT / "cep" / "tests",
    REPO_ROOT / "tests",
    REPO_ROOT / "tools",
    PY_ROOT,
)
CALLER_SUFFIXES = (".jsx", ".js", ".py")

# Python files are parsed with AST rather than scanned as text, and their
# DOCSTRINGS are excluded, because prose that merely discusses a JSX
# function name is not a call to it.
#
# This is not hypothetical tidiness — it bit twice during development.
# Writing a test documenting `runFull` as dead made `runFull` appear
# reached on the next run; then a second tool's docstring citing the same
# finding did it again. A file-level exclusion list was the first fix and
# was wrong: it cannot scale to every file that ever mentions a JSX name,
# and each miss silently converts a real finding into a false negative.
# Excluding docstrings structurally fixes the whole class at once.
#
# Comments are excluded for free: `ast` discards them entirely.
#
# One case the docstring fix cannot reach: this tool's OWN test asserts on
# names as string data (`assert "runFull" in dead`). That is a real string
# constant in real code, so AST correctly sees it — but a test asserting on
# the analyzer's output is definitionally not a caller of the thing under
# test. It is excluded by path. The two mechanisms are complementary, not
# redundant: AST handles prose anywhere in the repo, this handles the one
# file whose subject matter IS the name list.
# The same reasoning covers `ae_smoke.py`: it PROBES for symbols
# (`!!($.global.Auditor.runFull)`) to report whether AE loaded them. A
# diagnostic asking "does this exist" is not a caller — treating it as one
# would make every symbol the harness watches look reached, destroying both
# tools' signal at once.
#
# `test_jsx_reachability_gate.py` (#478) is the same trap in a new shape:
# its ALLOWLIST dict keys are string literals naming the exact symbols
# this tool reports dead. Without this exclusion, the gate's own ledger
# would make every allowlisted symbol look reached, permanently masking
# it from the ledger's own staleness check (item 3 in that test).
SELF_EXCLUDE: frozenset[str] = frozenset({
    "python/tests/test_jsx_reachability.py",
    "python/tests/test_jsx_reachability_gate.py",
    "python/tools/ae_smoke.py",
    "python/tests/test_ae_smoke.py",
})

# ── Definition patterns, in the three styles this codebase uses ──────────
#   1. DIMENSION.core.atoms.scrapeV5 = function(comp) {
_DEF_DOTTED = re.compile(
    r"^\s*(?:var\s+)?(?:[A-Za-z_$][\w$]*\.)+([A-Za-z_$][\w$]*)\s*=\s*function\s*\(",
    re.MULTILINE,
)
#   2.     _processLayerTransforms: function(cLayer, aeLayer) {
_DEF_LITERAL = re.compile(
    r"^\s*([A-Za-z_$][\w$]*)\s*:\s*function\s*\(",
    re.MULTILINE,
)
#   3. function scrapeLayer(layer) {
_DEF_PLAIN = re.compile(
    r"^\s*function\s+([A-Za-z_$][\w$]*)\s*\(",
    re.MULTILINE,
)

# Anonymous callbacks and ES3 boilerplate that are never "dead".
IGNORED_NAMES: frozenset[str] = frozenset({
    "function", "if", "for", "while", "return", "catch", "toString",
    "valueOf", "constructor", "callee", "apply", "call",
})


def _iter_files(roots, suffixes) -> List[Path]:
    out: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix not in suffixes:
                continue
            if "__pycache__" in p.parts or "node_modules" in p.parts:
                continue
            out.append(p)
    return out


def collect_definitions() -> Dict[str, List[Dict[str, Any]]]:
    """name -> [{file, line, style}]"""
    defs: Dict[str, List[Dict[str, Any]]] = {}
    for path in _iter_files(JSX_SOURCE_ROOTS, (".jsx",)):
        if EXCLUDED_DIR_PARTS & set(path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        lines = text.splitlines()

        def _line_of(idx: int) -> int:
            return text.count("\n", 0, idx) + 1

        for pattern, style in (
            (_DEF_DOTTED, "dotted"),
            (_DEF_LITERAL, "literal"),
            (_DEF_PLAIN, "plain"),
        ):
            for m in pattern.finditer(text):
                name = m.group(1)
                if name in IGNORED_NAMES:
                    continue
                defs.setdefault(name, []).append({
                    "file": rel,
                    "line": _line_of(m.start()),
                    "style": style,
                })
        del lines
    return defs


def _docstring_nodes(tree: ast.AST) -> Set[int]:
    """id() of every Constant node that is a docstring, not real data."""
    out: Set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            out.add(id(first.value))
    return out


def _python_mentions(text: str, names: Set[str]) -> Set[str]:
    """JSX names genuinely referenced by Python code.

    Counts string literals (the bridge dispatches JSX by name, so a literal
    IS a call site) and attribute access. Excludes docstrings and, by way
    of using `ast` at all, every comment — prose about a function is not a
    call to it.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()

    skip = _docstring_nodes(tree)
    found: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in skip:
                continue
            for token in re.findall(r"[A-Za-z_$][\w$]*", node.value):
                if token in names:
                    found.add(token)
        elif isinstance(node, ast.Attribute):
            if node.attr in names:
                found.add(node.attr)
        elif isinstance(node, ast.Name):
            if node.id in names:
                found.add(node.id)
    return found


def collect_call_sites(definition_names: Set[str]) -> Dict[str, Set[str]]:
    """name -> set of files mentioning it, EXCLUDING its own definition lines.

    A "mention" is deliberately loose: `foo(`, `.foo`, `"foo"`, `'foo'`.
    Python and CEP JS count, because the bridge dispatches JSX by string.
    """
    hits: Dict[str, Set[str]] = {n: set() for n in definition_names}
    if not definition_names:
        return hits

    # One alternation for all names — far faster than N passes over M files.
    alternation = "|".join(sorted(map(re.escape, definition_names), key=len, reverse=True))
    mention_re = re.compile(r"(?<![\w$])(" + alternation + r")(?![\w$])")

    for path in _iter_files(CALLER_ROOTS, CALLER_SUFFIXES):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        if rel in SELF_EXCLUDE:
            continue

        if path.suffix == ".py":
            for name in _python_mentions(text, definition_names):
                hits[name].add(rel)
            continue

        for line_no, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            # Skip the definition line itself — declaring is not calling.
            if _DEF_DOTTED.match(line) or _DEF_LITERAL.match(line) or _DEF_PLAIN.match(line):
                continue
            # Skip pure comment lines.
            if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("#"):
                continue
            for m in mention_re.finditer(line):
                hits[m.group(1)].add(rel)
    return hits


def analyze() -> Dict[str, Any]:
    defs = collect_definitions()
    hits = collect_call_sites(set(defs.keys()))

    dead: List[Dict[str, Any]] = []
    live_count = 0
    for name, sites in sorted(defs.items()):
        callers = hits.get(name, set())
        # A file mentioning a name it also defines is not proof of a call,
        # but we already skipped definition lines, so an in-file mention
        # here is a genuine internal call.
        if callers:
            live_count += 1
            continue
        dead.append({
            "name": name,
            "definitions": sites,
        })

    return {
        "jsx_files_scanned": len(_iter_files(JSX_SOURCE_ROOTS, (".jsx",))),
        "definitions_found": len(defs),
        "reached": live_count,
        "unreached": len(dead),
        "dead_definitions": dead,
    }


def _render_human(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("Dimension JSX reachability report (issue #419)")
    lines.append("=" * 60)
    lines.append(f"  jsx files scanned   : {report['jsx_files_scanned']:>5}")
    lines.append(f"  definitions found   : {report['definitions_found']:>5}")
    lines.append(f"  reached             : {report['reached']:>5}")
    lines.append(f"  no caller found     : {report['unreached']:>5}")
    lines.append("")

    dead = report["dead_definitions"]
    if not dead:
        lines.append("Every JSX definition has at least one caller.")
    else:
        lines.append(f"JSX definitions with no caller in JSX, Python or CEP JS ({len(dead)}):")
        lines.append("")
        for item in dead:
            first = item["definitions"][0]
            extra = f"  (+{len(item['definitions']) - 1} more)" if len(item["definitions"]) > 1 else ""
            lines.append(f"  {item['name']:<36} {first['file']}:{first['line']}{extra}")
        lines.append("")
        lines.append("  NOTE: report-only, and biased toward silence. Name matching is")
        lines.append("  global and unqualified, so this finds obviously-dead JSX, not")
        lines.append("  subtly-dead JSX. Anything listed here is worth a human look.")

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="jsx_reachability")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args(argv)

    report = analyze()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_render_human(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
