# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/orientation.py
Orientation buckets — the artist-facing shape of a single target.

Classifies ONE composition's dimensions into HORIZONTAL / SQUARE /
VERTICAL. Pure function, no side effects, no I/O, no dependency on the
source comp.

**This is not `aspect_strategy.py`, and it does not replace it.** The two
answer different questions and both are needed:

  - `aspect_strategy.classify(src, tgt)` answers *"what is the
    relationship between where we are and where we're going?"* —
    preserve / widen / narrow / equal-different-resolution. It is what
    the conform engine dispatches rule sets on. It is relative.
  - `orientation_bucket(w, h)` answers *"what shape is this target?"* —
    the thing a designer says out loud ("the vertical", "the square").
    It is absolute: TikTok is VERTICAL whether the master was 16:9 or
    4:5.

Conform math must keep using `aspect_strategy`. Buckets exist for two
jobs it cannot do:

  1. **Labelling.** A badge in the Format Queue and a line in the conform
     report that reads the way the artist thinks. `narrow` is precise and
     means nothing to someone looking at a preset list.
  2. **Addressing a target set by shape.** The comment directives planned
     in `docs/roadmap/2026-09-01-hardcore-audit-and-amd-variants-nudge-plan.md`
     (§3) — `variant:vertical`, `nudge:-40,20@vertical` — need a name for
     "every 9:16-ish target" that does not depend on what the master comp
     was. A relative strategy label cannot express that: HD→TikTok and
     4:5→TikTok are different strategies (`narrow` vs `narrow` at very
     different deltas) but the same bucket, and the artist means the
     bucket.

Thresholds live in `config/constants.py` (`ORIENTATION_UPPER_AR` /
`ORIENTATION_LOWER_AR`), defaulting to 1.2 and 0.8. Those defaults are
inherited from the Adaptive Motion Design breakpoint convention and are
deliberately wide: 4:5 portrait (0.8) and 5:4 (1.25) sit right at the
edges, and everything between reads as "squarish" to a designer. They are
module-level constants rather than per-profile settings for now — making
them profile-overridable is a real feature, but it needs a UI and a
migration story, so it is not in this slice.

Boundary convention (matches the AMD spec exactly):

    HORIZONTAL : ar >= 1.2
    SQUARE     : 0.8  <  ar  <  1.2
    VERTICAL   : ar <= 0.8

Both boundaries are inclusive toward the outer buckets, so exactly 0.8
(4:5) is VERTICAL and exactly 1.2 (6:5) is HORIZONTAL. 1:1 is SQUARE.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from config.constants import ORIENTATION_LOWER_AR, ORIENTATION_UPPER_AR


class Orientation(str, Enum):
    """Shape of a single composition. `str` mixin so the value serializes
    straight into JSON manifests and CEP payloads without a converter —
    same pattern as `AspectStrategy`."""

    HORIZONTAL = "HORIZONTAL"
    SQUARE = "SQUARE"
    VERTICAL = "VERTICAL"


@dataclass(frozen=True)
class OrientationResult:
    """Pure data record. `aspect_ratio` is width / height."""

    orientation: Orientation
    aspect_ratio: float
    width: int
    height: int


def orientation_bucket(width: int, height: int) -> Orientation:
    """Classify one composition's dimensions into an orientation bucket.

    Args:
        width, height: composition dimensions in pixels (must be > 0)

    Returns:
        The Orientation bucket. Never raises except on invalid dimensions.

    Raises:
        ValueError: if either dimension is non-positive.
    """
    return classify(width, height).orientation


def classify(width: int, height: int) -> OrientationResult:
    """`orientation_bucket` plus the diagnostic values behind it.

    Callers that only need the label should use `orientation_bucket`;
    this exists for report/telemetry surfaces that want to show the
    aspect ratio alongside the bucket name.
    """
    if width <= 0 or height <= 0:
        raise ValueError(
            f"Both dimensions must be positive; got {width}x{height}"
        )

    ar = width / height

    if ar >= ORIENTATION_UPPER_AR:
        bucket = Orientation.HORIZONTAL
    elif ar <= ORIENTATION_LOWER_AR:
        bucket = Orientation.VERTICAL
    else:
        bucket = Orientation.SQUARE

    return OrientationResult(
        orientation=bucket,
        aspect_ratio=ar,
        width=width,
        height=height,
    )


def display_label(orientation: Orientation) -> str:
    """Short label for dense UI rows (badges, pills, table cells).

    Kept here rather than in the CEP layer so the JS mirror and the HTML
    report cannot drift apart on wording — the same reasoning as
    `tag_registry`'s abbreviations.
    """
    return {
        Orientation.HORIZONTAL: "HORIZ",
        Orientation.SQUARE: "SQ",
        Orientation.VERTICAL: "VERT",
    }[orientation]
