import sqlite3

from tests.controller.conftest import hello, payload


def test_commit_failure_produces_retryable_nack(server, connect, storage, monkeypatch):
    def raise_operational_error(**kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(storage, "record_batch", raise_operational_error)

    client = connect()
    client.send(hello())
    client.recv()

    client.send(payload(batch_id="b1", body={}))
    nack = client.recv()

    assert nack == {
        "type": "payload_nack",
        "protocolVersion": 1,
        "batchId": "b1",
        "retryable": True,
    }


def test_commit_failure_with_unexpected_error_produces_non_retryable_nack(
    server, connect, storage, monkeypatch
):
    def raise_value_error(**kwargs):
        raise ValueError("unexpected corruption")

    monkeypatch.setattr(storage, "record_batch", raise_value_error)

    client = connect()
    client.send(hello())
    client.recv()

    client.send(payload(batch_id="b1", body={}))
    nack = client.recv()

    assert nack == {
        "type": "payload_nack",
        "protocolVersion": 1,
        "batchId": "b1",
        "retryable": False,
    }


def test_nack_does_not_mark_video_terminal(server, connect, storage, monkeypatch):
    def raise_operational_error(**kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(storage, "record_batch", raise_operational_error)

    client = connect()
    client.send(hello())
    client.recv()
    client.send(payload(batch_id="b1", body={}))
    client.recv()

    assert storage.get_video("vid1") is None
