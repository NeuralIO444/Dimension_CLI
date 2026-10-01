# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/tests/harness/color_verifier.py
Color Match (Track D) — synthetic LUT-application reference harness.

`core/lut_parser.py` proves a `.cube`/`.3dl` file parses into a
`LutData` table correctly. Nothing anywhere proves that table is
mathematically USABLE — that trilinearly interpolating through it
produces the color transform the LUT actually encodes. The real
application happens inside AE's `ADBE Apply Color LUT 2` effect,
which this harness cannot reach (no AE in CI); what it can prove,
synthetically, is that a parsed `LutData` behaves the way a 3D LUT is
specified to behave in the IES/CLF/Adobe .cube reference model:
trilinear interpolation over an NxNxN lattice, domain-scaled, clamped
at the edges.

This is deliberately a `tests/harness/` module, not a `core/` one —
it exists to let tests construct known-transform LUTs and assert
exact expected outputs, not to give the product a new LUT-preview
capability. See CLAUDE.md's reachability-gate note: modules under
`python/tests/` are classified `kind="test"` and never enter the
production-reachability audit, so this carries no allowlist burden.

Pure Python, no numpy/Pillow — the transform math only needs to run
against a handful of synthetic pixels per test, not a full image, and
keeping it dependency-free avoids any question of whether a new
library needs approval for a test-only file.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from core.lut_parser import LutData

RGB = Tuple[float, float, float]


def _clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _cell_index(grid_size: int, r_idx: int, g_idx: int, b_idx: int) -> int:
    # Matches lut_parser's documented "standard cube ordering (R
    # fastest-changing)": index = r + g*N + b*N*N.
    return r_idx + g_idx * grid_size + b_idx * grid_size * grid_size


def apply_lut_to_pixel(lut: LutData, rgb: RGB) -> RGB:
    """Trilinearly interpolate one RGB triplet through `lut.table`.

    Normalizes `rgb` into [0, grid_size-1] lattice coordinates using
    `lut.domain_min`/`lut.domain_max`, clamping to the lattice edges —
    a 3D LUT does not extrapolate past its declared domain, it holds
    the edge value, same as AE's own LUT application. Then blends the
    8 surrounding lattice corners by their trilinear weights.
    """
    n = lut.grid_size
    scale = n - 1

    lattice_coords = []
    for axis in range(3):
        dmin = lut.domain_min[axis]
        dmax = lut.domain_max[axis]
        span = dmax - dmin
        t = 0.0 if span == 0 else (rgb[axis] - dmin) / span
        lattice_coords.append(_clamp(t, 0.0, 1.0) * scale)

    r_c, g_c, b_c = lattice_coords
    r0, g0, b0 = int(r_c), int(g_c), int(b_c)
    # Guard the top edge: a coordinate landing exactly on the last
    # lattice point (t == 1.0) must not index one past the table.
    r1, g1, b1 = min(r0 + 1, scale), min(g0 + 1, scale), min(b0 + 1, scale)
    fr, fg, fb = r_c - r0, g_c - g0, b_c - b0

    def corner(ri: int, gi: int, bi: int) -> RGB:
        return lut.table[_cell_index(n, ri, gi, bi)]

    c000, c100 = corner(r0, g0, b0), corner(r1, g0, b0)
    c010, c110 = corner(r0, g1, b0), corner(r1, g1, b0)
    c001, c101 = corner(r0, g0, b1), corner(r1, g0, b1)
    c011, c111 = corner(r0, g1, b1), corner(r1, g1, b1)

    out: List[float] = []
    for ch in range(3):
        c00 = c000[ch] * (1 - fr) + c100[ch] * fr
        c10 = c010[ch] * (1 - fr) + c110[ch] * fr
        c01 = c001[ch] * (1 - fr) + c101[ch] * fr
        c11 = c011[ch] * (1 - fr) + c111[ch] * fr
        c0 = c00 * (1 - fg) + c10 * fg
        c1 = c01 * (1 - fg) + c11 * fg
        out.append(c0 * (1 - fb) + c1 * fb)
    return (out[0], out[1], out[2])


def apply_lut_to_pixels(lut: LutData, pixels: Iterable[RGB]) -> List[RGB]:
    """Convenience batch form of `apply_lut_to_pixel`."""
    return [apply_lut_to_pixel(lut, p) for p in pixels]


def build_synthetic_lut(
    grid_size: int,
    transform,
    domain_min: Sequence[float] = (0.0, 0.0, 0.0),
    domain_max: Sequence[float] = (1.0, 1.0, 1.0),
) -> LutData:
    """Constructs a `LutData` whose grid points are `transform(r, g, b)`
    sampled at each of the `grid_size**3` lattice positions, in the
    same R-fastest ordering `lut_parser` produces from a real file.

    `transform` receives normalized [0, 1] grid coordinates (i.e. the
    lattice position, NOT a domain-scaled color) and must return an
    (r, g, b) triplet. This mirrors how `.cube`/`.3dl` files declare
    their body rows — the file format has no notion of "the transform
    function", only its samples — so tests build known-transform LUTs
    exactly the way a real export would encode them.
    """
    scale = grid_size - 1
    table: List[RGB] = []
    for b_idx in range(grid_size):
        for g_idx in range(grid_size):
            for r_idx in range(grid_size):
                r = r_idx / scale if scale else 0.0
                g = g_idx / scale if scale else 0.0
                b = b_idx / scale if scale else 0.0
                table.append(transform(r, g, b))
    return LutData(
        source_format="cube",
        grid_size=grid_size,
        domain_min=tuple(domain_min),
        domain_max=tuple(domain_max),
        table=tuple(table),
    )
