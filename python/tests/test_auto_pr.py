# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_auto_pr.py
Unit tests for the autonomous PR review and push tool (python/tools/auto_pr.py).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

from tools.auto_pr import (
    audit_es3_syntax,
    generate_pr_body,
    run_pre_push_review,
    push_and_manage_pr,
)


def test_audit_es3_syntax_clean(tmp_path: Path):
    clean_jsx = tmp_path / "clean.jsx"
    clean_jsx.write_text(
        "var x = 10;\nfunction test() {\n    var y = 20;\n    return y;\n}\n",
        encoding="utf-8"
    )
    violations = audit_es3_syntax([clean_jsx])
    assert len(violations) == 0


def test_audit_es3_syntax_catches_let_and_const(tmp_path: Path):
    bad_jsx = tmp_path / "bad.jsx"
    bad_jsx.write_text(
        "let x = 10;\nconst y = 20;\nvar fn = () => {};\n",
        encoding="utf-8"
    )
    violations = audit_es3_syntax([bad_jsx])
    assert len(violations) >= 3
    msgs = [v[2] for v in violations]
    assert any("Keyword 'let'" in m for m in msgs)
    assert any("Keyword 'const'" in m for m in msgs)
    assert any("Arrow functions" in m for m in msgs)


def test_generate_pr_body():
    body = generate_pr_body("feat/new-feature", ["a1b2c3d feat: add new feature", "d4e5f6g test: add test cases"])
    assert "feat/new-feature" in body
    assert "add new feature" in body
    assert "Pre-Flight Quality Gate Verification Checklist" in body


@patch("subprocess.run")
def test_run_pre_push_review_success(mock_subproc):
    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_subproc.return_value = mock_res

    passed, issues = run_pre_push_review(skip_tests=True)
    assert passed is True
    assert len(issues) == 0


def _subproc_side_effect(fail_substrings):
    """subprocess.run side_effect: fail only for calls whose command
    contains one of fail_substrings, succeed otherwise. Lets a test
    target exactly one (or a specific combination of) check(s) instead
    of blanket-mocking every call to the same outcome."""
    def _side_effect(cmd, *args, **kwargs):
        res = MagicMock()
        cmd_str = " ".join(cmd)
        res.returncode = 1 if any(s in cmd_str for s in fail_substrings) else 0
        res.stdout, res.stderr = "", ""
        return res
    return _side_effect


@patch("subprocess.run")
def test_run_pre_push_review_reports_single_check_failure(mock_subproc):
    """Only the reachability check fails -- passed must be False and
    issues must name exactly that failure, not report success or blame
    an unrelated check. run_pre_push_review collects ALL failures rather
    than short-circuiting, so a single-failure case is the minimal
    regression proof that the per-check pass/fail wiring is correct."""
    mock_subproc.side_effect = _subproc_side_effect(["test_reachability_hook.py"])

    passed, issues = run_pre_push_review(skip_tests=True)

    assert passed is False
    assert len(issues) == 1
    assert "Reachability" in issues[0]


@patch("subprocess.run")
def test_run_pre_push_review_reports_multiple_independent_failures(mock_subproc):
    """Two unrelated checks fail -- both must be reported, proving the
    function doesn't stop at the first failure or only ever surface one
    issue regardless of how many checks actually failed."""
    mock_subproc.side_effect = _subproc_side_effect(
        ["verify_math_and_pillars.py", "headless_visual_dom"]
    )

    passed, issues = run_pre_push_review(skip_tests=True)

    assert passed is False
    assert len(issues) == 2
    assert any("86-Pillar" in i for i in issues)
    assert any("Node" in i for i in issues)


@patch("subprocess.run")
def test_run_pre_push_review_invokes_expected_commands(mock_subproc):
    """Verify the actual commands invoked, not just that *some* calls
    happened -- a blanket mock proves nothing about which checks ran."""
    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_subproc.return_value = mock_res

    run_pre_push_review(skip_tests=True)

    # ES3 audit is pure Python (no subprocess); skip_tests=True skips the
    # full pytest-suite gate -- 4 subprocess.run calls remain.
    assert mock_subproc.call_count == 4
    all_cmds = [" ".join(c.args[0]) for c in mock_subproc.call_args_list]
    assert any("test_reachability_hook.py" in c for c in all_cmds)
    assert any("verify_math_and_pillars.py" in c and "--all" in c for c in all_cmds)
    assert any("test_cep_jsx_bundle_sync.py" in c for c in all_cmds)
    assert any(c.startswith("node --test") for c in all_cmds)


@patch("subprocess.run")
def test_push_and_manage_pr_dry_run(mock_subproc):
    with patch("tools.auto_pr.get_git_info", return_value=("feat/test", "1234567", ["feat: test commit"])):
        success = push_and_manage_pr(dry_run=True)
        assert success is True
        mock_subproc.assert_not_called()


@patch("subprocess.run")
def test_auto_merge_uses_regular_merge_not_squash(mock_subproc):
    """Issue #382 -- every real merge in this repo's history is a regular
    merge commit (`git log --merges` confirmed), never squash. auto_merge
    must invoke `gh pr merge --auto --merge`, not `--squash`, or a rare
    real user of this flag would silently produce squashed history that
    looks different from everything else in the repo."""
    def _fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    mock_subproc.side_effect = _fake_run

    with patch("tools.auto_pr.get_git_info", return_value=("feat/test", "1234567", ["feat: test commit"])):
        success = push_and_manage_pr(auto_merge=True, dry_run=False)

    assert success is True
    all_cmds = [call.args[0] for call in mock_subproc.call_args_list]
    merge_cmds = [c for c in all_cmds if "merge" in c and "pr" in c]
    assert merge_cmds, "expected at least one `gh pr merge` invocation"
    for cmd in merge_cmds:
        assert "--merge" in cmd, f"expected --merge in {cmd}"
        assert "--squash" not in cmd, f"found --squash in {cmd} -- wrong merge strategy for this repo"
