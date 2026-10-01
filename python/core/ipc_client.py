# (c) 2026 NeuralIO 444
# Licensed under NeuralIO Shared Source License (NSSL).
# See LICENSE for full terms.

"""
python/core/ipc_client.py
Robust TCP socket client for communicating with After Effects ExtendScript socket server.
"""

import socket
import json
import time
from typing import Any, Dict

class IPCError(RuntimeError):
    """Base class for IPC errors."""
    pass

class IPCConnectionError(IPCError):
    """Raised when socket connection fails."""
    pass

class IPCTimeoutError(IPCError):
    """Raised when socket read/write times out."""
    pass

def send_msg(sock: socket.socket, msg_str: str) -> None:
    """Send a length-prefixed message over the socket."""
    payload = msg_str.encode("utf-8")
    prefix = f"{len(payload)}:".encode("utf-8")
    sock.sendall(prefix + payload)

def recv_msg(sock: socket.socket, timeout: float = 30.0) -> str:
    """Receive a length-prefixed message from the socket."""
    sock.settimeout(timeout)
    # Read length prefix
    len_bytes = b""
    start_time = time.monotonic()
    while True:
        if time.monotonic() - start_time > timeout:
            raise IPCTimeoutError("Timeout reading length prefix from socket")
        try:
            char = sock.recv(1)
        except socket.timeout as e:
            raise IPCTimeoutError("Timeout reading length prefix from socket") from e
        if not char:
            raise IPCConnectionError("Socket closed prematurely while reading length prefix")
        if char == b":":
            break
        len_bytes += char
        
    try:
        length = int(len_bytes.decode("utf-8"))
    except ValueError as e:
        raise IPCError(f"Invalid length prefix received: {len_bytes!r}") from e
        
    # Read body
    body_bytes = b""
    start_time = time.monotonic()
    while len(body_bytes) < length:
        if time.monotonic() - start_time > timeout:
            raise IPCTimeoutError("Timeout reading body from socket")
        try:
            chunk = sock.recv(min(length - len(body_bytes), 4096))
        except socket.timeout as e:
            raise IPCTimeoutError("Timeout reading body from socket") from e
        if not chunk:
            raise IPCConnectionError("Socket closed prematurely while reading body")
        body_bytes += chunk
        
    return body_bytes.decode("utf-8")

def execute_job(job_dict: dict, port: int = 45445, timeout: float = 30.0) -> Dict[str, Any]:
    """Send a job payload to After Effects ExtendScript socket server and return the response."""
    payload_str = json.dumps(job_dict)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect(("127.0.0.1", port))
    except ConnectionRefusedError as e:
        raise IPCConnectionError(
            f"Could not connect to After Effects at 127.0.0.1:{port}. "
            f"Ensure After Effects is running and the Dimension socket server is active."
        ) from e
    except Exception as e:
        raise IPCConnectionError(f"Socket connection error: {e}") from e
        
    try:
        send_msg(sock, payload_str)
        response_str = recv_msg(sock, timeout=timeout)
        try:
            return json.loads(response_str)
        except json.JSONDecodeError as e:
            raise IPCError(f"Invalid JSON response received: {response_str[:200]}") from e
    finally:
        try:
            sock.close()
        except Exception:
            pass
