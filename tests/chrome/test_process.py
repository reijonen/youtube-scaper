import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

from scraper.chrome import process

_TRAPS_SIGTERM = (
    "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n"
)

_EXITS_ON_SIGTERM = "import time\ntime.sleep(30)\n"


def test_launch_builds_expected_argv():
    with patch.object(process.subprocess, "Popen") as popen:
        process.launch(Path("/chrome"), Path("/data-dir"), ("--flag-a", "--flag-b"))

    argv = popen.call_args.args[0]
    assert argv == ["/chrome", "--user-data-dir=/data-dir", "--flag-a", "--flag-b"]


def test_terminate_waits_for_process_that_exits_on_sigterm():
    proc = subprocess.Popen([sys.executable, "-c", _EXITS_ON_SIGTERM])

    start = time.monotonic()
    process.terminate(proc, graceful_deadline_s=5.0)
    elapsed = time.monotonic() - start

    assert proc.poll() is not None
    assert elapsed < 5.0  # exited promptly on SIGTERM, no need to wait out the deadline


def test_terminate_escalates_to_kill_when_sigterm_is_ignored():
    proc = subprocess.Popen([sys.executable, "-c", _TRAPS_SIGTERM])
    time.sleep(0.2)  # let the signal handler get installed

    start = time.monotonic()
    process.terminate(proc, graceful_deadline_s=0.3)
    elapsed = time.monotonic() - start

    assert proc.poll() is not None
    assert elapsed >= 0.3  # had to wait out the deadline before escalating to SIGKILL


def test_terminate_on_already_exited_process_is_a_noop():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()

    process.terminate(proc, graceful_deadline_s=5.0)  # must not hang or raise
