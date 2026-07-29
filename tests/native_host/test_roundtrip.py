from tests.controller.conftest import PLAYER_PAYLOAD_BODY, hello, payload, video_done


def test_bridge_roundtrips_full_video_lifecycle_with_real_controller(bridge_harness, storage):
    """PLAN.md's Phase 4 exit criterion: "The bridge round-trips messages
    between a fake Chrome and a real controller." """
    chrome, _bridge = bridge_harness

    chrome.send(hello())
    ack = chrome.recv()
    assert ack["type"] == "hello_ack"
    assert ack["videoId"] == "vid1"

    chrome.send(payload(batch_id="b1", body=PLAYER_PAYLOAD_BODY, seq_source="initial"))
    ack = chrome.recv()
    assert ack == {"type": "payload_ack", "protocolVersion": 1, "batchId": "b1"}

    chrome.send(video_done(reason="chain_exhausted"))
    ack = chrome.recv()
    assert ack == {"type": "video_done_ack", "protocolVersion": 1, "videoId": "vid1"}

    stop = chrome.recv()
    assert stop == {"type": "stop", "protocolVersion": 1}

    record = storage.get_video("vid1")
    assert record.status == "completed"


def test_bridge_forwards_multiple_payloads_in_sequence(bridge_harness):
    chrome, _bridge = bridge_harness

    chrome.send(hello())
    chrome.recv()

    for i in range(3):
        chrome.send(payload(batch_id=f"b{i}", body={"i": i}))
        ack = chrome.recv()
        assert ack == {"type": "payload_ack", "protocolVersion": 1, "batchId": f"b{i}"}
