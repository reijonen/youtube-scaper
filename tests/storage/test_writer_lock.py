import pytest

from scraper.storage import Storage, WriterLockHeld


def test_second_storage_cannot_open_same_database(tmp_path):
    db_path = tmp_path / "db.sqlite3"
    raw_root = tmp_path / "raw"

    first = Storage(db_path, raw_root).open()
    try:
        with pytest.raises(WriterLockHeld):
            Storage(db_path, raw_root).open()
    finally:
        first.close()


def test_storage_can_reopen_after_close(tmp_path):
    db_path = tmp_path / "db.sqlite3"
    raw_root = tmp_path / "raw"

    first = Storage(db_path, raw_root).open()
    first.close()

    second = Storage(db_path, raw_root).open()
    second.close()
