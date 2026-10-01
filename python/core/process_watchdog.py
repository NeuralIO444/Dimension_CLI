# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
Dimension Engine — Parent Process Watchdog (TASK-ENG-05 / ADR 03)

`dimension_server.py` is spawned by the CEP panel and outlives its own
usefulness the moment After Effects goes away. Without a watchdog it sits
holding port 4444 forever, and the next AE launch fails to bind — the
"zombie server" class of bug.

WHY NOT `psutil`: ADR 03's sample code uses it, but adding a dependency
means pinning it in requirements.txt AND freezing it into the shipped
PyInstaller binary, growing the ZXP and the supply-chain surface. Matt
chose stdlib-only (2026-08-29). Everything here uses `os.kill(pid, 0)`
plus a `ps` / `tasklist` shell-out.

THE PID RECYCLING TRAP (Pre-Mortem Scenario 3): checking `pid_exists`
alone is NOT sufficient. When AE dies, the OS is free to hand that exact
integer to an unrelated process — Chrome, Spotify, anything. A watchdog
that only asks "does PID 4242 exist" then believes AE is alive forever
and never exits. Every check here therefore confirms process IDENTITY
(name match), not just existence.

THE INVERSE TRAP is equally important and is why `_probe_parent` is
tri-state rather than boolean: a `ps` invocation that fails for its own
reasons (transient fork failure, sandbox denial) must NOT be read as
"the parent died." Killing a live server because a subprocess hiccuped
would be a worse bug than the one being fixed. Unknown is not dead.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Callable, Optional

# ADR 03 specifies a 1.5s poll. Issue #260 requires self-termination
# within 2.0s of parent death; one definitive reading is enough to act
# (see _probe_parent's contract), so worst case is one full interval plus
# exit time — inside that budget.
DEFAULT_POLL_INTERVAL_S = 1.5

# Optional explicit identity anchor. Normally LEAVE THIS UNUSED and let
# the watchdog snapshot the parent's real name at arm time (see
# ParentWatchdog.arm) — hardcoding an anchor is a footgun here.
#
# The CEP panel does not run inside the After Effects process; it runs in
# CEPHtmlEngine, a separate child process. So the PID the panel can hand
# us resolves to "CEPHtmlEngine", NOT "After Effects". A watchdog armed
# with a hardcoded "After Effects" anchor would find a name mismatch on
# its very first poll, conclude the parent was already dead, and kill the
# server instantly on every launch.
#
# Watching CEPHtmlEngine is also the more correct target: it exits when
# AE exits AND when the panel is closed, which is exactly the lifetime the
# Dashboard server should be bound to.
DEFAULT_EXPECTED_NAME = "After Effects"

_PS_TIMEOUT_S = 5.0


def _normalize_name(raw: str) -> str:
    """Case-fold and strip spaces/.exe so one anchor matches both platforms.

    macOS `ps -o comm=` yields ".../MacOS/After Effects"; Windows
    `tasklist` yields "AfterFX.exe". Normalizing both to a compact
    lowercase form lets a single expected-name string cover each.
    """
    name = raw.strip().lower()
    # Keep only the basename — macOS comm= returns a full executable path.
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    if name.endswith(".exe"):
        name = name[: -len(".exe")]
    return name.replace(" ", "")


def _name_matches(actual: str, expected: str) -> bool:
    """True when `expected` is a substring of `actual` after normalization.

    Both sides are normalized, so "After Effects" matches macOS's
    "After Effects" and Windows' "AfterFX.exe" is matched by an expected
    value of "AfterFX". Callers on Windows should pass the AfterFX anchor.
    """
    return _normalize_name(expected) in _normalize_name(actual)


def _pid_exists(pid: int) -> Optional[bool]:
    """Cheap existence probe. True / False / None (undeterminable).

    POSIX only — signal 0 performs error checking without delivering a
    signal. Returns None on Windows, where `os.kill` cannot express this,
    so the caller falls through to the (authoritative) name query.
    """
    if os.name != "posix":
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but is owned by another user — still a live process.
        return True
    except (OverflowError, ValueError, OSError):
        return None


def _query_process_name(pid: int) -> Optional[str]:
    """Return the process name for `pid`, or None if it cannot be determined.

    None is deliberately ambiguous between "no such process" and "the
    query itself failed" — `_probe_parent` resolves that ambiguity using
    the cheap existence check, and treats an unresolved None as UNKNOWN
    rather than dead.
    """
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=_PS_TIMEOUT_S,
            )
            if out.returncode != 0:
                return None
            line = out.stdout.strip()
            # tasklist prints an INFO banner (not CSV) when nothing matched.
            if not line or not line.startswith('"'):
                return None
            return line.split('","')[0].lstrip('"')

        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True, text=True, timeout=_PS_TIMEOUT_S,
        )
        # ps exits non-zero when the pid does not exist; that is a real
        # answer, but an empty stdout on a zero exit is not.
        if out.returncode != 0:
            return None
        name = out.stdout.strip()
        return name or None
    except (OSError, subprocess.SubprocessError):
        return None


def probe_parent(
    pid: int,
    expected_name: str = DEFAULT_EXPECTED_NAME,
    *,
    _exists: Callable[[int], Optional[bool]] = _pid_exists,
    _name: Callable[[int], Optional[str]] = _query_process_name,
) -> Optional[bool]:
    """Tri-state parent liveness.

        True  — parent is alive AND is the expected process
        False — DEFINITIVELY gone (no such pid, or the pid now belongs to
                a different process — i.e. it was recycled)
        None  — undeterminable right now; the caller must treat this as
                "no new information", never as death

    The tri-state return is the whole point. Collapsing None into False
    would let one failed `ps` call terminate a server whose AE is running
    perfectly well.
    """
    exists = _exists(pid)
    if exists is False:
        return False  # definitive: the pid is gone

    name = _name(pid)
    if name is None:
        # Could not read a name. If the cheap probe positively confirmed
        # the pid exists, we know *something* is there but not what — that
        # is genuinely unknown, not dead.
        if exists is True:
            return None
        # POSIX where os.kill said "unknown", or Windows where the name
        # query is the only signal we have: on Windows a clean tasklist
        # run with no match is the authoritative "not running" answer,
        # but we cannot distinguish that from a failed invocation here,
        # so stay conservative.
        return None

    return True if _name_matches(name, expected_name) else False


class ParentWatchdog:
    """Background thread that terminates this process when AE goes away.

    Not started automatically — `start()` is explicit so tests and CLI
    invocations that have no parent AE never spawn it.
    """

    def __init__(
        self,
        parent_pid: int,
        expected_name: Optional[str] = None,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        *,
        on_parent_death: Optional[Callable[[], None]] = None,
        _probe: Callable[..., Optional[bool]] = probe_parent,
        _name: Callable[[int], Optional[str]] = _query_process_name,
    ) -> None:
        self.parent_pid = int(parent_pid)
        # None (the normal case) means "snapshot it at arm time" — see arm().
        self.expected_name = expected_name
        self.poll_interval_s = poll_interval_s
        self._probe = _probe
        self._name = _name
        self._on_parent_death = on_parent_death or self._default_terminate
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Observability for tests and for anyone debugging a server that
        # exited unexpectedly.
        self.last_state: Optional[bool] = None
        self.unknown_streak = 0
        self.armed = False

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def arm(self) -> bool:
        """Establish the identity anchor. Returns True if the watchdog may run.

        When no explicit `expected_name` was given, the parent's CURRENT
        process name is snapshotted and every later poll must still match
        it. That is what defeats PID recycling without the caller having to
        know whether it is being watched as "After Effects", "AfterFX.exe",
        or "CEPHtmlEngine" — a guess that is wrong on at least one platform
        and would make the watchdog kill the server on its first poll.

        Returns False when the parent's name cannot be read at all. The
        caller must then NOT run the watchdog: with no identity anchor we
        could not tell a recycled PID from the real parent, and a watchdog
        that cannot distinguish those is worse than none — it would either
        never fire or fire wrongly.
        """
        if self.expected_name:
            self.armed = True
            return True
        name = self._name(self.parent_pid)
        if not name:
            self.armed = False
            return False
        self.expected_name = name
        self.armed = True
        return True

    def _default_terminate(self) -> None:
        # Says "parent process", not "After Effects": the watched parent is
        # normally CEPHtmlEngine (the panel's host), so naming AE here
        # would send a reader hunting the wrong process in Activity Monitor.
        print(
            f"[watchdog] Parent process (PID {self.parent_pid}, "
            f"'{self.expected_name}') is gone — shutting down the Dimension "
            f"server to avoid orphaning the port.",
            file=sys.stderr,
            flush=True,
        )
        # os._exit, not sys.exit: this runs on a daemon thread, where
        # SystemExit would only unwind THIS thread and leave the blocking
        # serve_forever() loop on the main thread running forever — the
        # exact zombie this class exists to prevent.
        os._exit(0)

    def check_once(self) -> Optional[bool]:
        """One poll. Fires the death callback on a definitive False."""
        state = self._probe(self.parent_pid, self.expected_name)
        self.last_state = state
        if state is None:
            self.unknown_streak += 1
            return state
        self.unknown_streak = 0
        if state is False:
            self._on_parent_death()
        return state

    def _run(self) -> None:
        # Event.wait doubles as the sleep and the stop signal, so stop()
        # is immediate rather than waiting out a full poll interval.
        while not self._stop.wait(self.poll_interval_s):
            try:
                self.check_once()
            except Exception as exc:  # never let the watchdog kill itself
                print(f"[watchdog] probe error (ignored): {exc}",
                      file=sys.stderr, flush=True)

    def start(self) -> "ParentWatchdog":
        if self._thread is not None:
            return self
        if not self.arm():
            # Fail OPEN, not closed: an unwatched server is a recoverable
            # annoyance (the user quits it), a wrongly-killed one loses
            # work mid-conform.
            print(
                f"[watchdog] Could not read process name for parent PID "
                f"{self.parent_pid} — running WITHOUT a parent watchdog. "
                f"This server will not self-terminate when its parent exits.",
                file=sys.stderr, flush=True,
            )
            return self
        self._thread = threading.Thread(
            target=self._run, name="dimension-parent-watchdog", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_interval_s + 1.0)
            self._thread = None
