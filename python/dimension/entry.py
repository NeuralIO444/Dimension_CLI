# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Console entry for `dimension`.

Wraps `dimension.cli.main` with OSC 9;4 tab progress. The sequences go
to stderr and only when the terminal is Ghostty, WezTerm, Canario, or
Windows Terminal. `--json`, pipes, and CI stay silent so the machine
face remains one JSON document on stdout.
"""

from __future__ import annotations

import sys
from typing import Optional

from dimension.cli import main as cli_main
from dimension.osc_progress import create_osc_progress


def main(argv: Optional[list[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    label = next((a for a in args if not a.startswith("-")), "dimension")
    progress = create_osc_progress(label=label, disabled="--json" in args)
    progress.set_indeterminate(label)
    try:
        code = cli_main(argv)
    except Exception:
        progress.fail(label)
        raise
    if code == 0:
        progress.done(label)
    else:
        progress.fail(label)
    return code


if __name__ == "__main__":
    sys.exit(main())
