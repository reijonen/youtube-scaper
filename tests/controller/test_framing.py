import socket
import struct

import pytest

from scraper.controller import framing


@pytest.fixture
def sock_pair():
    a, b = socket.socketpair()
    yield a, b
    a.close()
    b.close()


def test_write_then_read_roundtrip(sock_pair):
    a, b = sock_pair
    framing.write_frame(a, b"hello world")
    assert framing.read_frame(b) == b"hello world"


def test_read_returns_none_on_clean_eof(sock_pair):
    a, b = sock_pair
    a.close()
    assert framing.read_frame(b) is None


def test_read_raises_on_truncated_frame(sock_pair):
    a, b = sock_pair
    a.sendall(struct.pack("!I", 100))
    a.sendall(b"short")
    a.close()
    with pytest.raises(ConnectionError):
        framing.read_frame(b)


def test_read_raises_frame_too_large(sock_pair):
    a, b = sock_pair
    a.sendall(struct.pack("!I", framing.MAX_FRAME_SIZE + 1))
    with pytest.raises(framing.FrameTooLarge):
        framing.read_frame(b)


def test_multiple_frames_in_sequence(sock_pair):
    a, b = sock_pair
    framing.write_frame(a, b"first")
    framing.write_frame(a, b"second")
    assert framing.read_frame(b) == b"first"
    assert framing.read_frame(b) == b"second"
