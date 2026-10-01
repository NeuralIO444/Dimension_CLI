# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_layer2_contract_policy.py
CI policy gate — Layer 2 contract tests must not use synthetic inline dicts.

Scans designated Layer 2 test modules for banned patterns that reproduce
the PR #45 anti-pattern (Python-built dicts that JSX never wrote).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

LAYER2_CONTRACT_FILES = (
    TESTS_DIR / "test_bridge_contracts_jsx.py",
    TESTS_DIR / "test_scrape_manifest_wire_contract.py",
)

ALLOWED_LOAD_PATTERNS = (
    "load_fixture",
    "read_text",
    "json.load",
    "Path(",
)


class _SyntheticDictVisitor(ast.NodeVisitor):
    """Detect inline dict literals passed to contract model parsers."""

    def __init__(self, source: str, path: Path):
        self.source = source
        self.path = path
        self.violations: list[str] = []

    def _line_snippet(self, node: ast.AST) -> str:
        try:
            segment = ast.get_source_segment(self.source, node) or ""
        except Exception:
            segment = ""
        return segment.replace("\n", " ")[:120]

    def visit_Call(self, node: ast.Call) -> None:
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        banned_calls = {
            "model_validate",
            "JsxScrapeManifestWire",
            "ScrapeManifest",
        }
        if func_name in banned_calls and node.args:
            first = node.args[0]
            if isinstance(first, ast.Dict):
                self.violations.append(
                    f"{self.path.name}:{node.lineno}: inline dict passed to "
                    f"{func_name}() — {self._line_snippet(node)}"
                )
        self.generic_visit(node)


def _scan_file(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    visitor = _SyntheticDictVisitor(source, path)
    visitor.visit(ast.parse(source, filename=str(path)))
    return visitor.violations


class TestLayer2ContractPolicy:
    @pytest.mark.parametrize("contract_file", LAYER2_CONTRACT_FILES)
    def test_no_synthetic_inline_dicts(self, contract_file: Path):
        assert contract_file.is_file(), f"missing {contract_file}"
        violations = _scan_file(contract_file)
        assert violations == [], (
            "Layer 2 contract tests must load real fixtures, not inline dicts:\n"
            + "\n".join(violations)
        )

    @pytest.mark.parametrize("contract_file", LAYER2_CONTRACT_FILES)
    def test_uses_real_fixture_loading(self, contract_file: Path):
        text = contract_file.read_text(encoding="utf-8")
        assert any(pat in text for pat in ALLOWED_LOAD_PATTERNS), (
            f"{contract_file.name} must load fixtures via read_text/json.load/"
            f"load_fixture — no real-fixture loader found"
        )