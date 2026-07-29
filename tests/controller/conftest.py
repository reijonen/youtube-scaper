import json
import shutil
import socket
import tempfile
from pathlib import Path

import pytest

from scraper.controller import (
    Caps,
    ControllerServer,
    VideoSession,
    protocol,
    read_frame,
    write_frame,
)
from scraper.storage import Storage

TEST_CAPS = Caps(
    max_payload_bytes=1024,
    max_payloads_per_video=5,
    max_total_bytes_per_video=4096,
)


class FakeClient:
    """A minimal scripted extension/bridge stand-in, driving a real Unix
    socket connection."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    def send(self, msg: dict) -> None:
        write_frame(self._sock, protocol.encode_message(msg))

    def recv(self, timeout: float = 2.0) -> dict | None:
        """Returns None both on a clean EOF and on timing out — either way,
        "nothing arrived" is what tests care about."""
        self._sock.settimeout(timeout)
        try:
            frame = read_frame(self._sock)
        except TimeoutError:
            return None
        if frame is None:
            return None
        return json.loads(frame.decode("utf-8"))

    def close(self) -> None:
        self._sock.close()


@pytest.fixture
def storage(tmp_path):
    db_path = tmp_path / "db.sqlite3"
    raw_root = tmp_path / "raw"
    with Storage(db_path, raw_root) as s:
        yield s


@pytest.fixture
def session(storage):
    storage.start_run("run1")
    return VideoSession(
        run_id="run1",
        video_id="vid1",
        video_url="https://www.youtube.com/watch?v=vid1",
        config={"maxRecommendations": 100},
        storage=storage,
        caps=TEST_CAPS,
    )


@pytest.fixture
def server(session):
    # AF_UNIX paths are capped at ~104 bytes on macOS; pytest's tmp_path
    # nests too deep for that, so use a short-named dir directly under the
    # system temp root instead.
    socket_dir = Path(tempfile.mkdtemp(prefix="scraper-sock-"))
    socket_path = socket_dir / "c.sock"
    try:
        with ControllerServer(socket_path, session, idle_timeout=2.0) as s:
            yield s, socket_path
    finally:
        shutil.rmtree(socket_dir, ignore_errors=True)


@pytest.fixture
def connect(server):
    _server, socket_path = server
    clients = []

    def _connect() -> FakeClient:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(str(socket_path))
        client = FakeClient(sock)
        clients.append(client)
        return client

    yield _connect

    for client in clients:
        client.close()


def hello(protocol_version: int = protocol.PROTOCOL_VERSION, restart: bool = False) -> dict:
    return {"type": "hello", "protocolVersion": protocol_version, "restart": restart}


def payload(
    *,
    batch_id: str,
    body: dict,
    seq_source: str = "network",
    endpoint: str = "/youtubei/v1/next",
    run_id: str = "run1",
    video_id: str = "vid1",
) -> dict:
    return {
        "type": "payload",
        "protocolVersion": protocol.PROTOCOL_VERSION,
        "runId": run_id,
        "videoId": video_id,
        "navEpoch": 1,
        "batchId": batch_id,
        "source": seq_source,
        "endpoint": endpoint,
        "capturedAt": "2026-07-29T00:00:00Z",
        "body": body,
    }


def video_done(
    reason: str = "chain_exhausted", run_id: str = "run1", video_id: str = "vid1"
) -> dict:
    return {
        "type": "video_done",
        "protocolVersion": protocol.PROTOCOL_VERSION,
        "runId": run_id,
        "videoId": video_id,
        "navEpoch": 1,
        "reason": reason,
    }


def video_failed(
    error_code: str = "CHAIN_STALLED",
    message: str = "stalled",
    run_id: str = "run1",
    video_id: str = "vid1",
) -> dict:
    return {
        "type": "video_failed",
        "protocolVersion": protocol.PROTOCOL_VERSION,
        "runId": run_id,
        "videoId": video_id,
        "navEpoch": 1,
        "errorCode": error_code,
        "message": message,
    }


PLAYER_PAYLOAD_BODY = {
    "videoDetails": {
        "videoId": "vid1",
        "isLive": None,
        "isUpcoming": None,
        "isLiveContent": False,
        "lengthSeconds": "120",
    }
}
