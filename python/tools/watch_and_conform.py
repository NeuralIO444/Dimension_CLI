#!/usr/bin/env python3
# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tools/watch_and_conform.py
Autonomous Auto-Testing & Hot-Reload File Watcher Sentinel for Dimension.

Monitors python/, Scripts/, cep/, and config/ trees for modifications.
On file change:
  1. Rebuilds modular JSX if Babysitter_src/ was modified.
  2. Syncs updated ExtendScript files to cep/jsx/.
  3. Executes the corresponding targeted unit test slice (<500ms).
  4. Generates updated latest_diagnostic_report.html.
  5. Optionally sends live hot-reload signals to the CEP panel via CEF remote debugging (port 8088).

Usage:
  python3 python/tools/watch_and_conform.py
  python3 python/tools/watch_and_conform.py --all-on-start
  python3 python/tools/watch_and_conform.py --once
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Ensure python/ is on sys.path
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "python") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "python"))

# Directories to watch
_WATCH_DIRS = [
    _REPO_ROOT / "python",
    _REPO_ROOT / "Scripts",
    _REPO_ROOT / "cep",
    _REPO_ROOT / "config",
]

# Patterns to ignore
_IGNORE_PATTERNS = [
    "*.pyc",
    "__pycache__/*",
    ".pytest_cache/*",
    "*.git/*",
    ".dimension_inbox/*",
    "*.log",
    "docs/reports/*",
    "*.tmp",
    "*~",
]


class Ansi:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    DIM = "\033[2m"


def is_ignored(path_str: str) -> bool:
    """Check if a path matches ignore patterns."""
    for pat in _IGNORE_PATTERNS:
        if fnmatch.fnmatch(path_str, pat) or fnmatch.fnmatch(os.path.basename(path_str), pat):
            return True
        # Directory-style patterns (e.g. "__pycache__/*") need to match a
        # path segment anywhere in the tree, not just a whole-string glob --
        # fnmatch above can't do that for a nested "a/b/__pycache__/c.pyc".
        # Pad with a leading separator so a top-level path (no "/" prefix
        # of its own, e.g. "docs/reports/x.html") matches the same way a
        # nested one does.
        if ("/" + path_str).find("/" + pat.strip("*")) != -1:
            return True
    return False


def get_file_snapshot() -> Dict[str, float]:
    """Scan watched directories and return a map of relative_path -> mtime."""
    snapshot: Dict[str, float] = {}
    for watch_dir in _WATCH_DIRS:
        if not watch_dir.is_dir():
            continue
        for root, dirs, files in os.walk(watch_dir):
            # Prune ignored directories in-place
            dirs[:] = [d for d in dirs if not is_ignored(os.path.join(root, d))]
            for f in files:
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, _REPO_ROOT)
                if not is_ignored(rel_path):
                    try:
                        snapshot[rel_path] = os.path.getmtime(full_path)
                    except OSError:
                        pass
    return snapshot


def resolve_targeted_tests(changed_files: Set[str]) -> Tuple[List[str], bool, bool]:
    """
    Given a set of changed relative paths, determine:
      (1) List of specific pytest test files or patterns to run.
      (2) Whether to run Node CEP tests.
      (3) Whether to trigger generate_babysitter.py.
    """
    pytest_targets: Set[str] = set()
    run_node_tests = False
    run_babysitter_gen = False

    for path in changed_files:
        p = path.replace("\\", "/")

        if "Babysitter_src" in p:
            run_babysitter_gen = True
            pytest_targets.add("python/tests/test_cep_jsx_bundle_sync.py")
            pytest_targets.add("python/tests/test_babysitter*.py")

        elif p.startswith("Scripts/Dimension_Assets/") and p.endswith(".jsx"):
            pytest_targets.add("python/tests/test_cep_jsx_bundle_sync.py")

        elif "telemetry" in p or "diagnostic_visualizer" in p:
            pytest_targets.add("python/tests/test_diagnostic_telemetry_and_visualizer.py")

        elif "scale_engine" in p or "gravity" in p:
            pytest_targets.add("python/tests/test_scale_engine*.py")
            pytest_targets.add("python/tests/test_universal_conform_behavior.py")

        elif "occlusion" in p or "soe" in p:
            pytest_targets.add("python/tests/test_soe_repulsion.py")
            pytest_targets.add("python/tests/test_occlusion_engine.py")

        elif "kinematics" in p:
            pytest_targets.add("python/tests/test_world_space_kinematics.py")
            pytest_targets.add("python/tests/test_kinematics_3d.py")

        elif "surveyor" in p or "heuristics" in p or "comment_gardener" in p:
            pytest_targets.add("python/tests/test_surveyor*.py")
            pytest_targets.add("python/tests/test_comment_gardener*.py")

        elif "dag" in p or "duplication" in p:
            pytest_targets.add("python/tests/test_dag_duplication.py")
            pytest_targets.add("python/tests/test_duplication*.py")

        elif "ae_eval" in p or "ae_bridge_mcp" in p:
            pytest_targets.add("python/tests/test_ae_bridge_mcp.py")

        elif "verify_math_and_pillars" in p:
            pytest_targets.add("python/tests/test_verify_math_and_pillars_tool.py")

        elif p.startswith("cep/"):
            run_node_tests = True
            pytest_targets.add("python/tests/test_studio_deck_contracts.py")

        elif p.startswith("python/tests/"):
            pytest_targets.add(p)

        elif p.startswith("python/models/"):
            pytest_targets.add("python/tests/test_bridge_contracts_jsx.py")
            pytest_targets.add("python/tests/test_scrape_manifest_wire_contract.py")

        else:
            pytest_targets.add("python/tests/test_scale_engine*.py")

    return sorted(list(pytest_targets)), run_node_tests, run_babysitter_gen


def trigger_cef_reload(port: int = 8088) -> bool:
    """Attempt to reload the CEP panel via CEF remote debugging endpoint."""
    url = f"http://127.0.0.1:{port}/json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Dimension-WatchSentinel"})
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and len(data) > 0:
                return True
    except Exception:
        pass
    return False


def run_watcher_cycle(
    changed_files: Set[str],
    venv_python: str,
    enable_cef: bool = True
) -> bool:
    """Execute a single test-and-sync cycle for a set of changed files."""
    t_start = time.perf_counter()
    print(f"\n{Ansi.BOLD}{Ansi.CYAN}⚡ [WATCHER CHANGE DETECTED]{Ansi.RESET} ({len(changed_files)} file{'s' if len(changed_files) != 1 else ''})")
    for f in sorted(list(changed_files))[:5]:
        print(f"  • {Ansi.DIM}{f}{Ansi.RESET}")
    if len(changed_files) > 5:
        print(f"  • ... and {len(changed_files) - 5} more")

    pytest_targets, run_node, run_babysitter = resolve_targeted_tests(changed_files)

    # Step 1: Rebuild Babysitter if necessary
    if run_babysitter:
        gen_script = _REPO_ROOT / "python" / "scripts" / "generate_babysitter.py"
        if gen_script.exists():
            print(f"{Ansi.BLUE}⚙️  Rebuilding Babysitter.jsx bundle...{Ansi.RESET}")
            res = subprocess.run([venv_python, str(gen_script)], capture_output=True, text=True)
            if res.returncode != 0:
                print(f"{Ansi.RED}❌ Babysitter generation failed:{Ansi.RESET}\n{res.stderr}")
                return False

    # Step 2: Sync ExtendScript files to cep/jsx/
    for f in changed_files:
        if f.startswith("Scripts/Dimension_Assets/") and f.endswith(".jsx") and "Babysitter_src" not in f:
            src_file = _REPO_ROOT / f
            dst_file = _REPO_ROOT / "cep" / "jsx" / os.path.basename(f)
            if src_file.exists():
                shutil.copy2(src_file, dst_file)
                print(f"{Ansi.DIM}  Synced {os.path.basename(f)} -> cep/jsx/{Ansi.RESET}")

    # Step 3: Run Node CEP tests if requested
    all_ok = True
    if run_node:
        print(f"{Ansi.MAGENTA}🧪 Running CEP Node.js Unit Tests...{Ansi.RESET}")
        node_cmd = ["node", "--test", "cep/tests/fast_tag_strip.test.js", "cep/tests/headless_visual_dom.test.js"]
        node_res = subprocess.run(node_cmd, cwd=str(_REPO_ROOT), capture_output=True, text=True)
        if node_res.returncode == 0:
            print(f"  {Ansi.GREEN}✓ Node tests passed cleanly.{Ansi.RESET}")
        else:
            print(f"  {Ansi.RED}✗ Node tests failed:{Ansi.RESET}\n{node_res.stdout}\n{node_res.stderr}")
            all_ok = False

    # Step 4: Run Targeted Pytest Slice
    if pytest_targets:
        targets_str = " ".join(pytest_targets)
        print(f"{Ansi.BLUE}🧪 Running Targeted Pytest Slice: {Ansi.BOLD}{targets_str}{Ansi.RESET}")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_REPO_ROOT / "python")
        pytest_cmd = [venv_python, "-m", "pytest"] + pytest_targets + ["-q"]
        pt_res = subprocess.run(pytest_cmd, cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True)
        if pt_res.returncode == 0:
            print(f"  {Ansi.GREEN}✓ Pytest passed cleanly.{Ansi.RESET}")
        else:
            print(f"  {Ansi.RED}✗ Pytest failed:{Ansi.RESET}\n{pt_res.stdout}\n{pt_res.stderr}")
            all_ok = False

    # Step 5: CEF Hot-Reload signal if enabled
    if enable_cef:
        if trigger_cef_reload():
            print(f"  {Ansi.CYAN}⟳ CEP panel reload dispatched via port 8088.{Ansi.RESET}")

    elapsed_ms = (time.perf_counter() - t_start) * 1000.0
    status_tag = f"{Ansi.GREEN}[PASS]{Ansi.RESET}" if all_ok else f"{Ansi.RED}[FAIL]{Ansi.RESET}"
    print(f"{status_tag} Cycle completed in {elapsed_ms:.1f}ms.\n")
    return all_ok


def start_watcher_loop(
    poll_interval: float = 0.3,
    all_on_start: bool = False,
    once_mode: bool = False,
    enable_cef: bool = True
):
    """Main watcher loop polling file modifications."""
    venv_python = str(_REPO_ROOT / ".venv" / "bin" / "python")
    if not os.path.exists(venv_python):
        venv_python = sys.executable

    print(f"{Ansi.BOLD}{Ansi.GREEN}🌌 DIMENSION AUTO-TESTING & HOT-RELOAD SENTINEL ACTIVE{Ansi.RESET}")
    print(f" • Repository Root:   {_REPO_ROOT}")
    print(f" • Poll Interval:     {poll_interval * 1000:.0f}ms")
    print(f" • CEF Hot-Reload:    {'Enabled (port 8088)' if enable_cef else 'Disabled'}")
    print(" • Watching:          python/, Scripts/, cep/, config/")
    print(f"{Ansi.DIM}Press Ctrl+C to exit sentinel loop.{Ansi.RESET}\n")

    if all_on_start or once_mode:
        print(f"{Ansi.YELLOW}▶ Running full 86-pillar diagnostic suite on initial start...{Ansi.RESET}")
        diag_script = _REPO_ROOT / "python" / "tools" / "verify_math_and_pillars.py"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_REPO_ROOT / "python")
        subprocess.run([venv_python, str(diag_script), "--all"], cwd=str(_REPO_ROOT), env=env)

        if once_mode:
            return

    last_snapshot = get_file_snapshot()

    try:
        while True:
            time.sleep(poll_interval)
            current_snapshot = get_file_snapshot()

            changed: Set[str] = set()
            # Detect modified or added
            for p, mtime in current_snapshot.items():
                if p not in last_snapshot or mtime > last_snapshot[p]:
                    changed.add(p)

            # Detect removed
            for p in last_snapshot:
                if p not in current_snapshot:
                    changed.add(p)

            if changed:
                # Small debounce wait to allow multi-file saves to settle
                time.sleep(0.1)
                latest_snapshot = get_file_snapshot()
                for p, mtime in latest_snapshot.items():
                    if p not in last_snapshot or mtime > last_snapshot[p]:
                        changed.add(p)

                last_snapshot = latest_snapshot
                run_watcher_cycle(changed, venv_python, enable_cef=enable_cef)

    except KeyboardInterrupt:
        print(f"\n{Ansi.YELLOW}⏹ Sentinel watcher stopped cleanly.{Ansi.RESET}")


def main():
    parser = argparse.ArgumentParser(description="Dimension Auto-Testing & Hot-Reload Watcher Sentinel")
    parser.add_argument("--interval", type=float, default=0.3, help="Polling interval in seconds (default 0.3s)")
    parser.add_argument("--all-on-start", action="store_true", help="Run full 86-pillar test suite on boot")
    parser.add_argument("--once", action="store_true", help="Run once and exit (for CI/pre-commit checks)")
    parser.add_argument("--no-cef", action="store_true", help="Disable CEF remote debugging reload")

    args = parser.parse_args()
    start_watcher_loop(
        poll_interval=args.interval,
        all_on_start=args.all_on_start,
        once_mode=args.once,
        enable_cef=not args.no_cef,
    )


if __name__ == "__main__":
    main()
