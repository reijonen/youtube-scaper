from tests.controller.conftest import hello, payload


def test_oversized_payload_is_rejected_and_trips_circuit_breaker(server, connect, storage):
    client = connect()
    client.send(hello())
    client.recv()

    big_body = {"x": "a" * 2000}  # exceeds TEST_CAPS.max_payload_bytes (1024)
    client.send(payload(batch_id="huge", body=big_body))

    nack = client.recv()
    assert nack == {
        "type": "payload_nack",
        "protocolVersion": 1,
        "batchId": "huge",
        "retryable": False,
    }

    stop = client.recv()
    assert stop["type"] == "stop"

    record = storage.get_video("vid1")
    assert record.status == "failed"
    assert record.failure_code == "CIRCUIT_BREAKER"


def test_too_many_payloads_trips_circuit_breaker(server, connect, storage):
    client = connect()
    client.send(hello())
    client.recv()

    # TEST_CAPS.max_payloads_per_video is 5.
    for i in range(5):
        client.send(payload(batch_id=f"b{i}", body={"i": i}))
        ack = client.recv()
        assert ack["type"] == "payload_ack"

    client.send(payload(batch_id="b5", body={"i": 5}))
    nack = client.recv()
    assert nack["type"] == "payload_nack"
    assert nack["retryable"] is False

    record = storage.get_video("vid1")
    assert record.status == "failed"
    assert record.failure_code == "CIRCUIT_BREAKER"
