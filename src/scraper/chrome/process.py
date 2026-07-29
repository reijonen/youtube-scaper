"""Launching and tearing down the Chrome process itself.

Kept separate from `session.ChromeSession` so the subprocess mechanics
(exact flags, terminate/kill escalation) are testable against a stand-in
executable without any of the reset or signal-handling machinery.
"""

import subprocess
import time
from pathlib import Path


def launch(executable: Path, data_dir: Path, extra_flags: tuple[str, ...]) -> subprocess.Popen:
    """Launch Chrome against `data_dir`. `extra_flags` is
    `config.CHROME_LAUNCH_FLAGS`, threaded through as a parameter (rather
    than imported here) so tests can launch a stand-in binary with whatever
    argv it actually understands."""
    argv = [str(executable), f"--user-data-dir={data_dir}", *extra_flags]
    return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=None, stderr=None)


def terminate(process: subprocess.Popen, graceful_deadline_s: float) -> None:
    """Graceful terminate, escalating to a forced kill after
    `graceful_deadline_s`, per SPEC-V3's "Per-video runtime cycle" teardown
    steps. Always waits for full exit before returning."""
    if process.poll() is not None:
        return

    process.terminate()
    deadline = time.monotonic() + graceful_deadline_s
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)

    if process.poll() is None:
        process.kill()

    process.wait()
