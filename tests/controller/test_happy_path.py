from tests.controller.conftest import PLAYER_PAYLOAD_BODY, hello, payload, video_done, video_failed


def test_full_happy_path(server, connect):
    controller_server, _ = server
    client = connect()

    client.send(hello())
    ack = client.recv()
    assert ack["type"] == "hello_ack"
    assert ack["runId"] == "run1"
    assert ack["videoId"] == "vid1"

    client.send(payload(batch_id="b1", body=PLAYER_PAYLOAD_BODY, seq_source="initial"))
    ack = client.recv()
    assert ack == {"type": "payload_ack", "protocolVersion": 1, "batchId": "b1"}

    client.send(payload(batch_id="b2", body={"unrelated": "data"}))
    ack = client.recv()
    assert ack == {"type": "payload_ack", "protocolVersion": 1, "batchId": "b2"}

    client.send(video_done(reason="chain_exhausted"))
    ack = client.recv()
    assert ack == {"type": "video_done_ack", "protocolVersion": 1, "videoId": "vid1"}

    stop = client.recv()
    assert stop == {"type": "stop", "protocolVersion": 1}

    assert controller_server.wait_until_done(timeout=2)


def test_video_completes_and_is_recorded(server, connect, storage):
    client = connect()
    client.send(hello())
    client.recv()
    client.send(payload(batch_id="b1", body=PLAYER_PAYLOAD_BODY, seq_source="initial"))
    client.recv()
    client.send(video_done(reason="max_recommendations_reached"))
    client.recv()
    client.recv()  # stop

    record = storage.get_video("vid1")
    assert record.status == "completed"
    assert record.video_kind == "video"
    assert record.completion_reason == "max_recommendations_reached"


def test_video_failed_path(server, connect, storage):
    client = connect()
    client.send(hello())
    client.recv()

    client.send(video_failed(error_code="CHAIN_STALLED", message="no progress"))
    ack = client.recv()
    assert ack == {"type": "video_failed_ack", "protocolVersion": 1, "videoId": "vid1"}

    stop = client.recv()
    assert stop["type"] == "stop"

    record = storage.get_video("vid1")
    assert record.status == "failed"
    assert record.failure_code == "CHAIN_STALLED"
    assert record.failure_message == "no progress"
