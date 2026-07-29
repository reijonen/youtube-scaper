from tests.controller.conftest import hello, payload


def test_second_connection_supersedes_first(server, connect):
    client1 = connect()
    client1.send(hello())
    ack1 = client1.recv()
    assert ack1["type"] == "hello_ack"

    client2 = connect()
    client2.send(hello(restart=True))
    ack2 = client2.recv()
    assert ack2["type"] == "hello_ack"

    # The old connection is superseded: further messages on it get no
    # response at all (the server has stopped acting on its generation).
    client1.send(payload(batch_id="stale", body={}))
    assert client1.recv(timeout=0.5) is None

    # The new connection is current and works normally.
    client2.send(payload(batch_id="fresh", body={}))
    ack = client2.recv()
    assert ack == {"type": "payload_ack", "protocolVersion": 1, "batchId": "fresh"}


def test_stale_connections_payload_is_never_committed(server, connect, storage):
    client1 = connect()
    client1.send(hello())
    client1.recv()

    client2 = connect()
    client2.send(hello(restart=True))
    client2.recv()

    client1.send(payload(batch_id="stale", body={}))
    client1.recv(timeout=0.5)

    row = storage.connection.execute(
        "SELECT 1 FROM received_batches WHERE batch_id = 'stale'"
    ).fetchone()
    assert row is None
