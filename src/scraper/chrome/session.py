"""Ties the reset sequence, process launch/teardown, and startup-handshake
deadline into one per-video context manager.

SPEC-V3 ("Per-video runtime cycle", "The controller installs signal
handlers..."): interruption must follow the exact same teardown path as a
normal exit — Chrome terminated, `data-dir` deleted. A `with ChromeSession(...)`
block gets this for free: SIGINT/SIGTERM are converted into a raised
`SystemExit`, which unwinds the `with` block like any other exception and
runs `__exit__` the same way a clean run or a raised error would.
"""

import shutil
import signal
import threading
from pathlib import Path

from scraper.chrome import process, reset
from scraper.chrome.errors import HandshakeTimeout
from scraper.chrome.singleton_lock import check_not_in_use

_HANDLED_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class ChromeSession:
    def __init__(
        self,
        *,
        executable: Path,
        data_dir: Path,
        template: Path,
        wrapper_path: Path,
        manifest_name: str,
        extension_id: str,
        launch_flags: tuple[str, ...],
        graceful_terminate_deadline_s: float,
    ) -> None:
        self.executable = executable
        self.data_dir = data_dir
        self.template = template
        self.wrapper_path = wrapper_path
        self.manifest_name = manifest_name
        self.extension_id = extension_id
        self.launch_flags = launch_flags
        self.graceful_terminate_deadline_s = graceful_terminate_deadline_s
        self._process = None
        self._previous_handlers: dict[int, object] = {}

    def __enter__(self) -> ChromeSession:
        check_not_in_use(self.data_dir, self.template)
        reset.reset_profile(
            self.data_dir,
            self.template,
            self.wrapper_path,
            self.manifest_name,
            self.extension_id,
        )
        self._process = process.launch(self.executable, self.data_dir, self.launch_flags)
        self._install_signal_handlers()
        return self

    def wait_for_handshake(self, handshake_event: threading.Event, deadline_s: float) -> None:
        """Block until `handshake_event` is set or `deadline_s` elapses. A
        timeout means the extension never loaded or never connected — SPEC-V3
        requires this to "abort loudly" rather than proceed as if idle-but-
        healthy, so this raises rather than returning a bool."""
        if not handshake_event.wait(timeout=deadline_s):
            raise HandshakeTimeout(
                f"extension did not complete handshake within {deadline_s}s of Chrome starting"
            )

    def __exit__(self, exc_type, exc, tb) -> None:
        self._restore_signal_handlers()
        if self._process is not None:
            process.terminate(self._process, self.graceful_terminate_deadline_s)
            self._process = None
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def _install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            raise SystemExit(128 + signum)

        for sig in _HANDLED_SIGNALS:
            self._previous_handlers[sig] = signal.signal(sig, handler)

    def _restore_signal_handlers(self) -> None:
        for sig, previous in self._previous_handlers.items():
            signal.signal(sig, previous)
        self._previous_handlers.clear()
