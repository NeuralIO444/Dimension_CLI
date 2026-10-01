# (c) 2026 NeuralIO 444
# See LICENSE for full terms.

"""Temporary `dimension` console-script entry point (issue #3).

The unified `dimension` CLI tree lands in issue #5; until then this stub
delegates to the legacy flat `cli.py` dispatcher so that
`pip install -e .` produces a working `dimension` command.
"""

from cli import main

if __name__ == "__main__":
    main()
