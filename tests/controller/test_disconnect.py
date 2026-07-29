import struct
import time

from tests.controller.conftest import hello, payload


def test_truncated_frame_mid_batch_commits_nothing(server, connect, storage):
    client = connect()
    client.send(hello())
    client.recv()

    # Send a length prefix promising 1000 bytes, then only send 10 and
    # disconnect — a mid-frame disconnect. The server must not act on any
    # partial data, and no rows should exist.
    client._sock.sendall(struct.pack("!I", 1000))
    client._sock.sendall(b"1234567890")
    client._sock.close()

    time.sleep(0.2)  # let the server's connection thread notice and return

    conn = storage.connection
    assert conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM received_batches").fetchone()[0] == 0
    assert storage.get_video("vid1") is None


def test_client_disconnect_right_after_send_still_leaves_committed_row(server, connect, storage):
    """Not a partial frame — a full payload was sent and, per the ack
    discipline, is committed before any ack is sent. A client that
    disconnects before reading the ack must not lose that already-durable
    write (SPEC-V3: acknowledged/committed data is never lost)."""
    client = connect()
    client.send(hello())
    client.recv()

    client.send(payload(batch_id="b1", body={}))
    client._sock.close()  # never read the ack

    time.sleep(0.2)

    row = storage.connection.execute(
        "SELECT 1 FROM received_batches WHERE batch_id = 'b1'"
    ).fetchone()
    assert row is not None
