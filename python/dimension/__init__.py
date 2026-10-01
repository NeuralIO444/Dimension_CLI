# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial.
# See LICENSE for full terms.

"""Dimension — unified `dimension` CLI (issue #5).

Two faces, one engine:

- **Human face** (default): readable summaries on stdout. The future
  Textual TUI (issue #15) calls the same `dimension.ops` functions.
- **Machine face** (`--json`): exactly one JSON document on stdout,
  zero decorative output, all logs on stderr, meaningful exit codes —
  built for MographJailed's locked Sequoia zsh interface, which
  invokes `dimension` as a subprocess.

Ops live in `dimension.ops` and are pure: they take arguments, return
JSON-serializable dicts, and never print. Argument parsing and output
formatting live in `dimension.cli`. Keep it that way — the TUI imports
ops, never the CLI handlers.
"""

__version__ = "0.1.0"
