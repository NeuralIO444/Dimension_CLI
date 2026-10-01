# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/test_target_cli_remove.py
QA sweep (2026-09-03) -- target_cli.py's `add` subcommand had no
counterpart to remove a custom target, matching the exact "create
exists, delete doesn't" gap found in profile_cli.py (issue #401).
`TargetStoreManager.remove_custom()` was already fully implemented
(cleans up favorites/last_used references too) but unreachable from
anywhere.

Mocked rather than subprocess-based: target_cli.py's main() constructs
TargetStoreManager() with the real production store path (no override
mechanism), so a subprocess-based test would risk touching Matt's real
~/Library/Application Support/.../dimension_targets.json. Mocking
TargetStoreManager in target_cli's own namespace keeps this test from
ever touching a real file.
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

import target_cli  # noqa: E402


def _run_main(argv, capsys):
    with patch.object(sys, "argv", ["target_cli.py"] + argv):
        target_cli.main()
    return capsys.readouterr().out.strip()


class TestRemoveSubcommand:
    def test_remove_calls_remove_custom_with_the_given_id(self, capsys):
        mock_tsm = MagicMock()
        mock_tsm.remove_custom.return_value = True

        with patch.object(target_cli, "TargetStoreManager", return_value=mock_tsm):
            out = _run_main(["remove", "--id", "custom:my_format"], capsys)

        mock_tsm.remove_custom.assert_called_once_with("custom:my_format")
        result = json.loads(out)
        assert result == {"id": "custom:my_format", "removed": True}

    def test_remove_reports_false_when_id_not_found(self, capsys):
        """remove_custom() returns False (not an exception) when the id
        doesn't exist -- the CLI must surface that honestly, not claim
        success for a no-op."""
        mock_tsm = MagicMock()
        mock_tsm.remove_custom.return_value = False

        with patch.object(target_cli, "TargetStoreManager", return_value=mock_tsm):
            out = _run_main(["remove", "--id", "custom:does_not_exist"], capsys)

        result = json.loads(out)
        assert result == {"id": "custom:does_not_exist", "removed": False}

    def test_remove_requires_id_argument(self):
        with patch.object(sys, "argv", ["target_cli.py", "remove"]):
            try:
                target_cli.main()
                assert False, "expected SystemExit for missing --id"
            except SystemExit:
                pass

    def test_add_subcommand_still_works_unmodified(self, capsys):
        """Regression guard: adding the remove subcommand must not
        perturb the existing add subcommand's behavior."""
        mock_target = MagicMock()
        mock_target.id = "custom:test_format"
        mock_target.label = "Test Format"
        mock_target.width = 1080
        mock_target.height = 1080
        mock_target.duration = None
        mock_target.fps = None
        mock_target.subcategory = "user"
        mock_target.aspect_label = "1:1"
        mock_target.output_name_template = None

        mock_tsm = MagicMock()
        mock_tsm.add_custom.return_value = mock_target

        with patch.object(target_cli, "TargetStoreManager", return_value=mock_tsm):
            out = _run_main(
                ["add", "--label", "Test Format", "--width", "1080", "--height", "1080"],
                capsys,
            )

        result = json.loads(out)
        assert result["id"] == "custom:test_format"
        assert result["width"] == 1080
