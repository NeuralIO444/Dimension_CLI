# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/telemetry.py
Dimension High-Precision Performance Telemetry & Diagnostic Instrumentation.

Provides sub-microsecond timing spans, memory heap tracking, and per-layer
transform profiling across all conform pipeline phases (Scrape -> Survey ->
Duplication -> ScaleEngine -> SOE -> Slicer -> Inject -> Audit).
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple


@dataclass
class LayerMetric:
    """Telemetry data captured for an individual conformed layer."""
    uid: str
    name: str
    tag: str
    layer_kind: str
    src_rect: List[float] = field(default_factory=list)
    src_pos: List[float] = field(default_factory=list)
    src_scale: List[float] = field(default_factory=list)
    dst_pos: List[float] = field(default_factory=list)
    dst_scale: List[float] = field(default_factory=list)
    soe_nudge: Optional[List[float]] = None
    flags: Dict[str, Any] = field(default_factory=dict)
    duration_us: float = 0.0


@dataclass
class TimingSpan:
    """A high-precision execution span representing a pipeline phase or function."""
    name: str
    phase: str
    start_ns: int
    end_ns: int = 0
    duration_ms: float = 0.0
    memory_delta_kb: float = 0.0
    layer_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def close(self, memory_delta_kb: float = 0.0, layer_count: int = 0, **extra_metadata) -> None:
        self.end_ns = time.perf_counter_ns()
        self.duration_ms = (self.end_ns - self.start_ns) / 1_000_000.0
        self.memory_delta_kb = memory_delta_kb
        if layer_count:
            self.layer_count = layer_count
        if extra_metadata:
            self.metadata.update(extra_metadata)


class TelemetryCollector:
    """Centralized diagnostic collector for Dimension performance instrumentation."""

    def __init__(self, enabled: Optional[bool] = None):
        # `enabled=None` (the default, including for the module singleton
        # below) defers to DIMENSION_TELEMETRY; an explicit True/False
        # always overrides it. The previous `enabled or bool(env == "1")`
        # form made the env var unreachable: `enabled` defaulted to True,
        # and `True or anything` is always True, so DIMENSION_TELEMETRY=0
        # could never actually disable the module-level TELEMETRY
        # singleton every conform pass writes to (scale_engine.py).
        if enabled is None:
            enabled = os.environ.get("DIMENSION_TELEMETRY", "1") == "1"
        self.enabled: bool = enabled
        self.spans: List[TimingSpan] = []
        self.layer_metrics: List[LayerMetric] = []
        self.system_info: Dict[str, Any] = {
            "platform": os.uname().sysname if hasattr(os, "uname") else "Unknown",
            "release": os.uname().release if hasattr(os, "uname") else "Unknown",
            "python_version": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}",
            "pid": os.getpid(),
        }
        self._tracemalloc_active: bool = False

    def start_profiling(self) -> None:
        """Enable memory heap tracing."""
        if not self._tracemalloc_active:
            try:
                tracemalloc.start()
                self._tracemalloc_active = True
            except Exception:
                pass

    def stop_profiling(self) -> Tuple[int, int]:
        """Stop memory tracing and return (current_kb, peak_kb)."""
        if self._tracemalloc_active:
            try:
                current, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                self._tracemalloc_active = False
                return current // 1024, peak // 1024
            except Exception:
                pass
        return 0, 0

    @contextlib.contextmanager
    def span(self, name: str, phase: str = "core", **metadata) -> Generator[TimingSpan, None, None]:
        """Context manager to measure high-resolution duration and memory delta of a block."""
        if not self.enabled:
            yield TimingSpan(name=name, phase=phase, start_ns=0)
            return

        mem_before = 0
        if self._tracemalloc_active:
            mem_before = tracemalloc.get_traced_memory()[0]

        span_obj = TimingSpan(
            name=name,
            phase=phase,
            start_ns=time.perf_counter_ns(),
            metadata=dict(metadata),
        )
        self.spans.append(span_obj)
        try:
            yield span_obj
        finally:
            mem_delta_kb = 0.0
            if self._tracemalloc_active:
                mem_after = tracemalloc.get_traced_memory()[0]
                mem_delta_kb = (mem_after - mem_before) / 1024.0
            span_obj.close(memory_delta_kb=mem_delta_kb)

    def record_layer(
        self,
        uid: str,
        name: str,
        tag: str,
        layer_kind: str,
        src_rect: Optional[List[float]] = None,
        src_pos: Optional[List[float]] = None,
        src_scale: Optional[List[float]] = None,
        dst_pos: Optional[List[float]] = None,
        dst_scale: Optional[List[float]] = None,
        soe_nudge: Optional[List[float]] = None,
        duration_us: float = 0.0,
        **flags,
    ) -> None:
        """Record spatial and transform telemetry for a single layer."""
        if not self.enabled:
            return
        self.layer_metrics.append(LayerMetric(
            uid=uid,
            name=name,
            tag=tag or "UNCLASS",
            layer_kind=layer_kind,
            src_rect=src_rect or [],
            src_pos=src_pos or [],
            src_scale=src_scale or [],
            dst_pos=dst_pos or [],
            dst_scale=dst_scale or [],
            soe_nudge=soe_nudge,
            flags=flags,
            duration_us=duration_us,
        ))

    def get_summary(self) -> Dict[str, Any]:
        """Produce a structured telemetry summary report."""
        total_duration_ms = sum(s.duration_ms for s in self.spans)
        phases: Dict[str, Dict[str, Any]] = {}
        for s in self.spans:
            if s.phase not in phases:
                phases[s.phase] = {"total_ms": 0.0, "count": 0, "spans": []}
            phases[s.phase]["total_ms"] += s.duration_ms
            phases[s.phase]["count"] += 1
            phases[s.phase]["spans"].append({
                "name": s.name,
                "duration_ms": round(s.duration_ms, 3),
                "memory_delta_kb": round(s.memory_delta_kb, 2),
                "layer_count": s.layer_count,
                "metadata": s.metadata,
            })

        total_layers = len(self.layer_metrics)
        throughput_layers_per_sec = (
            (total_layers / (total_duration_ms / 1000.0))
            if total_duration_ms > 0 and total_layers > 0
            else 0.0
        )

        return {
            "total_duration_ms": round(total_duration_ms, 3),
            "total_layers": total_layers,
            "throughput_layers_per_sec": round(throughput_layers_per_sec, 1),
            "phase_breakdown": phases,
            "system_info": self.system_info,
            "layer_count_by_tag": self._get_tag_counts(),
        }

    def _get_tag_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for l in self.layer_metrics:
            counts[l.tag] = counts.get(l.tag, 0) + 1
        return counts

    def reset(self) -> None:
        """Clear recorded spans and layer metrics."""
        self.spans.clear()
        self.layer_metrics.clear()

    def export_json(self, target_path: str | Path) -> str:
        """Export full telemetry payload to a JSON file."""
        data = {
            "summary": self.get_summary(),
            "spans": [asdict(s) for s in self.spans],
            "layer_metrics": [asdict(l) for l in self.layer_metrics],
        }
        p = Path(target_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return str(p.resolve())


# Global module singleton
TELEMETRY: TelemetryCollector = TelemetryCollector()
