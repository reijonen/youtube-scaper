import json
import os
import select
import threading

import pytest

from scraper.native_host import Bridge, ReconnectPolicy, read_native_frame, write_native_frame
from tests.controller.conftest import (  # noqa: F401 (re-exported as fixtures)
    PLAYER_PAYLOAD_BODY,
    hello,
    payload,
    server,
    session,
    storage,
    video_done,
    video_failed,
)


class FakeChrome:
    """Drives the bridge's stdin/stdout using Chrome's native-messaging
    framing (native byte order), the same way the real browser would."""

    def __init__(self, write_fd: int, read_fd: int) -> None:
        self._write_file = os.fdopen(write_fd, "wb", buffering=0)
        self._read_file = os.fdopen(read_fd, "rb", buffering=0)

    def send(self, msg: dict) -> None:
        write_native_frame(self._write_file, json.dumps(msg).encode("utf-8"), 1024 * 1024)

    def recv(self, timeout: float = 2.0) -> dict | None:
        ready, _, _ = select.select([self._read_file.fileno()], [], [], timeout)
        if not ready:
            return None
        frame = read_native_frame(self._read_file, 64 * 1024 * 1024)
        if frame is None:
            return None
        return json.loads(frame.decode("utf-8"))

    def close_stdin(self) -> None:
        self._write_file.close()

    def close(self) -> None:
        try:
            self._write_file.close()
        except OSError:
            pass
        try:
            self._read_file.close()
        except OSError:
            pass


@pytest.fixture
def bridge_harness(server):  # noqa: F811 (pytest fixture injection, not a real redefinition)
    """Wires a real Bridge to fake Chrome (via OS pipes, native framing) on
    one side and the real ControllerServer (via the Unix socket, from
    tests/controller/conftest.py) on the other."""
    _controller_server, socket_path = server

    chrome_to_bridge_r, chrome_to_bridge_w = os.pipe()
    bridge_to_chrome_r, bridge_to_chrome_w = os.pipe()

    bridge_stdin = os.fdopen(chrome_to_bridge_r, "rb", buffering=0)
    bridge_stdout = os.fdopen(bridge_to_chrome_w, "wb", buffering=0)

    bridge = Bridge(
        bridge_stdin,
        bridge_stdout,
        socket_path,
        reconnect_policy=ReconnectPolicy(base_delay=0.05, max_delay=0.2, max_attempts=None),
    )
    thread = threading.Thread(target=bridge.run, daemon=True)
    thread.start()

    chrome = FakeChrome(write_fd=chrome_to_bridge_w, read_fd=bridge_to_chrome_r)

    yield chrome, bridge

    chrome.close()
    thread.join(timeout=2)
