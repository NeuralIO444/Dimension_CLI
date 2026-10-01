# (c) 2026 NeuralIO 444
# Licensed under PolyForm Noncommercial 1.0.0 + commercial.
# See LICENSE for full terms.

"""`python -m dimension` — same entry as the `dimension` console script."""

import sys

from dimension.cli import main

if __name__ == "__main__":
    sys.exit(main())
