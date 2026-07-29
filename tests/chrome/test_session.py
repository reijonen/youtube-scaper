import os
import signal
import stat
import threading

import pytest

from scraper.chrome.errors import HandshakeTimeout
from scraper.chrome.session import ChromeSession


def _fake_chrome_script(tmp_path, body: str):
    """A stand-in Chrome binary: a shebang script so `process.launch`'s argv
    (`--user-data-dir=...` plus configured flags) lands in its own argv
    rather than being parsed by a real interpreter's option parser."""
    script = tmp_path / "fake-chrome"
    script.write_text(f"#!/usr/bin/env python3\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _session(tmp_path, fake_template, chrome_script) -> ChromeSession:
    return ChromeSession(
        executable=chrome_script,
        data_dir=tmp_path / "data-dir",
        template=fake_template,
        wrapper_path=tmp_path / "bin" / "scraper-native-host",
        manifest_name="com.sor.yts",
        extension_id="c" * 32,
        launch_flags=("--no-first-run",),
        graceful_terminate_deadline_s=2.0,
    )


def test_session_resets_profile_and_launches_process(tmp_path, fake_template):
    chrome = _fake_chrome_script(tmp_path, "import time\ntime.sleep(30)\n")
    session = _session(tmp_path, fake_template, chrome)

    with session:
        assert session.data_dir.is_dir()
        assert (session.data_dir / "Default" / "Preferences").exists()
        assert session._process.poll() is None
        proc = session._process

    assert not session.data_dir.exists()
    assert proc.poll() is not None


def test_handshake_timeout_raises_and_still_tears_down(tmp_path, fake_template):
    chrome = _fake_chrome_script(tmp_path, "import time\ntime.sleep(30)\n")
    session = _session(tmp_path, fake_template, chrome)

    proc_holder = {}
    with pytest.raises(HandshakeTimeout), session:
        proc_holder["proc"] = session._process
        session.wait_for_handshake(threading.Event(), deadline_s=0.1)

    assert not session.data_dir.exists()
    assert proc_holder["proc"].poll() is not None


def test_handshake_event_set_in_time_does_not_raise(tmp_path, fake_template):
    chrome = _fake_chrome_script(tmp_path, "import time\ntime.sleep(30)\n")
    session = _session(tmp_path, fake_template, chrome)
    event = threading.Event()
    event.set()

    with session:
        session.wait_for_handshake(event, deadline_s=2.0)


def test_signal_handlers_installed_then_restored(tmp_path, fake_template):
    chrome = _fake_chrome_script(tmp_path, "import time\ntime.sleep(30)\n")
    session = _session(tmp_path, fake_template, chrome)

    previous_int = signal.getsignal(signal.SIGINT)
    with session:
        assert signal.getsignal(signal.SIGINT) is not previous_int
    assert signal.getsignal(signal.SIGINT) == previous_int


def test_sigterm_during_session_tears_down_via_normal_exit_path(tmp_path, fake_template):
    chrome = _fake_chrome_script(tmp_path, "import time\ntime.sleep(30)\n")
    session = _session(tmp_path, fake_template, chrome)

    proc_holder = {}
    with pytest.raises(SystemExit), session:
        proc_holder["proc"] = session._process
        os.kill(os.getpid(), signal.SIGTERM)

    assert not session.data_dir.exists()
    assert proc_holder["proc"].poll() is not None
