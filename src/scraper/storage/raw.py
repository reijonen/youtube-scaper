"""Raw payload storage, per SPEC-V3's "Raw payload retention" section.

Written to `<raw_root>/<video_id>/<run_id>/<seq>-<source>.json.gz`, flushed
and fsynced, then atomically renamed into place — the caller only records
the path in SQLite (in the same transaction as the parsed rows) after this
returns, so the database can never reference a file that isn't fully
written."""

import gzip
import os
from pathlib import Path


def write_raw_payload(
    raw_root: Path, video_id: str, run_id: str, seq: int, source: str, body_bytes: bytes
) -> Path:
    directory = raw_root / video_id / run_id
    directory.mkdir(parents=True, exist_ok=True)

    final_path = directory / f"{seq}-{source}.json.gz"
    tmp_path = final_path.with_name(final_path.name + ".tmp")

    with open(tmp_path, "wb") as raw_file:
        with gzip.GzipFile(fileobj=raw_file, mode="wb") as gz:
            gz.write(body_bytes)
        raw_file.flush()
        os.fsync(raw_file.fileno())

    os.replace(tmp_path, final_path)
    return final_path
