"""The controller's Unix socket listener. Accepts a sequence of bridge
connections over a run (one Chrome process per video means one connection
per video normally, but a service-worker restart mid-video produces an
additional connection for the same VideoSession — see its module docstring
for how generations handle that)."""

import logging
import socket
import threading
import time
from pathlib import Path

from .connection import handle_connection
from .video_session import VideoSession

logger = logging.getLogger(__name__)


class ControllerServer:
    def __init__(
        self, socket_path: Path, session: VideoSession, idle_timeout: float = 30.0
    ) -> None:
        self._socket_path = socket_path
        self._session = session
        self._idle_timeout = idle_timeout
        self._sock: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._connection_threads: list[threading.Thread] = []
        self._stopping = threading.Event()

    def start(self) -> None:
        if self._socket_path.exists():
            self._socket_path.unlink()
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(self._socket_path))
        self._sock.listen(5)
        self._sock.settimeout(0.5)

        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return

            generation = self._session.new_connection_generation()
            thread = threading.Thread(
                target=self._run_connection, args=(conn, generation), daemon=True
            )
            thread.start()
            self._connection_threads.append(thread)

    def _run_connection(self, conn: socket.socket, generation: int) -> None:
        try:
            handle_connection(conn, self._session, generation, self._idle_timeout)
        except Exception:
            logger.exception("unhandled error in connection generation %d", generation)
        finally:
            conn.close()

    def wait_until_done(self, timeout: float | None = None) -> bool:
        """Block until the session reaches a terminal state. Returns False
        on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._session.is_done:
            if deadline is not None and time.monotonic() >= deadline:
                return False
            self._stopping.wait(0.05)
        return True

    def stop(self) -> None:
        self._stopping.set()
        if self._sock is not None:
            self._sock.close()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=2)
        for thread in self._connection_threads:
            thread.join(timeout=2)
        if self._socket_path.exists():
            self._socket_path.unlink()

    def __enter__(self) -> ControllerServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
