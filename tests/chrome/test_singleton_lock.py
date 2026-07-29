import os
import subprocess

import pytest

from scraper.chrome.errors import ChromeInUseError
from scraper.chrome.singleton_lock import (
    check_not_in_use,
    directory_in_use,
    is_process_alive,
    read_singleton_lock_pid,
)


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def test_no_lock_file_returns_none(tmp_path):
    assert read_singleton_lock_pid(tmp_path) is None


def test_lock_target_without_pid_suffix_returns_none(tmp_path):
    (tmp_path / "SingletonLock").symlink_to("not-a-pid")
    assert read_singleton_lock_pid(tmp_path) is None


def test_reads_pid_from_lock_target(tmp_path):
    (tmp_path / "SingletonLock").symlink_to("some-host-4242")
    assert read_singleton_lock_pid(tmp_path) == 4242


def test_own_pid_is_alive():
    assert is_process_alive(os.getpid()) is True


def test_dead_pid_is_not_alive():
    assert is_process_alive(_dead_pid()) is False


def test_directory_in_use_false_when_no_lock(tmp_path):
    assert directory_in_use(tmp_path) is False


def test_directory_in_use_false_when_lock_names_dead_pid(tmp_path):
    (tmp_path / "SingletonLock").symlink_to(f"host-{_dead_pid()}")
    assert directory_in_use(tmp_path) is False


def test_directory_in_use_true_when_lock_names_live_pid(tmp_path):
    (tmp_path / "SingletonLock").symlink_to(f"host-{os.getpid()}")
    assert directory_in_use(tmp_path) is True


def test_check_not_in_use_passes_when_all_clear(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    check_not_in_use(a, b)


def test_check_not_in_use_raises_on_live_lock(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    a.joinpath("SingletonLock").symlink_to(f"host-{os.getpid()}")

    with pytest.raises(ChromeInUseError):
        check_not_in_use(a)
