import json

from scraper.chrome.reset import (
    _copy_command_cache,
    copy_profile,
    reset_profile,
    strip_singleton_entries,
    write_native_messaging_manifest,
)


def test_copy_profile_reproduces_template_contents(fake_template, tmp_path):
    dest = tmp_path / "data-dir"
    copy_profile(fake_template, dest)

    assert (dest / "Default" / "Preferences").read_text() == "{}"
    assert (dest / "SingletonLock").is_symlink()


def test_copy_command_choice_is_cached_per_template(fake_template, tmp_path):
    _copy_command_cache.clear()
    dest = tmp_path / "data-dir"
    copy_profile(fake_template, dest)

    assert fake_template in _copy_command_cache
    chosen = _copy_command_cache[fake_template]

    dest2 = tmp_path / "data-dir-2"
    copy_profile(fake_template, dest2)
    assert _copy_command_cache[fake_template] == chosen


def test_strip_singleton_entries_removes_only_singleton_prefixed_files(tmp_path):
    (tmp_path / "SingletonLock").symlink_to("host-1")
    (tmp_path / "SingletonCookie").write_text("x")
    (tmp_path / "Preferences").write_text("{}")

    strip_singleton_entries(tmp_path)

    assert not (tmp_path / "SingletonLock").exists()
    assert not (tmp_path / "SingletonCookie").exists()
    assert (tmp_path / "Preferences").exists()


def test_write_native_messaging_manifest_shape(tmp_path):
    wrapper = tmp_path / "bin" / "scraper-native-host"
    write_native_messaging_manifest(tmp_path, wrapper, "com.sor.yts", "a" * 32)

    manifest_path = tmp_path / "NativeMessagingHosts" / "com.sor.yts.json"
    manifest = json.loads(manifest_path.read_text())

    assert manifest == {
        "name": "com.sor.yts",
        "description": "Native bridge for YouTube Scraper",
        "path": str(wrapper),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{'a' * 32}/"],
    }


def test_reset_profile_full_sequence(fake_template, tmp_path):
    data_dir = tmp_path / "data-dir"
    data_dir.mkdir()
    (data_dir / "stale-leftover.txt").write_text("from a previous video")

    wrapper = tmp_path / "bin" / "scraper-native-host"
    reset_profile(data_dir, fake_template, wrapper, "com.sor.yts", "b" * 32)

    # Old contents gone, template contents present, lock stripped, manifest written.
    assert not (data_dir / "stale-leftover.txt").exists()
    assert (data_dir / "Default" / "Preferences").read_text() == "{}"
    assert not (data_dir / "SingletonLock").exists()

    manifest = json.loads((data_dir / "NativeMessagingHosts" / "com.sor.yts.json").read_text())
    assert manifest["path"] == str(wrapper)
    assert manifest["allowed_origins"] == [f"chrome-extension://{'b' * 32}/"]
