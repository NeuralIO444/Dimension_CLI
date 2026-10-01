# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/config/constants.py
Centralized Engine Constants & System Invariants (v6.2 — Issue #295).

Single source of truth for numerical tolerances, hardware limits,
chunk sizes, retry envelopes, and default dimensions across all Dimension subsystems.
"""

from __future__ import annotations
from typing import Final

# ── 1. Geometric & Floating-Point Tolerances ───────────────────────────────
DEFAULT_EPSILON: Final[float] = 1e-4
ROTATION_EPSILON_DEG: Final[float] = 1e-6
EDGE_TOLERANCE_PX: Final[float] = 1e-9
SUBPIXEL_AUDIT_TOLERANCE_PX: Final[float] = 0.05
HIGHRES_AUDIT_TOLERANCE_PX: Final[float] = 0.21

# ── 2. Hardware & Composition Resolution Limits ────────────────────────────
MAX_GPU_TEXTURE_PX: Final[int] = 16384           # Hardware 16K max texture dimension
MAX_AE_COMP_DIMENSION_PX: Final[int] = 30000     # After Effects hard ceiling
EXTREME_ASPECT_RATIO_THRESHOLD: Final[float] = 10.0
BYTES_PER_PIXEL_8BPC: Final[int] = 4
BYTES_PER_PIXEL_32BPC: Final[int] = 16

# ── 3. Spatial Occlusion Engine (SOE) Repulsion ────────────────────────────
SOE_REPULSION_MARGIN_PX: Final[float] = 16.0
SOE_REPULSION_FLOOR_PX: Final[float] = 8.0
SOE_MAX_ITERATIONS: Final[int] = 100

# ── 4. Pipeline & Babysitter Injection Slicing ─────────────────────────────
DEFAULT_CHUNK_SIZE: Final[int] = 30
MAX_CHUNK_SIZE: Final[int] = 100
BABYSITTER_UNDO_GROUP_NAME: Final[str] = "Dimension Conform Injection"
ORPHAN_CLEANUP_UNDO_GROUP_NAME: Final[str] = "Clean Dimension Orphan Comps"

# ── 5. Server, Watchdog & NTFS File-System Retries ─────────────────────────
DEFAULT_SERVER_PORT: Final[int] = 4444
DEFAULT_WATCHDOG_POLL_INTERVAL_S: Final[float] = 1.5
DEFAULT_WATCHDOG_NAME: Final[str] = "After Effects"
READ_MAX_ATTEMPTS: Final[int] = 5
READ_INITIAL_DELAY_S: Final[float] = 0.02
TOTAL_READ_RETRY_WINDOW_S: Final[float] = 0.30

# ── 6. Orientation Buckets (artist-facing target shape) ────────────────────
# Boundaries for core/orientation.py's HORIZONTAL / SQUARE / VERTICAL
# classification of a single target, inclusive toward the outer buckets:
#   HORIZONTAL: ar >= UPPER   SQUARE: LOWER < ar < UPPER   VERTICAL: ar <= LOWER
# Defaults follow the Adaptive Motion Design breakpoint convention and are
# deliberately wide — 4:5 (0.80) and 5:4 (1.25) sit at the edges, and a
# designer reads everything between as "squarish". These are NOT the
# conform engine's dispatch thresholds; that is aspect_strategy.EPS, which
# answers a different (relative) question.
ORIENTATION_UPPER_AR: Final[float] = 1.2
ORIENTATION_LOWER_AR: Final[float] = 0.8

# ── 7. Typographic DNA & Heuristics ─────────────────────────────────────────
TYPO_FOOTNOTE_MAX_PT: Final[float] = 24.0
TYPO_TITLE_MIN_PT: Final[float] = 48.0
