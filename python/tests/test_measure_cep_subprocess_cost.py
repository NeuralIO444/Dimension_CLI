# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_measure_cep_subprocess_cost.py
#408 -- unit coverage for the pure logic in
scripts/measure_cep_subprocess_cost.py: stats aggregation and graceful
failure handling. The actual timing (_time_invocation spawning a real
subprocess) is exercised by running the script directly, not mocked
here -- a mocked subprocess call would prove nothing about real cold-
start cost, which is the entire point of this tool.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "scripts")))

import measure_cep_subprocess_cost as m


class TestTimeInvocation:
    def test_returns_none_on_missing_binary(self):
        assert m._time_invocation(["/no/such/binary/exists"]) is None

    def test_returns_none_on_timeout(self):
        with patch.object(m.subprocess, "run", side_effect=m.subprocess.TimeoutExpired(cmd="x", timeout=1)):
            assert m._time_invocation(["irrelevant"]) is None

    def test_returns_a_positive_float_on_success(self):
        with patch.object(m.subprocess, "run") as mock_run:
            mock_run.return_value = None
            ms = m._time_invocation(["irrelevant"])
        assert isinstance(ms, float)
        assert ms >= 0.0


class TestMeasure:
    def test_aggregates_mean_median_stddev_across_runs(self):
        fake_timings = iter([100.0, 200.0, 300.0])
        with patch.object(m, "_time_invocation", side_effect=lambda argv: next(fake_timings)):
            result = m._measure(lambda: ["irrelevant"], runs=3)
        assert result is not None
        assert result["mean"] == 200.0
        assert result["median"] == 200.0
        assert result["min"] == 100.0
        assert result["max"] == 300.0
        assert result["stddev"] > 0.0
        assert result["samples"] == [100.0, 200.0, 300.0]

    def test_single_run_has_zero_stddev(self):
        with patch.object(m, "_time_invocation", return_value=150.0):
            result = m._measure(lambda: ["irrelevant"], runs=1)
        assert result["stddev"] == 0.0

    def test_returns_none_if_any_invocation_fails(self):
        # A launch failure mid-run (e.g. binary vanished) must abort the
        # whole measurement for that subcommand rather than average in
        # a None -- a partial average would misreport the real cost.
        timings = iter([100.0, None, 300.0])
        with patch.object(m, "_time_invocation", side_effect=lambda argv: next(timings)):
            result = m._measure(lambda: ["irrelevant"], runs=3)
        assert result is None


class TestMainIntegration:
    def test_main_reports_frozen_and_dev_na_when_neither_present(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(m, "FROZEN_BINARY", tmp_path / "nonexistent_binary")
        monkeypatch.setattr(m, "DEV_PYTHON", tmp_path / "nonexistent_python")
        exit_code = m.main(["--runs", "1"])
        out = capsys.readouterr().out
        assert exit_code == 2
        assert "NOT FOUND" in out
