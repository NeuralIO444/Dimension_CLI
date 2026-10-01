# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
test_ipc_client.py
Regression coverage for python/core/ipc_client.py::recv_msg.

Uses socket.socketpair() (stdlib, no new dependency) to drive recv_msg
against a controlled peer socket, covering:
  1. OS-level socket.timeout firing mid-recv must surface as
     IPCTimeoutError, never as the raw builtin socket.timeout.
  2. A normal length-prefixed round trip still returns correctly.
  3. A peer closing mid-message still raises IPCConnectionError
     (regression guard for existing behavior).
"""

from __future__ import annotations

import os
import socket
import sys
import threading

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

import pytest

from core.ipc_client import (
    IPCConnectionError,
    IPCTimeoutError,
    recv_msg,
    send_msg,
)


def test_recv_msg_raises_ipc_timeout_error_not_socket_timeout():
    """A peer that never writes must surface IPCTimeoutError, not socket.timeout.

    This forces the OS-level socket timeout path specifically: the
    manual time.monotonic() bookkeeping in recv_msg checks *before*
    each blocking recv() call, so the only way to exercise the
    underlying socket.timeout race is to give the OS timeout a
    shorter/comparable window than the polling granularity and never
    write anything from the peer side.
    """
    left, right = socket.socketpair()
    try:
        # right (the peer) never writes anything.
        with pytest.raises(IPCTimeoutError):
            recv_msg(left, timeout=0.05)
    finally:
        left.close()
        right.close()


def test_recv_msg_normal_round_trip_returns_body():
    """A well-formed length-prefixed message still round-trips correctly."""
    left, right = socket.socketpair()
    try:
        message = '{"hello": "world"}'

        def _writer():
            send_msg(right, message)

        t = threading.Thread(target=_writer)
        t.start()
        t.join(timeout=2.0)

        result = recv_msg(left, timeout=2.0)
        assert result == message
    finally:
        left.close()
        right.close()


def test_recv_msg_peer_closed_mid_message_raises_ipc_connection_error():
    """Regression guard: peer closing mid-body still raises IPCConnectionError."""
    left, right = socket.socketpair()
    try:
        # Announce a body longer than what we actually send, then close.
        right.sendall(b"100:partial-body")
        right.close()

        with pytest.raises(IPCConnectionError):
            recv_msg(left, timeout=2.0)
    finally:
        left.close()


def test_recv_msg_peer_closed_before_length_prefix_raises_ipc_connection_error():
    """Regression guard: peer closing before sending any length prefix."""
    left, right = socket.socketpair()
    try:
        right.close()
        with pytest.raises(IPCConnectionError):
            recv_msg(left, timeout=2.0)
    finally:
        left.close()
