"""Tests for the `controller` CLI's run loop (PLAN.md's Phase 7).

Real Chrome and the real extension are out of reach in a unit test, so these
use a fake "Chrome" executable — a small standalone Python script, in the
same spirit as tests/chrome/test_session.py's fake-chrome scripts — that
connects directly to the controller's Unix socket and speaks the wire
protocol itself, standing in for the extension + native-host bridge
together. It never touches a real browser; it only proves the runner wires
Storage, ControllerServer/VideoSession, and ChromeSession together
correctly. The real end-to-end path is covered separately by a manual run
against real Chrome (see DECISIONS.md, Phase 7).
"""

import json
import stat
import tempfile
from pathlib import Path

import pytest

from scraper.controller import run_controller
from scraper.storage import Storage

FAKE_BRIDGE_SCRIPT = r"""
import json
import socket
import struct
import sys
import time

SOCKET_PATH = None
for arg in sys.argv[1:]:
    if arg.startswith("--test-socket="):
        SOCKET_PATH = arg.split("=", 1)[1]
assert SOCKET_PATH, "fake bridge needs --test-socket=<path>"

SCRIPT_PATH = None
for arg in sys.argv[1:]:
    if arg.startswith("--test-script="):
        SCRIPT_PATH = arg.split("=", 1)[1]
assert SCRIPT_PATH, "fake bridge needs --test-script=<path>"

with open(SCRIPT_PATH) as f:
    script = json.load(f)

for attempt in range(50):
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(SOCKET_PATH)
        break
    except OSError:
        time.sleep(0.1)
else:
    raise SystemExit("could not connect to controller socket")


def send(msg):
    body = json.dumps(msg).encode("utf-8")
    sock.sendall(struct.pack("!I", len(body)) + body)


def recv():
    header = b""
    while len(header) < 4:
        chunk = sock.recv(4 - len(header))
        if not chunk:
            return None
        header += chunk
    (length,) = struct.unpack("!I", header)
    body = b""
    while len(body) < length:
        chunk = sock.recv(length - len(body))
        if not chunk:
            return None
        body += chunk
    return json.loads(body.decode("utf-8"))


for step in script:
    if step["action"] == "send":
        send(step["message"])
    elif step["action"] == "recv":
        recv()
    elif step["action"] == "sleep":
        time.sleep(step["seconds"])

# Idle so the controller can drive the connection lifecycle (idle timeout,
# or the process being terminated by ChromeSession teardown) rather than the
# script racing to exit and closing the socket first.
time.sleep(5)
"""


def _write_fake_bridge(tmp_path: Path) -> Path:
    script = tmp_path / "fake-chrome"
    script.write_text(f"#!/usr/bin/env python3\n{FAKE_BRIDGE_SCRIPT}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _hello():
    return {"type": "hello", "protocolVersion": 1, "restart": False}


def _payload(batch_id, video_id, body):
    return {
        "type": "payload",
        "protocolVersion": 1,
        "runId": "irrelevant",
        "videoId": video_id,
        "navEpoch": 1,
        "batchId": batch_id,
        "source": "initial",
        "endpoint": "/youtubei/v1/next",
        "capturedAt": "2026-07-29T00:00:00Z",
        "body": body,
    }


def _video_done(video_id, reason="chain_exhausted"):
    return {
        "type": "video_done",
        "protocolVersion": 1,
        "runId": "irrelevant",
        "videoId": video_id,
        "navEpoch": 1,
        "reason": reason,
    }


def _video_failed(video_id, error_code, message):
    return {
        "type": "video_failed",
        "protocolVersion": 1,
        "runId": "irrelevant",
        "videoId": video_id,
        "navEpoch": 1,
        "errorCode": error_code,
        "message": message,
    }


PLAYER_PAYLOAD_BODY = {"videoDetails": {"videoId": "vidX", "lengthSeconds": "120"}}


def _happy_path_script(video_id: str) -> list[dict]:
    return [
        {"action": "send", "message": _hello()},
        {"action": "recv"},  # hello_ack
        {
            "action": "send",
            "message": _payload("b1", video_id, PLAYER_PAYLOAD_BODY),
        },
        {"action": "recv"},  # payload_ack
        {"action": "send", "message": _video_done(video_id)},
        {"action": "recv"},  # video_done_ack
        {"action": "recv"},  # stop
    ]


def _failed_path_script(video_id: str) -> list[dict]:
    return [
        {"action": "send", "message": _hello()},
        {"action": "recv"},  # hello_ack
        {
            "action": "send",
            "message": _video_failed(video_id, "CHAIN_STALLED", "no progress"),
        },
        {"action": "recv"},  # video_failed_ack
        {"action": "recv"},  # stop
    ]


def _never_connects_script() -> list[dict]:
    return [{"action": "sleep", "seconds": 30}]


@pytest.fixture
def fake_template(tmp_path):
    template = tmp_path / "template"
    (template / "Default").mkdir(parents=True)
    (template / "Default" / "Preferences").write_text("{}")
    return template


@pytest.fixture
def env(tmp_path, fake_template):
    """Everything run_controller needs, all rooted under tmp_path so the
    test never touches the real project's data-dir, database, or socket."""
    socket_dir = Path(tempfile.mkdtemp(prefix="scraper-runner-test-"))
    return {
        "template": fake_template,
        "data_dir": tmp_path / "data-dir",
        "socket_path": socket_dir / "c.sock",
        "db_path": tmp_path / "db.sqlite3",
        "raw_root": tmp_path / "raw",
        "lock_path": tmp_path / "db.sqlite3.lock",
        "wrapper_path": tmp_path / "bin" / "scraper-native-host",
    }


def _run(env, tmp_path, video_ids_arg, scripts_by_video, **overrides):
    """scripts_by_video maps video_id -> wire script. The fake bridge reads
    which script to run from a file dropped per invocation via a fixed
    per-video-id script directory, keyed by an env-var-free mechanism: the
    launch_flags carry a --test-script pointing at the right file, chosen
    up front since video IDs (and hence launch order) are known to the
    test."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    launch_flags_by_video = {}
    for video_id, script in scripts_by_video.items():
        script_path = scripts_dir / f"{video_id}.json"
        script_path.write_text(json.dumps(script))
        launch_flags_by_video[video_id] = script_path

    # ChromeSession launches one executable per video, but doesn't know
    # which video ahead of time from the caller's side — so the fake
    # executable itself is a tiny dispatcher script picking its own script
    # file by reading the profile's NativeMessagingHosts manifest is
    # overkill; instead every video in one test run shares one script by
    # construction (tests run one video per `_run` call, or use identical
    # scripts across videos where order doesn't matter for the assertion).
    (script_path,) = launch_flags_by_video.values() or (None,)
    executable = _write_fake_bridge(tmp_path)
    launch_flags = (f"--test-socket={env['socket_path']}", f"--test-script={script_path}")

    return run_controller(
        video_ids_arg=video_ids_arg,
        max_recommendations=overrides.get("max_recommendations", 100),
        inter_video_delay_ms=overrides.get("inter_video_delay_ms", 0),
        scroll_delay_ms=overrides.get("scroll_delay_ms", 0),
        delay_jitter=overrides.get("delay_jitter", 0.0),
        max_scroll_rounds=overrides.get("max_scroll_rounds", 2),
        executable=executable,
        data_dir=env["data_dir"],
        template=env["template"],
        wrapper_path=env["wrapper_path"],
        manifest_name="com.sor.yts",
        extension_id="c" * 32,
        launch_flags=launch_flags,
        socket_path=env["socket_path"],
        db_path=env["db_path"],
        raw_root=env["raw_root"],
        lock_path=env["lock_path"],
        handshake_deadline_s=overrides.get("handshake_deadline_s", 5.0),
        graceful_terminate_deadline_s=1.0,
        connection_idle_timeout_s=5.0,
    )


def test_empty_video_ids_errors_without_touching_anything(env, tmp_path):
    exit_code = _run(env, tmp_path, "  , ,  ", {})
    assert exit_code == 2
    assert not env["data_dir"].exists()
    assert not env["db_path"].exists()


def test_single_video_completes(env, tmp_path):
    exit_code = _run(env, tmp_path, "vidA", {"vidA": _happy_path_script("vidA")})
    assert exit_code == 0
    assert not env["data_dir"].exists()  # torn down after the video

    with Storage(env["db_path"], env["raw_root"]) as storage:
        record = storage.get_video("vidA")
    assert record.status == "completed"
    assert record.completion_reason == "chain_exhausted"


def test_video_failure_records_failure_and_run_still_succeeds(env, tmp_path):
    exit_code = _run(env, tmp_path, "vidA", {"vidA": _failed_path_script("vidA")})
    assert exit_code == 0

    with Storage(env["db_path"], env["raw_root"]) as storage:
        record = storage.get_video("vidA")
    assert record.status == "failed"
    assert record.failure_code == "CHAIN_STALLED"


def test_handshake_timeout_aborts_run(env, tmp_path):
    exit_code = _run(
        env,
        tmp_path,
        "vidA",
        {"vidA": _never_connects_script()},
        handshake_deadline_s=0.3,
    )
    assert exit_code == 1
    assert not env["data_dir"].exists()

    with Storage(env["db_path"], env["raw_root"]) as storage:
        assert storage.get_video("vidA") is None  # never recorded as failed


def test_rerun_skips_completed_video(env, tmp_path):
    first = _run(env, tmp_path, "vidA", {"vidA": _happy_path_script("vidA")})
    assert first == 0

    # Second run: same video ID, but the fake bridge script would fail this
    # assertion if it were actually launched (its script expects a fresh
    # video with no batches sent), so a nonempty exit code here would mean
    # the skip logic didn't work rather than a real collection failure.
    second = _run(env, tmp_path, "vidA", {"vidA": _never_connects_script()})
    assert second == 0
    assert not env["data_dir"].exists()


def test_writer_lock_blocks_concurrent_run(env, tmp_path):
    with Storage(env["db_path"], env["raw_root"], env["lock_path"]):
        exit_code = _run(env, tmp_path, "vidA", {"vidA": _happy_path_script("vidA")})
    assert exit_code == 1
