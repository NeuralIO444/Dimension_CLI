# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""`python -m dimension` — same entry as the `dimension` console script."""

import sys

from dimension.entry import main

if __name__ == "__main__":
    sys.exit(main())
