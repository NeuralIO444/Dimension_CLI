# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/lut_parser.py
Color Match (Track D) — Pure Python 3D LUT ingest engine.

Parses Iridas/Adobe `.cube` and Discreet Flame `.3dl` 3D LUT files into
a normalized, immutable `LutData` structure. This module has zero AE
dependency and never touches the AE project, the scrape manifest, or
the conform pipeline — it is a standalone ingest step that runs before
a LUT is ever handed to the (separate) Color Match inject bridge.

Format notes
------------
`.cube` (Iridas/Adobe spec):
  - `LUT_3D_SIZE N` declares a cubic NxNxN grid; `LUT_1D_SIZE` (a 1D
    LUT) is explicitly rejected — Color Match requires a 3D grade.
  - `DOMAIN_MIN` / `DOMAIN_MAX` default to (0,0,0) / (1,1,1) when
    absent. Scene-linear LUTs may declare a domain max > 1.0; those
    values are preserved verbatim, never clamped.
  - Body rows are whitespace-separated float triplets in standard
    cube ordering (R fastest-changing).

`.3dl` (Discreet Flame spec, as exchanged with colorists):
  - Header is either a bare integer (`N`, implicit NxNxN) or a
    `Mesh N N N` line. A non-cubic mesh (`Mesh X Y Z` with differing
    dimensions) is rejected — Dimension only supports cubic grids.
  - An optional two-token shaper/range line (e.g. `0 1023`) may
    follow the header; it is informational and skipped. Bit depth is
    NOT read from this line — it is auto-detected from the largest
    integer actually observed in the body, since some exports omit
    or mis-declare the range.
  - Any header that is neither a bare integer nor `Mesh N N N` is an
    unrecognized/legacy Discreet variant (e.g. a true `3DMESH` point
    list) that Dimension does not parse; the caller is told to
    re-export as `.cube` instead.

Both formats reject NaN/Inf entries and non-decimal-period floats
(e.g. European comma decimals) with a descriptive `LutParseError` —
`ADBE Apply Color LUT 2` in AE will crash or render black frames on
NaN/Inf, so these must never reach the inject bridge.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


class LutParseError(ValueError):
    """Raised when a `.cube` or `.3dl` file cannot be parsed safely."""


@dataclass(frozen=True)
class LutData:
    """Normalized in-memory representation of a parsed 3D LUT."""

    source_format: str  # "cube" | "3dl"
    grid_size: int
    domain_min: Tuple[float, float, float]
    domain_max: Tuple[float, float, float]
    table: Tuple[Tuple[float, float, float], ...]
    bit_depth: Optional[int] = None
    divisor: Optional[float] = None
    title: Optional[str] = None


_MESH_HEADER_RE = re.compile(r"^Mesh\s+(\d+)\s+(\d+)\s+(\d+)$", re.IGNORECASE)
_TITLE_RE = re.compile(r'^TITLE\s+"(.*)"$', re.IGNORECASE)

# (divisor, bit_depth), smallest range first — first one that covers the
# observed integer max wins.
_KNOWN_3DL_DIVISORS: Tuple[Tuple[float, int], ...] = (
    (1023.0, 10),
    (4095.0, 12),
    (65535.0, 16),
)


def _read_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise LutParseError(f"Could not read {label} file '{path}': {exc}") from exc


def _to_float(token: str, line_num: int) -> float:
    try:
        value = float(token)
    except ValueError as exc:
        raise LutParseError(
            f"Invalid float formatting on line {line_num} — expected decimal period '.'"
        ) from exc
    if math.isnan(value) or math.isinf(value):
        raise LutParseError(
            f"Malformed numerical entry (NaN or Inf) detected on line {line_num}"
        )
    return value


def _parse_float_triplet(line: str, line_num: int, keyword: str) -> Tuple[float, float, float]:
    parts = line.split()[1:]
    if len(parts) != 3:
        raise LutParseError(f"Malformed {keyword} header on line {line_num}")
    values = tuple(_to_float(p, line_num) for p in parts)
    return values  # type: ignore[return-value]


def _parse_data_row(line: str, line_num: int) -> Tuple[float, float, float]:
    parts = line.split()
    if len(parts) != 3:
        raise LutParseError(
            f"Expected 3 numeric columns on line {line_num}, found {len(parts)}"
        )
    r, g, b = (_to_float(p, line_num) for p in parts)
    return (r, g, b)


def parse_cube(path: Path) -> LutData:
    """Parse an Iridas/Adobe `.cube` 3D LUT."""
    path = Path(path)
    text = _read_text(path, ".cube")

    grid_size: Optional[int] = None
    domain_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    domain_max: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    title: Optional[str] = None
    table: List[Tuple[float, float, float]] = []

    for line_num, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        upper = line.upper()
        if upper.startswith("TITLE"):
            match = _TITLE_RE.match(line)
            title = match.group(1) if match else None
            continue
        if upper.startswith("LUT_1D_SIZE"):
            raise LutParseError(
                "1D LUTs (LUT_1D_SIZE) are not supported — Color Match requires a "
                "3D LUT (LUT_3D_SIZE)"
            )
        if upper.startswith("LUT_3D_SIZE"):
            parts = line.split()
            if len(parts) != 2 or not parts[1].isdigit():
                raise LutParseError(f"Malformed LUT_3D_SIZE header on line {line_num}")
            grid_size = int(parts[1])
            continue
        if upper.startswith("DOMAIN_MIN"):
            domain_min = _parse_float_triplet(line, line_num, "DOMAIN_MIN")
            continue
        if upper.startswith("DOMAIN_MAX"):
            domain_max = _parse_float_triplet(line, line_num, "DOMAIN_MAX")
            continue

        table.append(_parse_data_row(line, line_num))

    if grid_size is None:
        raise LutParseError("Missing required LUT_3D_SIZE header")

    expected_rows = grid_size ** 3
    if len(table) != expected_rows:
        raise LutParseError(
            f"Expected {expected_rows} rows for LUT_3D_SIZE {grid_size}, found {len(table)}"
        )

    return LutData(
        source_format="cube",
        grid_size=grid_size,
        domain_min=domain_min,
        domain_max=domain_max,
        table=tuple(table),
        title=title,
    )


def _infer_3dl_bit_depth(observed_max: int) -> Tuple[int, float]:
    for divisor, bit_depth in _KNOWN_3DL_DIVISORS:
        if observed_max <= divisor:
            return bit_depth, divisor
    raise LutParseError(
        f"Integer value {observed_max} exceeds the known 16-bit .3dl range (0-65535)"
    )


def parse_3dl(path: Path) -> LutData:
    """Parse a Discreet Flame `.3dl` 3D LUT."""
    path = Path(path)
    text = _read_text(path, ".3dl")

    numbered_lines = [
        (i, stripped)
        for i, raw in enumerate(text.splitlines(), start=1)
        if (stripped := raw.strip())
    ]
    if not numbered_lines:
        raise LutParseError("Empty .3dl file")

    header_num, header = numbered_lines[0]
    mesh_match = _MESH_HEADER_RE.match(header)
    if mesh_match:
        dims = tuple(int(d) for d in mesh_match.groups())
        if len(set(dims)) != 1:
            raise LutParseError(
                f"Non-cubic 3D LUT mesh {dims[0]}x{dims[1]}x{dims[2]} is not "
                "supported — Dimension requires an NxNxN cubic grid"
            )
        grid_size = dims[0]
    elif header.isdigit():
        grid_size = int(header)
    else:
        raise LutParseError(
            "Unrecognized .3dl header format on line "
            f"{header_num} — please re-export as .cube for Dimension compatibility"
        )

    body_start = 1
    if len(numbered_lines) > 1 and len(numbered_lines[1][1].split()) == 2:
        # Optional shaper/range line (e.g. "0 1023") — informational only;
        # bit depth is auto-detected from the body, not this line.
        body_start = 2

    body_lines = numbered_lines[body_start:]
    expected_rows = grid_size ** 3
    if len(body_lines) != expected_rows:
        raise LutParseError(
            f"Expected {expected_rows} rows for a {grid_size}x{grid_size}x{grid_size} "
            f"mesh, found {len(body_lines)}"
        )

    int_rows: List[Tuple[int, int, int]] = []
    observed_max = 0
    for line_num, line in body_lines:
        parts = line.split()
        if len(parts) != 3:
            raise LutParseError(
                f"Expected 3 numeric columns on line {line_num}, found {len(parts)}"
            )
        try:
            r, g, b = (int(p) for p in parts)
        except ValueError as exc:
            raise LutParseError(
                f"Invalid integer entry on line {line_num} — .3dl tables must use "
                "integers"
            ) from exc
        observed_max = max(observed_max, r, g, b)
        int_rows.append((r, g, b))

    bit_depth, divisor = _infer_3dl_bit_depth(observed_max)
    table = tuple((r / divisor, g / divisor, b / divisor) for r, g, b in int_rows)

    return LutData(
        source_format="3dl",
        grid_size=grid_size,
        domain_min=(0.0, 0.0, 0.0),
        domain_max=(1.0, 1.0, 1.0),
        table=table,
        bit_depth=bit_depth,
        divisor=divisor,
    )


def load_lut(path: Path) -> LutData:
    """Dispatch to `parse_cube` / `parse_3dl` based on file extension."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".cube":
        return parse_cube(path)
    if suffix == ".3dl":
        return parse_3dl(path)
    raise LutParseError(
        f"Unsupported LUT file extension '{suffix}' — expected .cube or .3dl"
    )
