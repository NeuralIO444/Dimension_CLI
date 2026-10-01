# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Dimension Engine — Structured JSON Logger
Replaces all print() calls. Outputs newline-delimited JSON to:
  - stderr (for terminal / CI visibility)
  - logs/dimension.log (rotating, 5 MB max, 3 backups)

Usage:
    from core.logger import log
    log.info("Scale engine started", extra={"scale_factor": 1.77})
    log.error("Hash mismatch", extra={"path": "scrape_manifest.json"})
"""

import logging
import json
import os
import sys
import time
from logging.handlers import RotatingFileHandler


_RESERVED_LOGRECORD_KEYS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
}


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "msg": record.getMessage(),
            "module": record.module,
        }
        # Python's logging flattens extra={...} into record.__dict__ as individual
        # attributes. Pull every non-reserved attribute through to the JSON payload.
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOGRECORD_KEYS or key.startswith("_"):
                continue
            if key == "extra" and isinstance(value, dict):
                payload.update(value)
                continue
            try:
                json.dumps(value)
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _resolve_log_dir() -> str:
    """Pick the directory the rotating file handler writes to.

    Priority:
      1. DIMENSION_LOG_DIR env var (explicit override, any caller).
      2. pytest detection → logs/tests/ so test noise never pollutes
         the runtime log consumed by the UI / ops / users.
      3. Default: logs/ next to the working directory.

    pytest is detected by module presence (import-time reliable) and by
    PYTEST_CURRENT_TEST (set per-test). Either is enough; both are checked
    because the logger is built once at import and may import before any
    individual test has started.
    """
    override = os.environ.get("DIMENSION_LOG_DIR")
    if override:
        return os.path.abspath(override)

    is_pytest = (
        "pytest" in sys.modules
        or "PYTEST_CURRENT_TEST" in os.environ
        or os.path.basename(sys.argv[0] if sys.argv else "").startswith("pytest")
    )
    if is_pytest:
        return os.path.abspath(os.path.join("logs", "tests"))

    return os.path.abspath("logs")


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("dimension")
    if logger.handlers:
        return logger  # already configured (import guard)

    logger.setLevel(logging.DEBUG)
    # Do NOT propagate to root — pytest's caplog / root handlers would
    # otherwise mirror every log line, defeating the isolation.
    logger.propagate = False

    # --- stderr handler ---
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(_JSONFormatter())
    logger.addHandler(sh)

    # --- rotating file handler ---
    log_dir = _resolve_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    fh = RotatingFileHandler(
        os.path.join(log_dir, "dimension.log"),
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_JSONFormatter())
    logger.addHandler(fh)

    return logger


log = _build_logger()


# ── Slot 17 — Phase profiling helper ───────────────────────────────────────
#
# Lightweight context manager that emits two structured log lines per phase:
#   {"msg": "phase.start", "phase": "<name>"}
#   {"msg": "phase.end",   "phase": "<name>", "elapsed_ms": <float>, **extra}
#
# Uses time.perf_counter() for monotonic sub-millisecond resolution — the
# JSON formatter's `ts` field only carries second precision, which is too
# coarse for phases like SCALE (single-digit ms) or LERP (tens of ms).
#
# Pure observability. No behavior change: every code path that previously
# ran without this wrapper still runs identically; the only side effect is
# two additional log lines per wrapped phase. Tail logs with
# `grep '"msg": "phase\.\(start\|end\)"'` to extract a profiling slice.
#
# Designed to nest: phase names should be hierarchical when nesting is
# desired ("conform.scale", "conform.lerp"), but the context manager does
# not enforce a tree — callers may freely interleave timers.
# KEPT INTENTIONALLY (issue #379): phase_timer has zero production callers
# but exists as a ready-made profiling hook for future telemetry work. Wiring
# it into conform phases (`scale_engine.py` / `orchestrator.py`) is a separate
# pipeline-adjacent change pending explicit review. This symbol should not be
# deleted as "unused" without revisiting that decision.
#
class phase_timer:  # noqa: N801 — lowercase to match `log` import style
    """Emit phase.start / phase.end log lines with `elapsed_ms`.

    Usage:
        with phase_timer("scale", layer_count=99):
            scale_engine.conform()

    On enter: logs `phase.start` with `phase` + any kwargs.
    On exit:  logs `phase.end` with `phase`, `elapsed_ms`, `ok` (bool),
              and the original kwargs. If an exception propagates, `ok`
              is False and `exc_type` carries the class name.
    """

    __slots__ = ("_name", "_extra", "_t0")

    def __init__(self, name: str, **extra):
        self._name = name
        self._extra = extra
        self._t0 = 0.0

    def __enter__(self):
        self._t0 = time.perf_counter()
        payload = {"phase": self._name, **self._extra}
        log.info("phase.start", extra=payload)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed_ms = (time.perf_counter() - self._t0) * 1000.0
        payload = {
            "phase": self._name,
            "elapsed_ms": round(elapsed_ms, 3),
            "ok": exc_type is None,
            **self._extra,
        }
        if exc_type is not None:
            payload["exc_type"] = exc_type.__name__
        log.info("phase.end", extra=payload)
        return False  # never swallow exceptions
