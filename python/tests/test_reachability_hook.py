# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_reachability_hook.py — Issue #298
Tests pre-commit reachability gate execution and exit codes.
"""

from unittest.mock import patch
from tools.reachability import main as reachability_main


class TestReachabilityHook:
    def test_check_vestigial_passes_cleanly(self):
        # Current repo state must have 0 vestigial imports
        exit_code = reachability_main(["--check-vestigial"])
        assert exit_code == 0

    def test_check_vestigial_fails_on_synthetic_vestigial_import(self):
        fake_report = {
            "totals": {"prod_modules": 10, "prod_reachable": 10, "prod_unreachable": 0, "prod_loc": 100, "dead_loc": 0, "dead_pct": 0},
            "unreachable_modules": [],
            "dead_symbols": [],
            "vestigial_imports": [
                {"module": "fake.mod", "lineno": 12, "imported": "unused_sym", "target_module": "fake.target"}
            ],
            "star_imports": [],
            "parse_failures": [],
        }
        with patch("tools.reachability.analyze", return_value=fake_report):
            exit_code = reachability_main(["--check-vestigial"])
            assert exit_code == 1
