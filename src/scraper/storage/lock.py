"""Single-writer lock for the database, held for the lifetime of a run so a
second controller cannot start against the same database (SPEC-V3, Storage
semantics). Backed by flock on a dedicated lock file — held via an open file
descriptor, so it releases automatically if the process dies, and it is
independent of SQLite's own locking so a fresh connection can never bypass
it."""

import fcntl
import os
from pathlib import Path


class WriterLockHeld(Exception):
    pass


class WriterLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise WriterLockHeld(f"writer lock already held: {self._path}") from exc
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = None

    def __enter__(self) -> WriterLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
