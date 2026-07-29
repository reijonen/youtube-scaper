"""The native-messaging bridge, per SPEC-V3's "Native-host bridge" and
PLAN.md's Phase 4. Chrome launches this when the extension calls
`chrome.runtime.connectNative("com.sor.yts")`.

Purely a forwarder between two framing formats: Chrome's native-messaging
protocol on stdin/stdout (32-bit length in *native* byte order, then UTF-8
JSON — SPEC-V3, "Message framing") and the controller's own length-prefixed
Unix socket protocol (scraper.controller.framing — big-endian, a distinct
and unrelated choice, see that module's docstring). It never inspects,
parses, or acts on message contents: "No scheduling logic, no SQLite, no
knowledge of videos" (PLAN.md).

stdout is reserved exclusively for native-messaging frames. `_lock_down_stdout`
duplicates the real stdout file descriptor for that exclusive use, then
redirects the OS-level fd 1 to wherever fd 2 (stderr) points — so a stray
print(), or a write from a C extension, ends up as a log line instead of
corrupting the protocol stream. This is stronger than reassigning
`sys.stdout = sys.stderr`, which only protects the Python-level object, not
raw fd writes.
"""

import logging
import os
import select
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from . import config
from .controller import framing as socket_framing
from .controller import protocol

logger = logging.getLogger(__name__)

# Native byte order, per SPEC-V3's "struct format `=I`" — distinct from the
# controller socket's big-endian choice (see controller/framing.py).
_NATIVE_LENGTH_STRUCT = struct.Struct("=I")

MAX_EXTENSION_TO_HOST_FRAME = 64 * 1024 * 1024
MAX_HOST_TO_EXTENSION_FRAME = 1 * 1024 * 1024

# There is no SPEC-V3-defined wire shape for the bridge's own
# controller-unavailable notification — see SPEC-V3, "If the controller is
# unreachable, the bridge reports a structured controller-unavailable error
# and the extension pauses," which names the behaviour but not a message
# schema. This mirrors the {type, protocolVersion} convention used
# throughout the controller protocol, but is not one of its 12 messages: it
# never touches the controller (by definition, when this fires, the
# controller is unreachable) and is synthesized by the bridge alone.
CONTROLLER_UNAVAILABLE_MESSAGE_TYPE = "controller_unavailable"


class NativeFrameTooLarge(Exception):
    pass


def _read_exact(stream: BinaryIO, size: int) -> bytes | None:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            if remaining == size:
                return None
            raise ConnectionError("stream closed mid-frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_native_frame(stream: BinaryIO, max_size: int) -> bytes | None:
    """Read one native-messaging frame. Returns None on clean EOF between
    frames. Raises NativeFrameTooLarge if the declared length exceeds
    max_size, ConnectionError on a truncated frame."""
    header = _read_exact(stream, _NATIVE_LENGTH_STRUCT.size)
    if header is None:
        return None
    (length,) = _NATIVE_LENGTH_STRUCT.unpack(header)
    if length > max_size:
        raise NativeFrameTooLarge(f"declared frame length {length} exceeds {max_size}")
    body = _read_exact(stream, length)
    if body is None:
        raise ConnectionError("stream closed mid-frame")
    return body


def write_native_frame(stream: BinaryIO, payload: bytes, max_size: int) -> None:
    if len(payload) > max_size:
        raise NativeFrameTooLarge(f"payload of {len(payload)} bytes exceeds {max_size}")
    stream.write(_NATIVE_LENGTH_STRUCT.pack(len(payload)))
    stream.write(payload)
    stream.flush()


def _wait_readable(fileno: int, timeout: float) -> bool:
    ready, _, _ = select.select([fileno], [], [], timeout)
    return bool(ready)


@dataclass(frozen=True)
class ReconnectPolicy:
    base_delay: float = 0.5
    max_delay: float = 30.0
    max_attempts: int | None = None  # None = retry forever

    def delay_for_attempt(self, attempt: int) -> float:
        return min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))


class Bridge:
    def __init__(
        self,
        stdin: BinaryIO,
        stdout: BinaryIO,
        socket_path: Path,
        reconnect_policy: ReconnectPolicy | None = None,
    ) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._socket_path = socket_path
        self._reconnect_policy = reconnect_policy or ReconnectPolicy()
        self._stdout_lock = threading.Lock()
        self._stdin_closed = False

    def run(self) -> None:
        """Runs until stdin closes (Chrome tore down the native port), or
        until the reconnect policy's max_attempts is exhausted (production
        use leaves this unbounded; tests bound it)."""
        attempt = 0
        reported_unavailable = False

        while not self._stdin_closed:
            sock = self._try_connect()
            if sock is None:
                attempt += 1
                if not reported_unavailable:
                    self._send_controller_unavailable()
                    reported_unavailable = True
                if (
                    self._reconnect_policy.max_attempts is not None
                    and attempt >= self._reconnect_policy.max_attempts
                ):
                    logger.error("giving up after %d connection attempts", attempt)
                    return
                time.sleep(self._reconnect_policy.delay_for_attempt(attempt))
                continue

            attempt = 0
            reported_unavailable = False
            self._forward_until_disconnect(sock)
            sock.close()

    def _try_connect(self) -> socket.socket | None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(self._socket_path))
        except OSError as exc:
            logger.warning("controller connection failed: %s", exc)
            sock.close()
            return None
        return sock

    def _send_controller_unavailable(self) -> None:
        msg = {
            "type": CONTROLLER_UNAVAILABLE_MESSAGE_TYPE,
            "protocolVersion": protocol.PROTOCOL_VERSION,
        }
        with self._stdout_lock:
            write_native_frame(
                self._stdout, protocol.encode_message(msg), MAX_HOST_TO_EXTENSION_FRAME
            )

    def _forward_until_disconnect(self, sock: socket.socket) -> None:
        stop = threading.Event()

        def stdin_to_socket() -> None:
            try:
                while not stop.is_set():
                    if not _wait_readable(self._stdin.fileno(), 0.5):
                        continue
                    frame = read_native_frame(self._stdin, MAX_EXTENSION_TO_HOST_FRAME)
                    if frame is None:
                        self._stdin_closed = True
                        return
                    socket_framing.write_frame(sock, frame)
            except (ConnectionError, NativeFrameTooLarge, OSError) as exc:
                logger.warning("stdin->socket forwarding stopped: %s", exc)
            finally:
                stop.set()
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        def socket_to_stdout() -> None:
            try:
                while not stop.is_set():
                    frame = socket_framing.read_frame(sock)
                    if frame is None:
                        return
                    with self._stdout_lock:
                        write_native_frame(self._stdout, frame, MAX_HOST_TO_EXTENSION_FRAME)
            except (ConnectionError, socket_framing.FrameTooLarge, OSError) as exc:
                logger.warning("socket->stdout forwarding stopped: %s", exc)
            finally:
                stop.set()

        t1 = threading.Thread(target=stdin_to_socket, daemon=True)
        t2 = threading.Thread(target=socket_to_stdout, daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()


def _lock_down_stdout() -> BinaryIO:
    raw_stdout_fd = os.dup(1)
    os.dup2(2, 1)
    return os.fdopen(raw_stdout_fd, "wb", buffering=0)


def main() -> int:
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    raw_stdout = _lock_down_stdout()
    bridge = Bridge(sys.stdin.buffer, raw_stdout, config.CONTROLLER_SOCKET)
    bridge.run()
    return 0
