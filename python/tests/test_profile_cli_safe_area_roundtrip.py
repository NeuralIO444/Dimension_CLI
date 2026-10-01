# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_profile_cli_safe_area_roundtrip.py — Track B / B3 (2026-08-26).

Verifies `profile_cli.py set-safe-area` persistence round-trip, per the
B3 exit criterion "Verify safe_area persistence round-trip via
set-safe-area CLI".

`StudioProfileRegistry` is a module-level singleton
(`logic.studio_profile_registry.REGISTRY`) rooted at the real
`~/Library/Application Support/Dimension/profiles/` — there's no CLI
flag or env var to redirect it, so exercising `profile_cli.py` in-process
would mutate the developer's real profile data. Each command instead
runs as its own subprocess with `HOME` pointed at a throwaway temp
directory (`Path.home()` follows `$HOME` on POSIX); every subprocess
gets a fresh `REGISTRY` instance, so this also verifies persistence
survives past the writing process — the same "CEP execFiles a fresh
`profile_cli.py` per command" shape production actually uses, not just
an in-memory cache round-trip.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

REPO_PY = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CLI = os.path.join(REPO_PY, "profile_cli.py")


def _run(args, home):
    env = dict(os.environ)
    env["HOME"] = str(home)
    proc = subprocess.run(
        [sys.executable, CLI] + args,
        cwd=REPO_PY, capture_output=True, text=True, timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class TestSafeAreaRoundTrip:
    def test_set_safe_area_persists_across_fresh_processes(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()

        set_result = _run([
            "set-safe-area", "--profile", "default",
            "--top", "0.12", "--right", "0.08",
            "--bottom", "0.15", "--left", "0.07",
        ], home)
        assert set_result["safe_area"] == {
            "top": 0.12, "right": 0.08, "bottom": 0.15, "left": 0.07,
        }

        # Fresh subprocess = fresh REGISTRY singleton = fresh load from
        # disk. If this matches, the write actually reached the YAML,
        # not just the writer's in-memory cache.
        show_result = _run(["show", "default"], home)
        assert show_result["safe_area"] == {
            "top": 0.12, "right": 0.08, "bottom": 0.15, "left": 0.07,
        }

    def test_partial_update_preserves_unspecified_edges(self, tmp_path):
        """--top alone must not reset right/bottom/left to schema
        defaults — set-safe-area's kwargs fallback (`args.X if args.X
        is not None else sa.X`) is the contract under test."""
        home = tmp_path / "home"
        home.mkdir()

        _run([
            "set-safe-area", "--profile", "default",
            "--top", "0.20", "--right", "0.20",
            "--bottom", "0.20", "--left", "0.20",
        ], home)

        partial = _run([
            "set-safe-area", "--profile", "default", "--top", "0.01",
        ], home)
        assert partial["safe_area"]["top"] == 0.01
        assert partial["safe_area"]["right"] == 0.20
        assert partial["safe_area"]["bottom"] == 0.20
        assert partial["safe_area"]["left"] == 0.20

        show_result = _run(["show", "default"], home)
        assert show_result["safe_area"] == {
            "top": 0.01, "right": 0.20, "bottom": 0.20, "left": 0.20,
        }

    def test_out_of_range_edge_rejected_and_not_persisted(self, tmp_path):
        """SafeArea's [0, 0.5) validator must reject before writing —
        an invalid edge shouldn't corrupt the on-disk profile."""
        home = tmp_path / "home"
        home.mkdir()

        env = dict(os.environ)
        env["HOME"] = str(home)
        proc = subprocess.run(
            [sys.executable, CLI, "set-safe-area", "--profile", "default", "--top", "0.9"],
            cwd=REPO_PY, capture_output=True, text=True, timeout=60, env=env,
        )
        assert proc.returncode != 0

        show_result = _run(["show", "default"], home)
        # Untouched — still the profile's shipped default, not 0.9.
        assert show_result["safe_area"]["top"] != 0.9
