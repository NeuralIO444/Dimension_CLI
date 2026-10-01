# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Tests for tools/inbox_quarantine.py (RECOMMENDATIONS M7) — tmp dirs,
no AE needed. Stale ages are simulated with os.utime."""

from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

from tools.inbox_quarantine import main, quarantine_stale_jobs

_DAY_S = 86_400


def _make_job(folder: Path, name: str, *, age_days: float, job_type: str = "inject") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps({"type": job_type}), encoding="utf-8")
    old = time.time() - age_days * _DAY_S
    os.utime(path, (old, old))
    return path


class TestQuarantineStaleJobs:
    def test_moves_only_stale_pending(self, tmp_path: Path):
        inbox = tmp_path / ".dimension_inbox"
        stale = _make_job(inbox, "job_old.json", age_days=10)
        fresh = _make_job(inbox, "job_new.json", age_days=1)

        payload = quarantine_stale_jobs(
            tmp_path, days=7.0, today=date(2026, 7, 6)
        )

        qdir = inbox / "quarantine_20260706"
        assert not stale.exists()
        assert (qdir / "job_old.json").is_file()
        assert fresh.is_file()  # fresh job untouched
        assert [m["name"] for m in payload["moved"]] == ["job_old.json"]
        assert payload["errors"] == []
        assert payload["quarantine_dir"] == str(qdir)

    def test_dry_run_moves_nothing(self, tmp_path: Path):
        inbox = tmp_path / ".dimension_inbox"
        stale = _make_job(inbox, "job_old.json", age_days=10)

        payload = quarantine_stale_jobs(tmp_path, days=7.0, dry_run=True)

        assert stale.is_file()  # still in place
        assert len(payload["moved"]) == 1
        assert payload["dry_run"] is True
        assert not any(inbox.glob("quarantine_*"))

    def test_claimed_jobs_reported_not_moved(self, tmp_path: Path):
        inbox = tmp_path / ".dimension_inbox"
        claimed = _make_job(inbox / "processing", "job_mid.json", age_days=10)

        payload = quarantine_stale_jobs(tmp_path, days=7.0)

        assert claimed.is_file()  # poller-owned; never moved
        assert payload["moved"] == []
        assert [j["name"] for j in payload["stale_claimed_not_moved"]] == [
            "job_mid.json"
        ]

    def test_custom_days_threshold(self, tmp_path: Path):
        inbox = tmp_path / ".dimension_inbox"
        _make_job(inbox, "job_3d.json", age_days=3)

        assert quarantine_stale_jobs(tmp_path, days=7.0)["moved"] == []
        moved = quarantine_stale_jobs(tmp_path, days=2.0)["moved"]
        assert [m["name"] for m in moved] == ["job_3d.json"]

    def test_empty_inbox_clean_summary(self, tmp_path: Path):
        payload = quarantine_stale_jobs(tmp_path, days=7.0)
        assert payload["moved"] == []
        assert payload["errors"] == []


class TestCli:
    def test_exit_zero_on_clean_and_on_move(self, tmp_path: Path, capsys):
        assert main(["--repo", str(tmp_path)]) == 0
        capsys.readouterr()

        _make_job(tmp_path / ".dimension_inbox", "job_old.json", age_days=10)
        assert main(["--repo", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "job_old.json" in out

    def test_json_output(self, tmp_path: Path, capsys):
        _make_job(tmp_path / ".dimension_inbox", "job_old.json", age_days=10)
        assert main(["--repo", str(tmp_path), "--json", "--dry-run"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True
        assert [m["name"] for m in payload["moved"]] == ["job_old.json"]
