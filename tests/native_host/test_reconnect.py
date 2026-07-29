import os
import shutil
import tempfile
import threading
from pathlib import Path

from scraper.controller import ControllerServer
from scraper.native_host import Bridge, ReconnectPolicy
from tests.controller.conftest import hello
from tests.native_host.conftest import FakeChrome


def test_controller_unavailable_reported_then_recovers_on_reconnect(session):
    socket_dir = Path(tempfile.mkdtemp(prefix="scraper-sock-"))
    socket_path = socket_dir / "c.sock"  # nothing listens here yet

    chrome_to_bridge_r, chrome_to_bridge_w = os.pipe()
    bridge_to_chrome_r, bridge_to_chrome_w = os.pipe()
    bridge_stdin = os.fdopen(chrome_to_bridge_r, "rb", buffering=0)
    bridge_stdout = os.fdopen(bridge_to_chrome_w, "wb", buffering=0)

    bridge = Bridge(
        bridge_stdin,
        bridge_stdout,
        socket_path,
        reconnect_policy=ReconnectPolicy(base_delay=0.05, max_delay=0.1, max_attempts=None),
    )
    thread = threading.Thread(target=bridge.run, daemon=True)
    thread.start()

    chrome = FakeChrome(write_fd=chrome_to_bridge_w, read_fd=bridge_to_chrome_r)
    try:
        unavailable = chrome.recv(timeout=2.0)
        assert unavailable == {"type": "controller_unavailable", "protocolVersion": 1}

        # Now start the real controller — the bridge should reconnect on its
        # own via the backoff loop, with no restart of the bridge itself.
        server = ControllerServer(socket_path, session, idle_timeout=2.0)
        server.start()
        try:
            chrome.send(hello())
            ack = chrome.recv(timeout=3.0)
            assert ack is not None
            assert ack["type"] == "hello_ack"
        finally:
            server.stop()
    finally:
        chrome.close()
        thread.join(timeout=2)
        shutil.rmtree(socket_dir, ignore_errors=True)
