# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/occlusion/mask_solver.py
Safe-zone mask loader, distance transform field generator, and AABB/OBB raster classifier.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
import cv2  # type: ignore
import numpy as np  # type: ignore

from core.logger import log
from core.spatial_math import (
    aabb_of,
    footprint_mask,
    is_rotated,
    local_point_to_world,
    obb_corners,
)
from core.occlusion.constants import (
    MaskZones,
    OcclusionMaskLoadError,
    THRESH_CUTOFF_MAX,
    THRESH_GO_MIN,
    THRESH_NUDGE_HI,
    THRESH_NUDGE_LO,
)


_MaskCacheKey = Tuple[str, float, int, int]
_PNG_MASK_CACHE: Dict[_MaskCacheKey, "OcclusionMask"] = {}


class OcclusionMask:
    """Loads a mask (PNG path or in-memory ndarray), classifies into
    three zones, and precomputes the distance field SOE uses for
    solver lookups."""

    path: Optional[Path]
    comp_w: int
    comp_h: int
    zones: MaskZones
    distance_to_go: np.ndarray   # float32, pixel distance to nearest GO
    nearest_go_dy: np.ndarray    # int16, vertical delta to nearest GO
    nearest_go_dx: np.ndarray    # int16, horizontal delta to nearest GO

    def __init__(self, source: Union[Path, str, np.ndarray],
                 comp_w: int, comp_h: int):
        self.comp_w = int(comp_w)
        self.comp_h = int(comp_h)
        if self.comp_w <= 0 or self.comp_h <= 0:
            raise OcclusionMaskLoadError(
                f"Invalid comp dimensions: {self.comp_w}x{self.comp_h}"
            )

        if isinstance(source, np.ndarray):
            self.path = None
            raw = self._validate_ndarray(source)
            self._classify(raw)
            self._precompute_distance_field()
            return

        self.path = Path(source)

        cache_key: Optional[_MaskCacheKey] = None
        if self.path.is_file():
            try:
                mtime = self.path.stat().st_mtime
            except OSError:
                mtime = None
            if mtime is not None:
                cache_key = (str(self.path), mtime, self.comp_w, self.comp_h)

        if cache_key is not None:
            cached = _PNG_MASK_CACHE.get(cache_key)
            if cached is not None:
                self._adopt_cached_mask(cached)
                return

        raw = self._read_png()
        self._classify(raw)
        self._precompute_distance_field()
        if cache_key is not None:
            _PNG_MASK_CACHE[cache_key] = self

    def _read_png(self) -> np.ndarray:
        if not self.path.is_file():
            raise OcclusionMaskLoadError(f"Mask not found: {self.path}")
        raw = cv2.imread(str(self.path), cv2.IMREAD_GRAYSCALE)
        if raw is None:
            raise OcclusionMaskLoadError(
                f"cv2 returned None loading mask: {self.path}"
            )
        if raw.dtype != np.uint8:
            raw = raw.astype(np.uint8, copy=False)
        return raw

    def _adopt_cached_mask(self, cached: "OcclusionMask") -> None:
        self.zones = cached.zones
        self.distance_to_go = cached.distance_to_go
        self.nearest_go_dy = cached.nearest_go_dy
        self.nearest_go_dx = cached.nearest_go_dx
        log.info(
            "Safe-zone mask cache hit — reusing precomputed zones",
            extra={"path": self._describe_source()},
        )

    def _validate_ndarray(self, arr: np.ndarray) -> np.ndarray:
        if arr.ndim != 2:
            raise OcclusionMaskLoadError(
                f"Mask ndarray must be 2D grayscale; got ndim={arr.ndim}, "
                f"shape={list(arr.shape)}"
            )
        if arr.dtype != np.uint8:
            raise OcclusionMaskLoadError(
                f"Mask ndarray dtype must be uint8; got {arr.dtype}. "
                f"Strategies must produce uint8 0/255 arrays."
            )
        return arr

    def _describe_source(self) -> str:
        if self.path is not None:
            return str(self.path)
        return f"<derived: {self.comp_w}x{self.comp_h}>"

    def _classify(self, raw: np.ndarray) -> None:
        if raw.shape != (self.comp_h, self.comp_w):
            log.warning(
                "Safe-zone mask dimensions mismatch — resampling",
                extra={
                    "path": self._describe_source(),
                    "mask_shape": list(raw.shape),
                    "comp_shape": [self.comp_h, self.comp_w],
                },
            )
            raw = cv2.resize(
                raw, (self.comp_w, self.comp_h),
                interpolation=cv2.INTER_NEAREST,
            )

        go     = raw >= THRESH_GO_MIN
        cutoff = raw <= THRESH_CUTOFF_MAX
        nudge  = (raw >= THRESH_NUDGE_LO) & (raw <= THRESH_NUDGE_HI)
        leftover = ~(go | cutoff | nudge)
        nudge = nudge | leftover

        self.zones = MaskZones(go=go, nudge=nudge, cutoff=cutoff)
        log.info(
            "Safe-zone mask classified",
            extra={
                "path": self._describe_source(),
                "go_px":     int(go.sum()),
                "nudge_px":  int(nudge.sum()),
                "cutoff_px": int(cutoff.sum()),
            },
        )

    def _precompute_distance_field(self) -> None:
        go = self.zones.go
        h, w = go.shape

        if not go.any():
            log.warning(
                "Mask has zero GO pixels — every layer will SOE_FAIL",
                extra={"path": self._describe_source()},
            )
            self.distance_to_go = np.full((h, w), np.inf, dtype=np.float32)
            self.nearest_go_dy = np.zeros((h, w), dtype=np.int16)
            self.nearest_go_dx = np.zeros((h, w), dtype=np.int16)
            return

        non_go = (~go).astype(np.uint8)
        dist, labels = cv2.distanceTransformWithLabels(
            non_go, cv2.DIST_L2, cv2.DIST_MASK_PRECISE,
            labelType=cv2.DIST_LABEL_PIXEL,
        )
        go_yx = np.argwhere(go)
        flat_labels = labels.astype(np.int64).flatten()
        ys = np.arange(h).repeat(w)
        xs = np.tile(np.arange(w), h)
        nearest_y = np.where(
            flat_labels == 0,
            ys,
            go_yx[np.clip(flat_labels - 1, 0, len(go_yx) - 1), 0],
        )
        nearest_x = np.where(
            flat_labels == 0,
            xs,
            go_yx[np.clip(flat_labels - 1, 0, len(go_yx) - 1), 1],
        )
        dy = (nearest_y - ys).reshape(h, w).astype(np.int16)
        dx = (nearest_x - xs).reshape(h, w).astype(np.int16)

        self.distance_to_go = dist.astype(np.float32, copy=False)
        self.nearest_go_dy = dy
        self.nearest_go_dx = dx

    def classify_aabb(self, l: float, t: float, r: float, b: float) -> dict:
        h = self.comp_h
        w = self.comp_w
        cl = max(0, int(round(l)))
        ct = max(0, int(round(t)))
        cr = min(w, int(round(r)))
        cb = min(h, int(round(b)))
        if cr <= cl or cb <= ct:
            return {
                "overlap_cutoff_px": 0,
                "overlap_nudge_px":  0,
                "overlap_go_px":     0,
                "centroid_zone":     "GO",
                "translation_vector": None,
            }

        slc = (slice(ct, cb), slice(cl, cr))
        cutoff_px = int(self.zones.cutoff[slc].sum())
        nudge_px  = int(self.zones.nudge[slc].sum())
        go_px     = int(self.zones.go[slc].sum())

        cy = max(0, min(h - 1, (ct + cb - 1) // 2 if cb > ct else ct))
        cx = max(0, min(w - 1, (cl + cr - 1) // 2 if cr > cl else cl))
        if self.zones.cutoff[cy, cx]:
            centroid_zone = "CUTOFF"
        elif self.zones.go[cy, cx]:
            centroid_zone = "GO"
        else:
            centroid_zone = "NUDGE"

        translation_vector: Optional[Tuple[int, int]] = None
        if cutoff_px > 0:
            translation_vector = (
                int(self.nearest_go_dy[cy, cx]),
                int(self.nearest_go_dx[cy, cx]),
            )

        return {
            "overlap_cutoff_px":  cutoff_px,
            "overlap_nudge_px":   nudge_px,
            "overlap_go_px":      go_px,
            "centroid_zone":      centroid_zone,
            "translation_vector": translation_vector,
        }

    def classify_obb(self, bounds: Dict[str, float],
                     rotation_deg: float = 0.0,
                     pivot: Optional[Tuple[float, float]] = None) -> dict:
        aabb = bounds
        rotated = is_rotated(rotation_deg)
        corners = None
        if rotated:
            pivot_pt = pivot if pivot is not None else (
                (float(bounds["l"]) + float(bounds["r"])) / 2.0,
                (float(bounds["t"]) + float(bounds["b"])) / 2.0,
            )
            corners = obb_corners(bounds, rotation_deg, pivot_pt)
            aabb = aabb_of(corners)

        metrics = self.classify_aabb(aabb["l"], aabb["t"], aabb["r"], aabb["b"])
        if not rotated or metrics["overlap_cutoff_px"] == 0:
            return metrics

        return self._narrowphase(metrics, corners, aabb)

    def _narrowphase(self, broad: dict, corners, aabb: Dict[str, float]) -> dict:
        cl = max(0, int(round(aabb["l"])))
        ct = max(0, int(round(aabb["t"])))
        cr = min(self.comp_w, int(round(aabb["r"])))
        cb = min(self.comp_h, int(round(aabb["b"])))
        if cr <= cl or cb <= ct:
            return broad

        inside = footprint_mask(corners, cl, ct, cr, cb)
        if inside.size == 0 or not inside.any():
            return broad

        slc = (slice(ct, cb), slice(cl, cr))
        cutoff_px = int(np.logical_and(self.zones.cutoff[slc], inside).sum())
        nudge_px = int(np.logical_and(self.zones.nudge[slc], inside).sum())
        go_px = int(np.logical_and(self.zones.go[slc], inside).sum())

        ys_in, xs_in = np.nonzero(inside)
        cy = int(np.clip(ct + int(round(ys_in.mean())), 0, self.comp_h - 1))
        cx = int(np.clip(cl + int(round(xs_in.mean())), 0, self.comp_w - 1))
        if self.zones.cutoff[cy, cx]:
            centroid_zone = "CUTOFF"
        elif self.zones.go[cy, cx]:
            centroid_zone = "GO"
        else:
            centroid_zone = "NUDGE"

        translation_vector = None
        if cutoff_px > 0:
            translation_vector = (int(self.nearest_go_dy[cy, cx]),
                                  int(self.nearest_go_dx[cy, cx]))

        return {
            "overlap_cutoff_px":  cutoff_px,
            "overlap_nudge_px":   nudge_px,
            "overlap_go_px":      go_px,
            "centroid_zone":      centroid_zone,
            "translation_vector": translation_vector,
        }


def compute_world_bounds(position: List[float],
                         anchor: List[float],
                         scale: List[float],
                         source_rect: List[float],
                         rotation_deg: float = 0.0) -> Optional[Dict[str, float]]:
    """AA formula (LayerTagging.ts:73-94). Scale is in percent (100 = 1.0)."""
    if not position or not anchor or not scale or not source_rect:
        return None
    if len(position) < 2 or len(anchor) < 2 or len(source_rect) < 4:
        return None
    ax, ay = anchor[0], anchor[1]
    sx = (scale[0] if len(scale) > 0 else 100.0) / 100.0
    sy = (scale[1] if len(scale) > 1 else 100.0) / 100.0
    s_left, s_top, s_w, s_h = source_rect[0], source_rect[1], source_rect[2], source_rect[3]

    if is_rotated(rotation_deg):
        c1 = local_point_to_world(position, (ax, ay), (sx, sy), (s_left, s_top), rotation_deg)
        c2 = local_point_to_world(position, (ax, ay), (sx, sy), (s_left + s_w, s_top), rotation_deg)
        c3 = local_point_to_world(position, (ax, ay), (sx, sy), (s_left + s_w, s_top + s_h), rotation_deg)
        c4 = local_point_to_world(position, (ax, ay), (sx, sy), (s_left, s_top + s_h), rotation_deg)
        bb = aabb_of([c1, c2, c3, c4])
        l, r = bb["l"], bb["r"]
        t, b = bb["t"], bb["b"]
    else:
        x0, y0 = local_point_to_world(position, (ax, ay), (sx, sy), (s_left, s_top), rotation_deg)
        x1 = x0 + s_w * sx
        y1 = y0 + s_h * sy
        l, r = min(x0, x1), max(x0, x1)
        t, b = min(y0, y1), max(y0, y1)
    return {"l": float(l), "t": float(t), "r": float(r), "b": float(b), "w": float(r - l), "h": float(b - t)}


# Legacy alias
_compute_world_bounds = compute_world_bounds
