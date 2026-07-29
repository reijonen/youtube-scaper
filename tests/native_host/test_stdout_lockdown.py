"""_lock_down_stdout manipulates the real process's fd 1/fd 2, which isn't
safe to do inside the pytest process itself (it would corrupt pytest's own
output capturing). Verified via a subprocess instead, with stdout and
stderr captured on separate pipes so we can assert nothing but the intended
frame reaches stdout."""

import struct
import subprocess
import sys

_SCRIPT = """
import sys
from scraper.native_host import _lock_down_stdout, write_native_frame

raw_stdout = _lock_down_stdout()
print("this must not corrupt the protocol stream", file=sys.stdout)
write_native_frame(raw_stdout, b"hello", 1024)
"""


def test_stray_print_does_not_corrupt_stdout_and_is_redirected_to_stderr():
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        timeout=10,
    )

    assert proc.stdout == struct.pack("=I", 5) + b"hello"
    assert b"this must not corrupt the protocol stream" in proc.stderr
