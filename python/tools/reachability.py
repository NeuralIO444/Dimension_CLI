# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/reachability.py
Static reachability analysis for the Dimension Python codebase.

Answers one question: **what can the shipped product actually reach?**

Builds two graphs and walks both from real production entry points:

  1. MODULE graph — module -> modules it imports
  2. SYMBOL graph — module -> every bare name it references

WHY THIS EXISTS
---------------
`dimension_engine.spec` uses `collect_submodules('core'|'logic'|'models')`,
which force-bundles every module into the shipped binary regardless of
whether anything can reach it. Dead code therefore has had zero visible
cost, and 14.4% of production Python accumulated as unreachable (see
`docs/audits_2026/2026-08-18-reachability-audit-and-dce-plan.md`).

This module makes that measurable and, via
`python/tests/test_reachability.py`, keeps it from regrowing.

DELIBERATE BIAS
---------------
The symbol graph is NAME-BASED and therefore an OVER-approximation of
reachability: if the bare name `foo` appears anywhere in a reachable
module, `foo` counts as reachable. Python's dynamic dispatch (getattr,
string imports, registries) makes exact reachability undecidable, so
this tool biases toward FALSE NEGATIVES.

    Anything reported dead is dead with high confidence.
    Some real dead code will be missed.

That is the correct bias for an audit that authorizes deletion.

KNOWN FALSE-POSITIVE CLASSES (filtered — do not remove these filters)
--------------------------------------------------------------------
- Framework-invoked callables: pydantic `@field_validator` /
  `@model_validator`, `@property`, `@staticmethod`, pytest `@fixture`.
  Called by the framework, never by bare name.
- Visitor dispatch: `visit_<NodeType>` methods are resolved by
  constructed name (`getattr(self, 'visit_' + node.type)`).
- Dunder methods.

EXECUTED vs USEFUL — why import statements do not count as usage
----------------------------------------------------------------
Two different properties get confused here, and the distinction is the
whole point of the `vestigial_imports` report:

  EXECUTED — importing a module runs its top-level code. A vestigial
             import DOES do this. So the MODULE graph must keep the
             edge, and `_walk` must keep following it: dropping the
             edge would report a module dead while its top-level code
             still runs, which is a FALSE POSITIVE and violates this
             tool's stated bias.

  USEFUL   — something in the module is actually called. A vestigial
             import does NOT do this.

DCE cares about USEFUL. Before 2026-08-18 `_collect_refs` added every
imported alias to `names_used`, which meant one leftover import
laundered a completely dead module past both the module gate and the
symbol report:

    # live_module.py
    from core.zombie import zombie_fn   # never called anywhere

`core.zombie` reported reachable and `zombie_fn` reported used. Both
were dead. That is precisely the shape a half-finished deletion leaves
behind (callers removed, import forgotten), so it had to be closed
before any deletion phase. The fix is split in two:

  1. Import aliases no longer land in `names_used`. Only genuine
     `ast.Name` / `ast.Attribute` / string-literal references count.
  2. `vestigial_imports` names every import whose bound local name is
     never referenced in its own module. The module walk is unchanged
     (see EXECUTED above); `python/tests/test_reachability.py` closes
     the hole by failing when a vestigial import targets an
     allowlisted or otherwise-unreachable module.

Usage:
    python -m tools.reachability            # human summary, exit 0
    python -m tools.reachability --json     # machine-readable
    ./bin/reach                             # operator wrapper
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
PY_ROOT = REPO_ROOT / "python"

# Production entry points.
#
# `cli.py` is the PyInstaller entry in dimension_engine.spec; the rest
# are either spawned directly by the CEP panel / Dashboard or named in
# that spec's `hiddenimports`. Adding an entry point here widens what
# counts as reachable — do it only when something genuinely becomes a
# new way to start the product.
#
# DECIDED 2026-08-18 — `python/scripts/` is NOT an entry-point class,
# and this is settled, not an oversight. It was measured at the time:
# promoting all 14 script modules to walk roots flipped exactly two
# modules from unreachable to reachable — `core.manifest_diff`
# (correct: a live operator tool, see the allowlist's OPERATOR_TOOL
# entry) and `core.effect_conformer` (WRONG: unreachable, and its math
# documented in CLAUDE.md as actively harmful, kept "alive" only by two
# throwaway capture/profiling scripts from closed slots). One right,
# one wrong — and the wrong one was the module the project had already
# ruled must die.
#
# `effect_conformer` was deleted in Phase 2a (2026-08-19), so re-running
# that measurement today would flip only `manifest_diff` and look
# harmless. It is not. The principle is what generalizes, not the
# arithmetic: `python/scripts/` is a graveyard of one-off slot scripts,
# and counting a graveyard as an entry point makes the corpses look
# alive. The next dead module a stale script imports would be laundered
# exactly as `effect_conformer` was. Handle genuine operator tools where
# they belong — in the allowlist's vocabulary — not by widening the walk.
ENTRY_MODULES: Tuple[str, ...] = (
    "dimension.__main__",  # `dimension` console script (pyproject [project.scripts])
    "cli",                  # PyInstaller entry (dimension_engine.spec)
    "dimension_server",     # Dashboard HTTP server (cli.py serve)
    "orchestrator",         # conform pipeline orchestration
    "survey",
    "data_dumper",
    "comment_garden_cli",
    "prefs_cli",
    "target_cli",
    "profile_cli",
    "duplication_cli",
    "units_cli",
    "unit_override_cli",
    "lut_cli",              # `dimension_engine lut ...` — Color Room LUT derive/validate path
)

# Decorators whose presence means "the framework calls this, not us".
FRAMEWORK_DECORATORS: frozenset[str] = frozenset({
    "field_validator", "model_validator", "validator", "root_validator",
    "field_serializer", "model_serializer",
    "property", "setter", "getter", "cached_property",
    "staticmethod", "classmethod",
    "singledispatch", "singledispatchmethod",
    "overload", "abstractmethod",
    "fixture", "hookimpl",
})

# Prefixes resolved by constructed name rather than direct reference.
DYNAMIC_DISPATCH_PREFIXES: Tuple[str, ...] = ("visit_", "do_", "handle_")


def _module_name(path: Path) -> str:
    rel = path.relative_to(PY_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _classify(path: Path) -> str:
    p = str(path)
    if "/tests/" in p:
        return "test"
    if "/scripts/" in p:
        return "script"
    if "/tools/" in p:
        return "tool"
    return "prod"


def _decorator_names(node: ast.AST) -> List[str]:
    out: List[str] = []
    for deco in getattr(node, "decorator_list", []) or []:
        for sub in ast.walk(deco):
            if isinstance(sub, ast.Name):
                out.append(sub.id)
            elif isinstance(sub, ast.Attribute):
                out.append(sub.attr)
    return out


class Analysis:
    """Result of one reachability pass."""

    def __init__(self) -> None:
        self.modules: Dict[str, Dict[str, Any]] = {}
        self.imports: Dict[str, Set[str]] = defaultdict(set)
        self.names_used: Dict[str, Set[str]] = defaultdict(set)
        self.defs: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.parse_failures: List[str] = []
        # Raw records; targets are resolved in analyze(), once every
        # module name is known.
        self.vestigial_raw: List[Dict[str, Any]] = []
        self.star_imports_raw: List[Dict[str, Any]] = []

    # -- graph walks ----------------------------------------------------

    def _resolve_primary(self, target: str) -> Optional[str]:
        """Longest known module name that is a prefix of ``target``.

        Unlike :meth:`_resolve` this does NOT return ancestor packages —
        it answers "which single module does this dotted string name?"
        Returns ``None`` for stdlib / third-party / unknown targets.
        """
        parts = target.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in self.modules:
                return cand
        return None

    def _resolve(self, target: str) -> Set[str]:
        """Map an import string onto known module names.

        Importing a submodule executes every parent package's
        ``__init__.py`` — ``from stages.batch import x`` runs
        ``stages/__init__.py``. Parent packages are therefore reachable
        too, and must be returned here or they show up as false-positive
        dead code. (This bug was live in the first draft and would have
        wrongly condemned ``stages/__init__.py``.)
        """
        out: Set[str] = set()
        parts = target.split(".")
        # Longest known prefix = the module actually imported.
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in self.modules:
                out.add(cand)
                break
        # Every ancestor package runs its __init__ on import.
        for i in range(1, len(parts)):
            pkg = ".".join(parts[:i])
            if pkg in self.modules:
                out.add(pkg)
        return out

    def _walk(self, roots: Iterable[str]) -> Set[str]:
        seen: Set[str] = set()
        stack = [r for r in roots if r in self.modules]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for tgt in self.imports.get(cur, ()):
                for resolved in self._resolve(tgt):
                    if resolved not in seen:
                        stack.append(resolved)
        return seen

    def reachable_from_production(self) -> Set[str]:
        return self._walk(ENTRY_MODULES)

    def reachable_from_tests(self) -> Set[str]:
        tests = [m for m, i in self.modules.items() if i["kind"] == "test"]
        return self._walk(tests)


def _parse_all() -> Analysis:
    a = Analysis()
    for path in sorted(PY_ROOT.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        try:
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            a.parse_failures.append(f"{path}: {exc}")
            continue

        mod = _module_name(path)
        a.modules[mod] = {
            "path": str(path.relative_to(REPO_ROOT)),
            "kind": _classify(path),
            "loc": len(src.splitlines()),
        }

        _collect_defs(a, mod, tree, a.modules[mod]["kind"])
        _collect_refs(a, mod, tree)
        _collect_vestigial(a, mod, tree, src.splitlines())
    return a


def _collect_defs(a: Analysis, mod: str, tree: ast.Module, kind: str) -> None:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            a.defs[(mod, node.name)] = {
                "module": mod, "name": node.name, "kind": "function",
                "lineno": node.lineno, "loc": end - node.lineno + 1,
                "mkind": kind, "decorators": _decorator_names(node),
            }
        elif isinstance(node, ast.ClassDef):
            end = getattr(node, "end_lineno", node.lineno)
            a.defs[(mod, node.name)] = {
                "module": mod, "name": node.name, "kind": "class",
                "lineno": node.lineno, "loc": end - node.lineno + 1,
                "mkind": kind, "decorators": _decorator_names(node),
            }
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    e2 = getattr(sub, "end_lineno", sub.lineno)
                    qual = f"{node.name}.{sub.name}"
                    a.defs[(mod, qual)] = {
                        "module": mod, "name": qual, "kind": "method",
                        "lineno": sub.lineno, "loc": e2 - sub.lineno + 1,
                        "mkind": kind, "decorators": _decorator_names(sub),
                    }


def _collect_refs(a: Analysis, mod: str, tree: ast.Module) -> None:
    """Populate the module graph (`imports`) and the symbol graph (`names_used`).

    Import statements feed the MODULE graph only. They deliberately do NOT
    feed `names_used`: an import proves the target is EXECUTED, never that
    anything in it is USEFUL. See the module docstring's "EXECUTED vs
    USEFUL" section — counting import aliases as uses let one vestigial
    import launder a dead module past both the gate and the symbol report.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                a.imports[mod].add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                a.imports[mod].add(node.module)
            for alias in node.names:
                if node.module and alias.name != "*":
                    a.imports[mod].add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Name):
            a.names_used[mod].add(node.id)
        elif isinstance(node, ast.Attribute):
            a.names_used[mod].add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # String literals can name modules or functions (dynamic
            # import, monkeypatch targets, registry keys). Count them —
            # conservative, keeps us biased toward false negatives.
            val = node.value
            if val and len(val) < 120:
                for piece in val.replace(":", ".").split("."):
                    piece = piece.strip()
                    if piece.isidentifier():
                        a.names_used[mod].add(piece)


def _module_reference_names(tree: ast.Module) -> Set[str]:
    """Every name a module genuinely REFERENCES, ignoring its own imports.

    `ast.Import` / `ast.ImportFrom` carry their bindings as `ast.alias`
    string fields, not as `ast.Name` nodes, so walking the tree for
    Name/Attribute/arg naturally excludes the import statements
    themselves — which is exactly what "is this import ever used?"
    needs.

    `ast.arg` is included deliberately: a name imported into a test
    module and consumed as a pytest fixture parameter appears only as a
    function argument. Counting it keeps this analysis biased toward
    FALSE NEGATIVES, matching the rest of the tool.
    """
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Quoted annotations, `__all__` entries, monkeypatch targets,
            # registry keys. Same conservative heuristic as _collect_refs.
            val = node.value
            if val and len(val) < 120:
                for piece in val.replace(":", ".").split("."):
                    piece = piece.strip()
                    if piece.isidentifier():
                        names.add(piece)
    return names


def _dunder_all(tree: ast.Module) -> Set[str]:
    """String entries of a module-level ``__all__``.

    Re-export packages (`__init__.py` doing `from .x import Y` purely so
    callers can say `from pkg import Y`) bind names they never reference.
    Those imports are load-bearing, not vestigial.
    """
    out: Set[str] = set()
    for node in tree.body:
        target = None
        if isinstance(node, ast.Assign) and node.targets:
            first = node.targets[0]
            if isinstance(first, ast.Name) and first.id == "__all__":
                target = node.value
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "__all__":
                target = node.value
        if isinstance(target, (ast.List, ast.Tuple)):
            for elt in target.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    out.add(elt.value)
    return out


_NOQA_RE = re.compile(r"#\s*noqa(?::\s*(?P<codes>[A-Z][A-Z0-9]*(?:\s*,\s*[A-Z][A-Z0-9]*)*))?")


def _has_unused_import_noqa(lines: List[str], node: ast.AST) -> bool:
    """Is this import statement annotated `# noqa: F401` (or bare `# noqa`)?

    F401 is flake8's "imported but unused". Writing it is an explicit,
    reviewable claim by a human that the import is deliberately
    side-effecting or a re-export — the same kind of written
    justification the module ALLOWLIST demands. Honouring it keeps this
    report free of the legitimate cases (pytest registering fixtures on
    a plugin namespace, `models/bridge_contract.py`'s re-exports) so the
    entries that remain are real.

    Scanned across the statement's full physical extent, because the
    marker sits on the `from x import (` line of a parenthesised import.
    """
    start = getattr(node, "lineno", 0)
    end = getattr(node, "end_lineno", start) or start
    for idx in range(start, end + 1):
        if idx < 1 or idx > len(lines):
            continue
        m = _NOQA_RE.search(lines[idx - 1])
        if not m:
            continue
        codes = m.group("codes")
        # A directive with no codes suppresses everything on the line.
        if codes is None:
            return True
        if "F401" in [c.strip() for c in codes.split(",")]:
            return True
    return False


def _collect_vestigial(
    a: Analysis, mod: str, tree: ast.Module, lines: List[str]
) -> None:
    """Record imports whose bound local name is never referenced.

    An import is VESTIGIAL when nothing in its own module ever uses the
    name it binds. It still EXECUTES the target's top-level code — see
    the module docstring — so this never changes the module walk. It is
    reported so that a half-finished deletion (callers removed, import
    left behind) cannot quietly keep a dead module looking alive.

    Bound-name rules:
        import a.b               -> binds `a`
        import a.b as z          -> binds `z`
        from a import b          -> binds `b`
        from a import b as c     -> binds `c`
        from a import *          -> unresolvable; reported separately

    Imports carrying `# noqa: F401` are treated as declared-intentional —
    see :func:`_has_unused_import_noqa`.

    ALIAS WRITE-BACK. This function also repairs one thing `_collect_refs`
    can no longer do on its own. `from m import f as g` followed by `g()`
    leaves only the name `g` in the tree, so the symbol `m.f` would look
    unreferenced everywhere and land in `dead_symbols` — a FALSE POSITIVE,
    and a dangerous one: `core/scale_engine.py:78` imports the 569-LOC
    live conform function `apply_narrow_rule_set` under an alias. So when
    an import is proven NOT vestigial, the imported symbol's own name is
    written into `names_used`. This cannot reopen the laundering hole:
    laundering requires the bound name to be UNUSED, and the write-back
    happens only when it is used.
    """
    referenced = _module_reference_names(tree)
    exported = _dunder_all(tree)

    def _keep(name: str) -> None:
        """The bound name is genuinely used, so the symbol it denotes is too."""
        terminal = name.split(".")[-1]
        if terminal and terminal != "*":
            a.names_used[mod].add(terminal)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        declared_intentional = _has_unused_import_noqa(lines, node)

        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if declared_intentional or bound in referenced or bound in exported:
                    _keep(alias.name)
                    continue
                shown = alias.name + (f" as {alias.asname}" if alias.asname else "")
                a.vestigial_raw.append({
                    "module": mod,
                    "imported": shown,
                    "lineno": node.lineno,
                    "_target": alias.name,
                })
        else:
            # Relative imports (`from . import x`) have no absolute
            # module string; the binding check still applies, the target
            # simply resolves to None.
            base = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    a.star_imports_raw.append({
                        "module": mod,
                        "lineno": node.lineno,
                        "_target": base,
                    })
                    continue
                bound = alias.asname or alias.name
                if declared_intentional or bound in referenced or bound in exported:
                    _keep(alias.name)
                    continue
                full = f"{base}.{alias.name}" if base else alias.name
                shown = full + (f" as {alias.asname}" if alias.asname else "")
                # Resolve against the FULL dotted path, not the package.
                # `from logic import license_status` names a module, but
                # `logic` is a namespace package with no __init__.py, so
                # resolving the base alone returns None and the record is
                # silently dropped — the exact `from logic import style_ops`
                # shape this gate exists to catch. _resolve_primary takes the
                # longest known prefix, so passing the full path still yields
                # `logic.license_status` for
                # `from logic.license_status import get_current_status`.
                a.vestigial_raw.append({
                    "module": mod,
                    "imported": shown,
                    "lineno": node.lineno,
                    "_target": full,
                })


def _is_framework_invoked(info: Dict[str, Any]) -> bool:
    if set(info.get("decorators") or ()) & FRAMEWORK_DECORATORS:
        return True
    bare = info["name"].split(".")[-1]
    if bare.startswith("__") and bare.endswith("__"):
        return True
    return bare.startswith(DYNAMIC_DISPATCH_PREFIXES)


def analyze() -> Dict[str, Any]:
    """Run a full pass. Returns a JSON-serializable report."""
    a = _parse_all()
    prod_reachable = a.reachable_from_production()
    test_reachable = a.reachable_from_tests()

    prod_modules = {m for m, i in a.modules.items() if i["kind"] == "prod"}
    unreachable = sorted(prod_modules - prod_reachable)

    test_names: Set[str] = set()
    for m, i in a.modules.items():
        if i["kind"] == "test":
            test_names |= a.names_used.get(m, set())

    dead_symbols: List[Dict[str, Any]] = []
    test_only_symbols: List[Dict[str, Any]] = []
    for (mod, qual), info in a.defs.items():
        if info["mkind"] != "prod" or _is_framework_invoked(info):
            continue
        bare = qual.split(".")[-1]
        used_in_prod = any(
            bare in a.names_used.get(m, set()) for m in prod_reachable
        )
        if used_in_prod:
            continue
        (test_only_symbols if bare in test_names else dead_symbols).append(info)

    unreachable_loc = sum(a.modules[m]["loc"] for m in unreachable)
    dead_in_live = [s for s in dead_symbols if s["module"] not in set(unreachable)]
    test_only_in_live = [
        s for s in test_only_symbols if s["module"] not in set(unreachable)
    ]
    # Vestigial imports: resolve each target now that every module name
    # is known. Only local (in-tree) targets are interesting — a unused
    # `import json` is a lint nit, not a DCE laundering path.
    vestigial: List[Dict[str, Any]] = []
    for rec in a.vestigial_raw:
        target = a._resolve_primary(rec["_target"])
        if target is None:
            continue
        vestigial.append({
            "module": rec["module"],
            "imported": rec["imported"],
            "lineno": rec["lineno"],
            "target_module": target,
        })
    vestigial.sort(key=lambda d: (d["module"], d["lineno"]))

    star_imports: List[Dict[str, Any]] = []
    for rec in a.star_imports_raw:
        target = a._resolve_primary(rec["_target"])
        if target is None:
            continue
        star_imports.append({
            "module": rec["module"],
            "lineno": rec["lineno"],
            "target_module": target,
        })
    star_imports.sort(key=lambda d: (d["module"], d["lineno"]))

    prod_loc = sum(i["loc"] for i in a.modules.values() if i["kind"] == "prod")
    total_dead = (
        unreachable_loc
        + sum(s["loc"] for s in dead_in_live)
        + sum(s["loc"] for s in test_only_in_live)
    )

    return {
        "totals": {
            "prod_modules": len(prod_modules),
            "prod_reachable": len(prod_modules & prod_reachable),
            "prod_unreachable": len(unreachable),
            "prod_loc": prod_loc,
            "unreachable_loc": unreachable_loc,
            "dead_loc": total_dead,
            "dead_pct": round(100.0 * total_dead / prod_loc, 2) if prod_loc else 0.0,
        },
        "unreachable_modules": [
            {
                "module": m,
                "path": a.modules[m]["path"],
                "loc": a.modules[m]["loc"],
                "test_only": m in test_reachable,
            }
            for m in unreachable
        ],
        "dead_symbols": sorted(dead_in_live, key=lambda d: -d["loc"]),
        "test_only_symbols": sorted(test_only_in_live, key=lambda d: -d["loc"]),
        "vestigial_imports": vestigial,
        "star_imports": star_imports,
        "parse_failures": a.parse_failures,
    }


def _render_human(report: Dict[str, Any]) -> str:
    t = report["totals"]
    out: List[str] = []
    out.append("Dimension reachability report")
    out.append("=" * 60)
    out.append(
        f"  production modules : {t['prod_modules']:>6}"
        f"  (reachable {t['prod_reachable']}, unreachable {t['prod_unreachable']})"
    )
    out.append(f"  production LOC     : {t['prod_loc']:>6}")
    out.append(f"  dead / unreachable : {t['dead_loc']:>6}  ({t['dead_pct']}%)")
    out.append("")
    if report["unreachable_modules"]:
        out.append("Unreachable modules (no production entry point reaches these):")
        for m in sorted(report["unreachable_modules"], key=lambda x: -x["loc"]):
            tag = "test-only" if m["test_only"] else "FULLY DEAD"
            out.append(f"  {m['loc']:>5}  {tag:<11}  {m['path']}")
        out.append("")
    if report["dead_symbols"]:
        out.append("Dead symbols inside otherwise-live modules:")
        for s in report["dead_symbols"][:25]:
            out.append(f"  {s['loc']:>5}  {s['module']}:{s['lineno']}  {s['name']}")
        if len(report["dead_symbols"]) > 25:
            out.append(f"  ... and {len(report['dead_symbols']) - 25} more")
        out.append("")
    if report.get("vestigial_imports"):
        out.append(
            "Vestigial imports (bound name never referenced in its own "
            "module —"
        )
        out.append(
            "  the target still EXECUTES, but nothing in it is used here; "
            "this is how"
        )
        out.append("  a half-finished deletion keeps a dead module looking alive):")
        for v in report["vestigial_imports"]:
            out.append(
                f"  {v['module']}:{v['lineno']}  ->  {v['imported']}"
                f"   [target: {v['target_module']}]"
            )
        out.append("")
    if report.get("star_imports"):
        out.append("Star imports (bindings unresolvable — not analysed above):")
        for s in report["star_imports"]:
            out.append(
                f"  {s['module']}:{s['lineno']}  ->  "
                f"from {s['target_module']} import *"
            )
        out.append("")
    if report["parse_failures"]:
        out.append("Parse failures:")
        out.extend(f"  {p}" for p in report["parse_failures"])
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Static reachability analysis for the Dimension codebase.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--check-vestigial",
        action="store_true",
        help="exit 1 if any vestigial (unused) imports are detected (pre-commit gate)",
    )
    args = parser.parse_args(argv)

    report = analyze()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_render_human(report))

    if args.check_vestigial and report.get("vestigial_imports"):
        count = len(report["vestigial_imports"])
        print(
            f"\n\033[1;31m[REACHABILITY PRE-COMMIT FAILURE] Found {count} vestigial import(s) in codebase!\033[0m",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
