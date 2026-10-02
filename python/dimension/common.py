# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial.
# See LICENSE for full terms.

"""Shared plumbing for the `dimension` CLI: exit codes, the JSON
machine-face contract, and the ops error type.

Exit codes (documented in `dimension --help`):
  0   OK
  1   runtime error (bad input, missing file, engine failure)
  2   usage error (argparse)
  65  LUT_UNSCRIPTABLE — the requested op is impossible on AE's
      scripting platform (Dimension #494). Distinct from 1 so
      callers can branch on it.
  69  AE_UNAVAILABLE — a live-AE command ran with no After Effects
      poller reachable. Distinct from 1 so scripts can prompt the
      user to launch AE instead of reporting a bug.
"""

from __future__ import annotations

import json
import logging
from typing import Any

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2  # argparse's own default
EXIT_UNSCRIPTABLE = 65  # EX_DATAERR: real op, impossible platform
EXIT_AE_UNAVAILABLE = 69  # live-AE command, no AE reachable


class DimensionError(Exception):
    """Ops raise this; the CLI layer turns it into output + exit code.

    Never raised for usage mistakes (argparse owns those) — only for
    failures the op itself detects: missing files, engine errors,
    unreachable AE, platform limitations.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "ERROR",
        exit_code: int = EXIT_ERROR,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


def emit_json(payload: Any) -> None:
    """Machine face: exactly one JSON document on stdout, nothing else."""
    print(json.dumps(payload, indent=2, default=str))


def emit_human(text: str) -> None:
    """Human face: plain readable text on stdout."""
    print(text)


def log_to_stderr(level: str = "INFO") -> None:
    """Route the engine logger to stderr at the requested level.

    The engine's structured logger already writes to stderr; this just
    sets the level from the CLI's --log-level flag.
    """
    logging.getLogger("dimension").setLevel(getattr(logging, level.upper(), logging.INFO))


def error_payload(err: DimensionError) -> dict:
    return {"status": "ERROR", "code": err.code, "error": str(err)}
