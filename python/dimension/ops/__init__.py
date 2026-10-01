# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""dimension.ops — pure engine operations for the CLI (and the future TUI).

Every function here takes plain arguments and returns a
JSON-serializable dict. No printing, no sys.argv, no argparse.
`dimension.cli` handles argument parsing and output formatting;
issue #15's Textual TUI will call these directly.
"""
