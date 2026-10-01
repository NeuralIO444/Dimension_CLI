# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""Shared fixtures for all tests under python/tests/.

The Dimension AE pytest plugin (pytest_dimension_ae) was dropped in the
Dimension_CLI extraction (issue #2): it is an AE-live-only harness and
this repo has no After Effects. AE-gated tests keep their explicit
`ae_live` / `ae_capture` markers (registered in pytest.ini); CI skips
them via `-m "not ae_live and not ae_capture"`.
"""
