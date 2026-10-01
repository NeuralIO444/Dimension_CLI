# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/field_reachability.py — the computed-but-never-read FIELD detector (issue #418).

WHY THIS TOOL EXISTS
--------------------
`tools/reachability.py` catches unreachable *modules*, which is why those
cannot hide. It cannot see a field that is computed, stored, and never
read by anything. That blind spot is where most of this repo's
"the feature is in but it isn't working" surprises actually live.

The confirmed example that motivated it (2026-09-04): `layer.archetype`
is computed by `surveyor.py` AND independently by `SovCore_Layer.jsx`.
Nothing reads it. Ever. It is wired to compute, not wired to matter.

CLAUDE.md already names the class in its sharp edges — "Manifest fields
can be computed but never consumed" — citing `*.static` (one production
reader whose own output was silently dropped at AE) and the
`V5_LAYER_KEYS` allow-list trap that swallowed `content_tag` for a full
release and no-op'd SOE for a year on six more fields.

THE CROSS-LANGUAGE RULE (the whole reason this is not a five-line grep)
----------------------------------------------------------------------
A Dimension model field is frequently written in Python and read in
ExtendScript. `conformed_transforms` is consumed by Babysitter.jsx;
`conformed_enabled` by its layer writers. A Python-only reader scan would
report every one of those as dead and be catastrophically wrong.

So the reader scan spans Python, JSX and CEP JS. A field is "read" if ANY
of the three touch it.

DELIBERATE CONSERVATISM
-----------------------
Field names are matched GLOBALLY, not resolved to their owning model. If
any model has a field `name` and any file anywhere reads `.name`, every
`name` field counts as read. This produces false NEGATIVES (dead fields
reported as live) and never false POSITIVES (live fields reported as
dead).

That asymmetry is chosen on purpose. A false positive here would send
someone deleting a field that Babysitter reads — the exact silent-drop
failure this tool exists to prevent. A false negative just means the tool
missed one, which is where we already are today. Report-only, and biased
toward silence over noise.

CARRYOVER IS NOT DEBT
---------------------
The conformed manifest deliberately preserves source values alongside
conformed ones (CLAUDE.md: "Conformed manifests carry source carryover
AND scaled output in parallel views"). Those are correct by design. This
tool does not attempt to distinguish them — that judgement belongs to a
human reading the report, which is why this ships report-only and is not
a CI gate.

USAGE
-----
    python -m tools.field_reachability            # human-readable
    python -m tools.field_reachability --json     # machine-readable
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

REPO_ROOT = Path(__file__).resolve().parents[2]
PY_ROOT = REPO_ROOT / "python"
MODELS_ROOT = PY_ROOT / "models"

# Non-Python trees that can legitimately read a Python-written field.
JS_ROOTS = (
    REPO_ROOT / "Scripts" / "Dimension_Assets",
    REPO_ROOT / "cep" / "js",
    REPO_ROOT / "cep" / "jsx",
)
JS_SUFFIXES = (".jsx", ".js")

# Field names too generic to attribute meaningfully. Matching these
# globally would mark everything live anyway; listing them keeps the
# report honest about what it cannot see rather than silently passing.
UNRESOLVABLE_COMMON: frozenset[str] = frozenset({
    "id", "name", "type", "kind", "value", "index", "path", "data",
    "status", "width", "height", "x", "y", "start", "end", "size",
})


def _iter_python_files(root: Path) -> List[Path]:
    return [
        p for p in root.rglob("*.py")
        if "__pycache__" not in str(p)
    ]


def _is_pydantic_model(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in ("BaseModel", "BaseSettings"):
            return True
        if isinstance(base, ast.Attribute) and base.attr in ("BaseModel", "BaseSettings"):
            return True
    return False


def collect_model_fields() -> Dict[str, List[Dict[str, Any]]]:
    """Map model-qualified name -> list of {field, module, line}."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for path in _iter_python_files(MODELS_ROOT):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        module = str(path.relative_to(PY_ROOT).with_suffix("")).replace("/", ".")
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not _is_pydantic_model(node):
                continue
            fields: List[Dict[str, Any]] = []
            for stmt in node.body:
                # `field: type` or `field: type = Field(...)`
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fname = stmt.target.id
                    if fname.startswith("__"):
                        continue
                    if fname == "model_config":
                        continue
                    fields.append({
                        "field": fname,
                        "module": module,
                        "line": stmt.lineno,
                    })
            if fields:
                out[f"{module}:{node.name}"] = fields
    return out


class _ReadCollector(ast.NodeVisitor):
    """Collects every name that is READ as an attribute, subscript, or getattr."""

    def __init__(self) -> None:
        self.reads: Set[str] = set()

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # `x.foo` in a load context is a read. `x.foo = 1` is a Store.
        if isinstance(node.ctx, ast.Load):
            self.reads.add(node.attr)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.ctx, ast.Load):
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                self.reads.add(sl.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # getattr(obj, "field") / obj.get("field")
        fn = node.func
        is_getattr = isinstance(fn, ast.Name) and fn.id == "getattr"
        is_get = isinstance(fn, ast.Attribute) and fn.attr in ("get", "pop", "setdefault")
        if (is_getattr and len(node.args) >= 2) or (is_get and node.args):
            arg = node.args[1] if is_getattr else node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                self.reads.add(arg.value)
        self.generic_visit(node)


class _ScopeCollector(ast.NodeVisitor):
    """Per-function reads and attribute-stores, for self-guard detection.

    A "self-guard" is a field whose only read is inside the very function
    that assigns it — e.g. `surveyor.py`:

        if getattr(layer, "archetype", None) is None:
            layer.archetype = classify_layer_archetype(layer)

    That read is real, but it exists only to decide whether to write.
    Nothing downstream consumes the value. Counting it as a genuine
    reader would hide exactly the case this tool was built for, so it
    gets its own category rather than being silently merged either way.
    """

    def __init__(self) -> None:
        self.scope_reads: Dict[str, Set[str]] = defaultdict(set)
        self.scope_stores: Dict[str, Set[str]] = defaultdict(set)
        self._scope: List[str] = ["<module>"]

    def _enter(self, name: str) -> None:
        self._scope.append(name)

    def _exit(self) -> None:
        self._scope.pop()

    @property
    def _cur(self) -> str:
        return self._scope[-1]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node.name)
        self.generic_visit(node)
        self._exit()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            self.scope_reads[self._cur].add(node.attr)
        elif isinstance(node.ctx, ast.Store):
            self.scope_stores[self._cur].add(node.attr)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            if isinstance(node.ctx, ast.Load):
                self.scope_reads[self._cur].add(sl.value)
            elif isinstance(node.ctx, ast.Store):
                self.scope_stores[self._cur].add(sl.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        fn = node.func
        is_getattr = isinstance(fn, ast.Name) and fn.id == "getattr"
        is_get = isinstance(fn, ast.Attribute) and fn.attr in ("get", "pop", "setdefault")
        if (is_getattr and len(node.args) >= 2) or (is_get and node.args):
            arg = node.args[1] if is_getattr else node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                self.scope_reads[self._cur].add(arg.value)
        self.generic_visit(node)


def collect_python_reads(include_tests: bool = False) -> Dict[str, Set[str]]:
    """Returns {"real": names read outside their writing scope,
                "self_guard": names read ONLY inside a scope that also writes them}."""
    real: Set[str] = set()
    guarded: Set[str] = set()

    for path in _iter_python_files(PY_ROOT):
        p = str(path)
        if not include_tests and ("/tests/" in p or "/tools/" in p):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        c = _ScopeCollector()
        c.visit(tree)
        for scope, names in c.scope_reads.items():
            stores = c.scope_stores.get(scope, set())
            for n in names:
                if n in stores:
                    guarded.add(n)
                else:
                    real.add(n)

    # A name read genuinely somewhere is live regardless of also being
    # self-guarded elsewhere.
    return {"real": real, "self_guard": guarded - real}


# A read: `.field` NOT followed by `=` (and not `==`/`===`, which IS a read).
_JS_ATTR_READ = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)\s*(?!=[^=])")
# A read: `obj["field"]` NOT followed by `=`.
_JS_STR_KEY_READ = re.compile(
    r"""\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\]\s*(?!=[^=])"""
)
# A WRITE, excluded: `.field =` (single =) or an object-literal key `field:` / "field":
_JS_ATTR_WRITE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)\s*=[^=]")
_JS_LITERAL_KEY = re.compile(r"""["']?([A-Za-z_][A-Za-z0-9_]*)["']?\s*:""")


def collect_js_reads() -> Set[str]:
    """Textual scan of JSX + CEP JS, distinguishing reads from writes.

    The distinction matters and is the whole reason this is not a grep.
    `SovCore_Layer.jsx` *emits* `archetype` — it builds an object literal
    with that key and hands it to Python. That is a WRITE. If a bare
    mention counted, every field JSX emits would look consumed, and the
    tool would be blind to exactly the case that motivated it (a field
    both sides compute and neither side reads).

    So: `.field` in non-assignment position and `obj["field"]` reads count.
    `.field =` assignments and `field:` object-literal keys do not.

    Still over-inclusive in the safe direction — `==`/`===` comparisons
    count as reads (they are), and a field mentioned in a comment counts.
    Per the module docstring, false negatives are the acceptable failure
    mode here; false positives are not.
    """
    reads: Set[str] = set()
    for root in JS_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in JS_SUFFIXES or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            found = set(_JS_ATTR_READ.findall(text)) | set(_JS_STR_KEY_READ.findall(text))
            # Subtract names that appear ONLY as writes/literal keys in this file.
            writes = set(_JS_ATTR_WRITE.findall(text)) | set(_JS_LITERAL_KEY.findall(text))
            reads |= (found - (writes - found))
    return reads


def analyze(include_tests: bool = False) -> Dict[str, Any]:
    models = collect_model_fields()
    py = collect_python_reads(include_tests=include_tests)
    py_reads = py["real"]
    py_self_guard = py["self_guard"]
    js_reads = collect_js_reads()
    all_reads = py_reads | js_reads

    # field name -> list of owning "module:Model" declarations
    owners: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for model, fields in models.items():
        for f in fields:
            owners[f["field"]].append({
                "model": model,
                "module": f["module"],
                "line": f["line"],
            })

    unread: List[Dict[str, Any]] = []
    self_guarded: List[Dict[str, Any]] = []
    unresolvable: List[str] = []
    for fname, decls in sorted(owners.items()):
        if fname in UNRESOLVABLE_COMMON:
            unresolvable.append(fname)
            continue
        if fname in all_reads:
            continue
        entry = {"field": fname, "declared_in": decls}
        if fname in py_self_guard:
            self_guarded.append(entry)
        else:
            unread.append(entry)

    total_fields = sum(len(v) for v in models.values())
    return {
        "models_scanned": len(models),
        "fields_declared": total_fields,
        "distinct_field_names": len(owners),
        "python_read_names": len(py_reads),
        "js_read_names": len(js_reads),
        "unresolvable_common": sorted(unresolvable),
        "unread_fields": unread,
        "self_guarded_fields": self_guarded,
    }


def _render_human(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("Dimension field-reachability report (issue #418)")
    lines.append("=" * 60)
    lines.append(f"  pydantic models scanned : {report['models_scanned']:>5}")
    lines.append(f"  fields declared         : {report['fields_declared']:>5}")
    lines.append(f"  distinct field names    : {report['distinct_field_names']:>5}")
    lines.append(f"  names read in Python    : {report['python_read_names']:>5}")
    lines.append(f"  names read in JSX/JS    : {report['js_read_names']:>5}")
    lines.append("")

    unread = report["unread_fields"]
    if not unread:
        lines.append("No computed-but-never-read fields detected.")
    else:
        lines.append(f"Fields with NO reader in Python, JSX or CEP JS ({len(unread)}):")
        lines.append("")
        for item in unread:
            decls = item["declared_in"]
            first = decls[0]
            where = f"{first['module']}:{first['line']}"
            extra = f"  (+{len(decls) - 1} more decl)" if len(decls) > 1 else ""
            lines.append(f"  {item['field']:<34} {where}{extra}")
        lines.append("")
        lines.append("  NOTE: report-only. Source-carryover fields on the conformed")
        lines.append("  manifest are correct by design (CLAUDE.md: 'parallel views') —")
        lines.append("  a human decides which of these are debt.")

    guarded = report.get("self_guarded_fields") or []
    if guarded:
        lines.append("")
        lines.append(f"Read ONLY as a self-guard ({len(guarded)}):")
        lines.append("  (read solely to decide whether to write it — nothing consumes the value)")
        lines.append("")
        for item in guarded:
            first = item["declared_in"][0]
            lines.append(f"  {item['field']:<34} {first['module']}:{first['line']}")

    skipped = report["unresolvable_common"]
    if skipped:
        lines.append("")
        lines.append(f"Not analyzable — name too generic to attribute ({len(skipped)}):")
        lines.append("  " + ", ".join(skipped))

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="field_reachability")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument(
        "--include-tests", action="store_true",
        help="count test-file reads as real readers (default: production only)",
    )
    args = ap.parse_args(argv)

    report = analyze(include_tests=args.include_tests)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_render_human(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
