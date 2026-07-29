"""Length-prefixed framing for the controller's Unix socket (SPEC-V3: "The
controller Unix socket uses length-prefixed framing, not newline-delimited
JSON"). A 4-byte big-endian length prefix, then the payload.

This is a distinct boundary from Chrome's native-messaging framing on
stdin/stdout (Phase 4), which SPEC-V3 pins to *native* byte order because
Chrome itself dictates that format. Nothing pins the byte order on this
socket — it's the controller's own protocol — so big-endian (network byte
order) is used as the ordinary, unambiguous choice.
"""

import socket
import struct

_LENGTH_STRUCT = struct.Struct("!I")
_LENGTH_PREFIX_SIZE = _LENGTH_STRUCT.size

# Mirrors SPEC-V3's extension-to-host ceiling: payloads flow in the roomy
# direction, so this is the largest frame that should ever legitimately
# arrive. A length prefix beyond this is untrusted input, not a real frame.
MAX_FRAME_SIZE = 64 * 1024 * 1024


class FrameTooLarge(Exception):
    pass


def _recv_exact(sock: socket.socket, size: int) -> bytes | None:
    """Read exactly `size` bytes, or return None if the peer closed the
    connection before sending any of them (clean EOF at a frame boundary).
    A partial frame followed by EOF raises ConnectionError."""
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            if remaining == size:
                return None
            raise ConnectionError("connection closed mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(sock: socket.socket) -> bytes | None:
    """Read one length-prefixed frame. Returns None on a clean EOF between
    frames. Raises FrameTooLarge if the declared length exceeds
    MAX_FRAME_SIZE, ConnectionError on a truncated frame."""
    header = _recv_exact(sock, _LENGTH_PREFIX_SIZE)
    if header is None:
        return None
    (length,) = _LENGTH_STRUCT.unpack(header)
    if length > MAX_FRAME_SIZE:
        raise FrameTooLarge(f"declared frame length {length} exceeds {MAX_FRAME_SIZE}")
    body = _recv_exact(sock, length)
    if body is None:
        raise ConnectionError("connection closed mid-frame")
    return body


def write_frame(sock: socket.socket, payload: bytes) -> None:
    sock.sendall(_LENGTH_STRUCT.pack(len(payload)) + payload)
