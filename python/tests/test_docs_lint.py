# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_docs_lint.py

CLAUDE.md is read by every agent session before it touches this repo, so
its defects propagate into code. It had no test. The 2026-09-01 hardcore
audit (finding C1) found, in one file: seven headers whose fenced code
blocks had been stripped, leaving a dangling "…:" sentence; two sections
duplicated verbatim; and four factual claims contradicted by the code
they describe.

This lints the agent-facing markdown for the two defect classes a
machine can see:

1. **Stripped content.** A line ending in ":" that introduces a block
   (e.g. "referenced by:", "dropped into:") followed by a blank line and
   then a heading — the shape left behind when a fenced block is deleted.
2. **Duplicate headings.** The same H2/H3 twice in one file, which is how
   "ExtendScript constraints (ES3)" ended up in CLAUDE.md twice with the
   reader unable to tell which copy was current.

Plus a link check: every `docs/…md` path referenced must exist.

Deliberately NOT linted: prose style, length, heading order. This is a
correctness gate, not a style gate — the same reasoning as `ruff.toml`'s
narrow rule set. A noisy doc linter gets switched off.
"""

from __future__ import annotations

import os
import re

import pytest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Files an agent is expected to read as instructions, not as history.
_LINTED = ("CLAUDE.md", "README.md", "CONTRIBUTING.md")

# Headings that legitimately repeat: paired good/bad examples in
# CLAUDE.md's "How to ask the user" section, where the repetition IS the
# structure (two worked examples, each with a Bad and a Good form).
_ALLOWED_DUPLICATE_HEADINGS = {"### Bad", "### Good"}

_HEADING_RE = re.compile(r"^#{2,3} \S")
_INTRO_RE = re.compile(r":\s*$")
_DOCS_LINK_RE = re.compile(r"\((docs/[^)]+\.md)\)")


def _read(name: str) -> list[str]:
    path = os.path.join(_REPO_ROOT, name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read().splitlines()


def _linted_files() -> list[str]:
    return [n for n in _LINTED if os.path.isfile(os.path.join(_REPO_ROOT, n))]


@pytest.mark.parametrize("name", _LINTED)
class TestAgentFacingDocs:
    def test_no_introduced_block_was_stripped(self, name):
        """A colon-terminated intro line must be followed by content.

        The failure shape: `The current manifest is referenced by:` then a
        blank line then `### Safe-zone mask resolution` — the fenced path
        that made the sentence mean something is gone.
        """
        if name not in _linted_files():
            pytest.skip(f"{name} not present")
        lines = _read(name)
        offenders = []
        current_level = 0
        for i, line in enumerate(lines):
            if _HEADING_RE.match(line):
                current_level = len(line) - len(line.lstrip("#"))
                continue
            if not _INTRO_RE.search(line):
                continue
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            nxt2 = lines[i + 2] if i + 2 < len(lines) else ""
            if nxt.strip() != "":
                continue
            if i + 2 >= len(lines):
                offenders.append(f"  {name}:{i + 1}: {line.strip()}")
                continue
            if not _HEADING_RE.match(nxt2):
                continue
            # A DEEPER heading means the colon introduced the subsections
            # that follow ("...built through the lens of X:" then "### 1.
            # ..."), which is fine. A sibling-or-shallower heading means
            # this section ended on a promise it no longer keeps.
            next_level = len(nxt2) - len(nxt2.lstrip("#"))
            if next_level <= current_level:
                offenders.append(f"  {name}:{i + 1}: {line.strip()}")
        assert not offenders, (
            "Introduced block appears to have been stripped (colon line, then "
            "blank, then a heading). Restore the code block or reword the "
            "sentence so it does not promise content:\n" + "\n".join(offenders)
        )

    def test_no_duplicate_headings(self, name):
        if name not in _linted_files():
            pytest.skip(f"{name} not present")
        seen: dict[str, int] = {}
        dupes = []
        for i, line in enumerate(_read(name)):
            if not _HEADING_RE.match(line):
                continue
            key = line.strip()
            if key in _ALLOWED_DUPLICATE_HEADINGS:
                continue
            if key in seen:
                dupes.append(f"  {name}:{i + 1}: {key} (first seen line {seen[key]})")
            else:
                seen[key] = i + 1
        assert not dupes, (
            "Duplicate heading — a reader cannot tell which copy is current. "
            "Merge them, or make the titles distinct:\n" + "\n".join(dupes)
        )

    def test_docs_links_resolve(self, name):
        if name not in _linted_files():
            pytest.skip(f"{name} not present")
        missing = []
        for i, line in enumerate(_read(name)):
            for target in _DOCS_LINK_RE.findall(line):
                if not os.path.isfile(os.path.join(_REPO_ROOT, target)):
                    missing.append(f"  {name}:{i + 1}: {target}")
        assert not missing, (
            "Link points at a docs file that does not exist:\n" + "\n".join(missing)
        )


class TestRoadmapLinks:
    """ROADMAP.md is the index agents follow to find plan docs. A dangling
    link there sends the reader looking for a spec that was never written
    (three of them were dangling as of the 2026-09-01 audit)."""

    def test_roadmap_docs_links_resolve(self):
        missing = []
        for i, line in enumerate(_read("ROADMAP.md")):
            for target in _DOCS_LINK_RE.findall(line):
                if not os.path.isfile(os.path.join(_REPO_ROOT, target)):
                    missing.append(f"  ROADMAP.md:{i + 1}: {target}")
        assert not missing, (
            "ROADMAP links a docs file that does not exist. Either write it, "
            "drop the link, or mark the item as unscoped:\n" + "\n".join(missing)
        )
