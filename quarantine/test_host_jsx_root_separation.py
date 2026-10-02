# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_host_jsx_root_separation.py
Source-level invariant: cep/jsx/host.jsx keeps two roots apart.

  _workingRoot()  — where THIS PANEL's output goes. Follows the user's
                    working-directory preference so a client conform
                    lands next to its .aep.
  _bridgeRoot()   — where the .dimension_inbox job queue and
                    poller_heartbeat.txt live. An IPC channel.

Why they must stay separate: the process on the far side of the bridge
is a separate Python program (dimension_server.py serving the Dashboard,
or cli.py) whose root is fixed by its own launch arguments and which
never reads the CEP preference. On 2026-08-23 the working-directory
feature routed the poller's heartbeat through _workingRoot(); the
heartbeat moved to <aep_dir>/Dimension/, the Dashboard kept polling the
repo, and it reported "AE engine not responding: poller heartbeat stale"
while the poller was ticking normally two directories away.

These are text assertions over the JSX source rather than behavioral
tests. That is deliberate and is NOT the "synthetic fixture standing in
for a JSX contract" anti-pattern CLAUDE.md warns about: nothing here
claims to prove AE's runtime behavior. It proves a property of the
source that a human reviewer would otherwise have to re-check by eye on
every future edit — exactly the kind of re-merging that caused the bug.
Runtime behavior stays covered by the PR's manual AE QA checklist.
"""

from __future__ import annotations

import os
import re

import pytest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_HOST_JSX = os.path.join(_REPO, "cep", "jsx", "host.jsx")


@pytest.fixture(scope="module")
def host_src() -> str:
    with open(_HOST_JSX, "r", encoding="utf-8") as fh:
        return fh.read()


def _code_lines(src: str):
    """Yield (lineno, text) for lines that are not pure comments.

    The rationale comments in host.jsx name both functions repeatedly;
    without this filter every assertion below would trip over prose.
    """
    for i, line in enumerate(src.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("*"):
            continue
        yield i, line


class TestBothRootsExist:
    def test_working_root_is_defined(self, host_src):
        assert re.search(r"^function _workingRoot\(\)", host_src, re.M)

    def test_bridge_root_is_defined(self, host_src):
        assert re.search(r"^function _bridgeRoot\(\)", host_src, re.M)

    def test_they_are_not_aliases(self, host_src):
        """_bridgeRoot() returning _workingRoot() would reintroduce the
        bug while leaving both names in place."""
        body = re.search(
            r"function _bridgeRoot\(\)\s*\{(.*?)\}", host_src, re.S).group(1)
        assert "_workingRoot" not in body, (
            "_bridgeRoot() must not delegate to _workingRoot() — they answer "
            "different questions; see the 2026-08-23 Dashboard regression."
        )


class TestIpcPathsUseBridgeRoot:
    """Every .dimension_inbox / heartbeat path must anchor to _bridgeRoot()."""

    def test_no_ipc_path_is_built_from_working_root(self, host_src):
        offenders = [
            (n, ln.strip()) for n, ln in _code_lines(host_src)
            if "_workingRoot()" in ln
            and (".dimension_inbox" in ln or "poller_heartbeat" in ln)
        ]
        assert not offenders, (
            "IPC paths must use _bridgeRoot(), not _workingRoot():\n"
            + "\n".join(f"  host.jsx:{n}: {t}" for n, t in offenders)
        )

    def test_inbox_dir_uses_bridge_root(self, host_src):
        line = [ln for _, ln in _code_lines(host_src) if "inboxDir:" in ln]
        assert line, "poller inboxDir() not found"
        assert "_bridgeRoot()" in line[0], line[0].strip()

    def test_every_dimension_inbox_literal_anchors_to_bridge_root(self, host_src):
        """Catches a new inbox path added later without the anchor."""
        for n, ln in _code_lines(host_src):
            if ".dimension_inbox" not in ln:
                continue
            # Either built from _bridgeRoot() on the same line, or from a
            # local already assigned _bridgeRoot() (the tick() pattern).
            assert ("_bridgeRoot()" in ln or re.search(r"\broot\s*\+", ln)), (
                f"host.jsx:{n} builds a .dimension_inbox path from an "
                f"unanchored root: {ln.strip()}"
            )

    def test_tick_root_local_is_the_bridge_root(self, host_src):
        """tick() assigns `var root = ...` and uses it for the heartbeat."""
        m = re.search(r"var root = (_\w+Root)\(\);", host_src)
        assert m, "tick()'s root assignment not found"
        assert m.group(1) == "_bridgeRoot", (
            f"tick() anchors its heartbeat to {m.group(1)}(); it must use "
            "_bridgeRoot() or the Dashboard loses sight of the poller."
        )


class TestOutputPathsUseWorkingRoot:
    """Conversely, project output must follow the user's preference."""

    @pytest.mark.parametrize("artifact", [
        "scrape_manifest.json",
        "chunk_manifest.json",
        "transfer_status.log",
    ])
    def test_output_artifact_follows_working_root(self, host_src, artifact):
        lines = [
            ln for _, ln in _code_lines(host_src)
            if artifact in ln and ("+" in ln or "=" in ln) and "Root()" in ln
        ]
        assert lines, f"no path construction found for {artifact}"
        for ln in lines:
            assert "_workingRoot()" in ln, (
                f"{artifact} must follow the working directory: {ln.strip()}"
            )


class TestSetterContract:
    """main.js pushes the resolved directory in via dimSetWorkingRoot()."""

    def test_setter_and_getter_exist(self, host_src):
        assert re.search(r"^function dimSetWorkingRoot\(", host_src, re.M)
        assert re.search(r"^function dimGetWorkingRoot\(", host_src, re.M)

    def test_setter_rejects_an_empty_path(self, host_src):
        """Clearing the override would silently drop the panel back to
        REPO_ROOT and split a session's artifacts across two roots."""
        body = re.search(
            r"function dimSetWorkingRoot\(rootPath\)\s*\{(.*?)\n\}",
            host_src, re.S).group(1)
        assert "if (!rootPath)" in body
        assert "ERROR" in body

    def test_setter_never_touches_the_bridge_root(self, host_src):
        body = re.search(
            r"function dimSetWorkingRoot\(rootPath\)\s*\{(.*?)\n\}",
            host_src, re.S).group(1)
        assert "REPO_ROOT" not in body, (
            "dimSetWorkingRoot must not reassign REPO_ROOT — the bridge "
            "anchor has to stay fixed while the working directory moves."
        )
