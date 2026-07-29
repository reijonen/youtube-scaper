"""In-use detection for a Chrome user-data directory.

SPEC-V3 ("Per-video runtime cycle") requires checking whether `data-dir` or
`data-dir-template` is held by a live Chrome process before touching either,
and explicitly rules out `lsof +D` (recursive, slow on a populated profile)
in favour of reading `SingletonLock`. On POSIX, Chromium's ProcessSingleton
creates `SingletonLock` as a symlink whose target is `<hostname>-<pid>`; this
reads that target directly rather than shelling out.

A dead PID (the profile's owning Chrome crashed or was killed without
cleanup) must not block a reset — only a live PID does.
"""

import os
from pathlib import Path

from scraper.chrome.errors import ChromeInUseError


def read_singleton_lock_pid(directory: Path) -> int | None:
    """Return the PID encoded in `directory/SingletonLock`, or None if the
    lock is absent, not a symlink, or its target doesn't parse."""
    lock_path = directory / "SingletonLock"
    try:
        target = os.readlink(lock_path)
    except OSError:
        return None

    _hostname, _, pid_str = target.rpartition("-")
    if not pid_str.isdigit():
        return None
    return int(pid_str)


def is_process_alive(pid: int) -> bool:
    """Best-effort liveness check via signal 0. EPERM means the process
    exists but is owned by someone else, which still counts as alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def directory_in_use(directory: Path) -> bool:
    pid = read_singleton_lock_pid(directory)
    return pid is not None and is_process_alive(pid)


def check_not_in_use(*directories: Path) -> None:
    """Raise ChromeInUseError if a live Chrome process holds any of the
    given directories."""
    for directory in directories:
        if directory_in_use(directory):
            raise ChromeInUseError(
                f"a live Chrome process holds {directory} (SingletonLock); close it first"
            )
