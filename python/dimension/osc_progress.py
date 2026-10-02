# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.
#
# OSC 9;4 sequence format and terminal detection follow
# https://github.com/steipete/osc-progress (MIT, Peter Steinberger).
# That package is Node-only; Dimension_CLI forbids VCS dependencies,
# so this is a stdlib port of the protocol, not a vendored runtime.

"""OSC 9;4 terminal progress for the `dimension` CLI.

Writes ConEmu/Windows Terminal progress sequences to stderr so Ghostty,
WezTerm, Canario, and Windows Terminal can paint a tab/taskbar bar.
Everywhere else — pipes, CI, `--json`, unknown terminals — every call
is a no-op. stdout is never touched.
"""

from __future__ import annotations

import os
import re
import sys
import time
from typing import Callable, Mapping, Optional, TextIO

OSC_PROGRESS_PREFIX = "\x1b]9;4;"
OSC_PROGRESS_ST = "\x1b\\"
OSC_PROGRESS_BEL = "\x07"
OSC_PROGRESS_C1_ST = "\x9c"

STATE_CLEAR = 0
STATE_NORMAL = 1
STATE_ERROR = 2
STATE_INDETERMINATE = 3
STATE_PAUSED = 4

_SUPPORTED_TERM_PROGRAMS = ("ghostty", "wezterm", "canario")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_THROTTLE_S = 0.15


def sanitize_label(label: str) -> str:
    """Drop C0/C1, DEL, and OSC terminators so a label cannot break the sequence."""
    cleaned = label.replace(OSC_PROGRESS_ST, "").replace(OSC_PROGRESS_BEL, "")
    cleaned = cleaned.replace(OSC_PROGRESS_C1_ST, "").replace("\x1b", "")
    return _CONTROL_RE.sub("", cleaned).strip()


def supports_osc_progress(
    env: Optional[Mapping[str, str]] = None,
    is_tty: Optional[bool] = None,
    *,
    disabled: bool = False,
    force: bool = False,
    disable_env_var: str = "DIMENSION_NO_PROGRESS",
    force_env_var: str = "DIMENSION_FORCE_PROGRESS",
) -> bool:
    """True only for a TTY we know will honor OSC 9;4.

    `disabled` wins over `force`. The disable env var (value ``"1"``)
    wins over the force env var. Force never bypasses a non-TTY.
    """
    env = os.environ if env is None else env
    if is_tty is None:
        is_tty = sys.stderr.isatty()
    if env.get(disable_env_var) == "1" or disabled:
        return False
    if not is_tty:
        return False
    if env.get(force_env_var) == "1" or force:
        return True
    if env.get("WT_SESSION"):
        return True
    term_program = (env.get("TERM_PROGRAM") or "").lower()
    return any(name in term_program for name in _SUPPORTED_TERM_PROGRAMS)


def _sequence(state: int, percent: Optional[int], label: str, terminator: str) -> str:
    body = f"{state}"
    if percent is not None and state != STATE_INDETERMINATE:
        body += f";{percent}"
    safe = sanitize_label(label)
    if safe:
        body += f";{safe}"
    end = OSC_PROGRESS_BEL if terminator == "bel" else OSC_PROGRESS_ST
    return f"{OSC_PROGRESS_PREFIX}{body}{end}"


def strip_osc_progress(text: str) -> str:
    """Remove OSC 9;4 sequences (ST, BEL, or C1 ST). Unterminated tails are dropped."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith(OSC_PROGRESS_PREFIX, i):
            j = i + len(OSC_PROGRESS_PREFIX)
            while j < n:
                if text.startswith(OSC_PROGRESS_ST, j) or text.startswith(OSC_PROGRESS_BEL, j):
                    j += 1 if text.startswith(OSC_PROGRESS_BEL, j) else 2
                    break
                if text.startswith(OSC_PROGRESS_C1_ST, j):
                    j += 1
                    break
                j += 1
            else:
                break
            i = j
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


class OscProgress:
    """Stateful reporter. Methods are no-ops when support detection fails."""

    def __init__(
        self,
        *,
        enabled: bool,
        write: Callable[[str], None],
        label: str = "dimension",
        terminator: str = "st",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = enabled
        self._write = write
        self.label = label
        self._terminator = terminator
        self._clock = clock
        self._last_emit = 0.0
        self._last_key: Optional[tuple] = None
        self._percent = 0
        self._closed = False

    def set_indeterminate(self, label: Optional[str] = None) -> None:
        self._emit(STATE_INDETERMINATE, None, label, force=True)

    def set_percent(self, percent: float, label: Optional[str] = None) -> None:
        if percent != percent:  # NaN
            percent = 0.0
        clamped = max(0, min(100, int(round(percent))))
        self._percent = clamped
        self._emit(STATE_NORMAL, clamped, label, force=False)

    def set_paused(self, label: Optional[str] = None) -> None:
        self._emit(STATE_PAUSED, self._percent, label, force=True)

    def done(self, label: Optional[str] = None) -> None:
        self._emit(STATE_NORMAL, 100, label or self.label, force=True)
        self.clear()

    def fail(self, label: Optional[str] = None) -> None:
        self._emit(STATE_ERROR, self._percent, label or self.label, force=True)
        self.clear()

    def clear(self) -> None:
        self._emit(STATE_CLEAR, None, self.label, force=True)
        self._closed = True

    def _emit(self, state: int, percent: Optional[int], label: Optional[str], *, force: bool) -> None:
        if not self.enabled:
            return
        if label:
            self.label = label
        key = (state, percent, self.label)
        now = self._clock()
        if not force:
            if key == self._last_key:
                return
            if now - self._last_emit < _THROTTLE_S and state == STATE_NORMAL:
                self._last_key = key
                return
        if key == self._last_key and state == STATE_CLEAR and self._closed:
            return
        self._write(_sequence(state, percent, self.label, self._terminator))
        self._last_emit = now
        self._last_key = key
        self._closed = state == STATE_CLEAR


def create_osc_progress(
    *,
    label: str = "dimension",
    env: Optional[Mapping[str, str]] = None,
    is_tty: Optional[bool] = None,
    disabled: bool = False,
    force: bool = False,
    stream: Optional[TextIO] = None,
    terminator: str = "st",
) -> OscProgress:
    target = stream if stream is not None else sys.stderr

    def _write(seq: str) -> None:
        target.write(seq)
        target.flush()

    enabled = supports_osc_progress(env, is_tty, disabled=disabled, force=force)
    return OscProgress(enabled=enabled, write=_write, label=label, terminator=terminator)
