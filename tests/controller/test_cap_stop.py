"""The controller — not the extension — knows the true stored (deduped)
recommendation count, so it is the one that decides when
`--max-recommendations` has been reached and tells the extension to stop,
rather than the extension inferring it from raw sidebar JSON. See
video_session.py, handle_payload, and DECISIONS.md's Phase 6 entry."""

import pytest

from scraper.controller import VideoSession
from tests.controller.conftest import TEST_CAPS, hello, payload


def _sidebar_payload(video_ids: list[str]) -> dict:
    contents = [
        {
            "lockupViewModel": {
                "contentId": video_id,
                "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
                "metadata": {},
                "contentImage": {},
            }
        }
        for video_id in video_ids
    ]
    return {
        "contents": {
            "twoColumnWatchNextResults": {
                "secondaryResults": {
                    "secondaryResults": {
                        "results": [{"itemSectionRenderer": {"contents": contents}}]
                    }
                }
            }
        }
    }


@pytest.fixture
def session(storage):
    storage.start_run("run1")
    return VideoSession(
        run_id="run1",
        video_id="vid1",
        video_url="https://www.youtube.com/watch?v=vid1",
        config={"maxRecommendations": 2},
        storage=storage,
        caps=TEST_CAPS,
    )


def test_ack_followed_by_stop_when_cap_reached(server, connect):
    client = connect()
    client.send(hello())
    client.recv()

    client.send(payload(batch_id="b1", body=_sidebar_payload(["v1", "v2"])))
    ack = client.recv()
    assert ack == {"type": "payload_ack", "protocolVersion": 1, "batchId": "b1"}

    stop = client.recv()
    assert stop == {"type": "stop", "protocolVersion": 1}


def test_no_stop_before_cap_reached(server, connect):
    client = connect()
    client.send(hello())
    client.recv()

    client.send(payload(batch_id="b1", body=_sidebar_payload(["v1"])))
    ack = client.recv()
    assert ack == {"type": "payload_ack", "protocolVersion": 1, "batchId": "b1"}

    assert client.recv(timeout=0.3) is None


def test_cap_stop_sent_only_once(server, connect):
    client = connect()
    client.send(hello())
    client.recv()

    client.send(payload(batch_id="b1", body=_sidebar_payload(["v1", "v2", "v3"])))
    client.recv()  # ack
    client.recv()  # stop

    client.send(payload(batch_id="b2", body=_sidebar_payload(["v4"])))
    ack = client.recv()
    assert ack == {"type": "payload_ack", "protocolVersion": 1, "batchId": "b2"}
    assert client.recv(timeout=0.3) is None  # no second stop
