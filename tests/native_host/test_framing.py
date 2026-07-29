import os
import struct
import sys

import pytest

from scraper import native_host
from scraper.native_host import NativeFrameTooLarge, read_native_frame, write_native_frame


@pytest.fixture
def pipe_files():
    r_fd, w_fd = os.pipe()
    r = os.fdopen(r_fd, "rb", buffering=0)
    w = os.fdopen(w_fd, "wb", buffering=0)
    yield r, w
    for f in (r, w):
        try:
            f.close()
        except OSError:
            pass


def test_write_then_read_roundtrip(pipe_files):
    r, w = pipe_files
    write_native_frame(w, b"hello world", 1024)
    assert read_native_frame(r, 1024) == b"hello world"


def test_read_returns_none_on_clean_eof(pipe_files):
    r, w = pipe_files
    w.close()
    assert read_native_frame(r, 1024) is None


def test_read_raises_on_truncated_frame(pipe_files):
    r, w = pipe_files
    w.write(struct.pack("=I", 100))
    w.write(b"short")
    w.close()
    with pytest.raises(ConnectionError):
        read_native_frame(r, 1024)


def test_read_raises_frame_too_large(pipe_files):
    r, w = pipe_files
    w.write(struct.pack("=I", 2000))
    with pytest.raises(NativeFrameTooLarge):
        read_native_frame(r, 1024)


def test_write_raises_when_payload_exceeds_max(pipe_files):
    _r, w = pipe_files
    with pytest.raises(NativeFrameTooLarge):
        write_native_frame(w, b"x" * 2000, 1024)


def test_uses_native_byte_order():
    """SPEC-V3 pins native byte order (struct format `=I`) for this
    boundary — distinct from the controller socket's big-endian choice."""
    expected = "<" if sys.byteorder == "little" else ">"
    packed_native = native_host._NATIVE_LENGTH_STRUCT.pack(1)
    packed_explicit = struct.pack(f"{expected}I", 1)
    assert packed_native == packed_explicit
