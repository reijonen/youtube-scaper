from tests.controller.conftest import hello, payload


def test_protocol_version_mismatch_aborts_without_hello_ack(server, connect):
    client = connect()
    client.send(hello(protocol_version=999))
    assert client.recv(timeout=1.0) is None


def test_first_message_must_be_hello(server, connect):
    client = connect()
    client.send(payload(batch_id="b1", body={}))
    assert client.recv(timeout=1.0) is None


def test_ping_gets_pong(server, connect):
    client = connect()
    client.send(hello())
    client.recv()

    client.send({"type": "ping", "protocolVersion": 1, "runId": "run1"})
    pong = client.recv()
    assert pong == {"type": "pong", "protocolVersion": 1}
