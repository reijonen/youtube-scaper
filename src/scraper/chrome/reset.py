"""Profile reset sequence: PLAN.md Phase 5, steps 2-5 of SPEC-V3's
"Per-video runtime cycle" (the in-use check in step 1 is `singleton_lock`,
launching in step 6 is `process`).

Delete `data-dir`, copy `data-dir-template` over it with a reliable
recursive macOS copy, strip stale `Singleton*` entries from the copy so an
uncleanly-closed template can't make the runtime Chrome refuse to start, and
write the native-messaging host manifest into the copy.
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

# PLAN.md: "Benchmark /usr/bin/ditto against cp -Rc (APFS clone) and use
# whichever is faster." Both fully replace the destination as a copy of the
# source when the destination doesn't already exist, matching how this is
# always called (data-dir is deleted first). Benchmarked once per template
# per process — re-timing it before every video's reset would triple the
# copy cost it's meant to minimise.
_DITTO = ("/usr/bin/ditto",)
_CP_CLONE = ("cp", "-Rc")
_copy_command_cache: dict[Path, tuple[str, ...]] = {}


def _time_copy(command: tuple[str, ...], src: Path, dst: Path) -> float:
    start = time.monotonic()
    subprocess.run([*command, str(src), str(dst)], check=True, capture_output=True)
    return time.monotonic() - start


def _pick_copy_command(template: Path) -> tuple[str, ...]:
    cached = _copy_command_cache.get(template)
    if cached is not None:
        return cached

    scratch = template.parent / f"{template.name}.copy-benchmark"
    timings = {}
    for command in (_DITTO, _CP_CLONE):
        shutil.rmtree(scratch, ignore_errors=True)
        timings[command] = _time_copy(command, template, scratch)
    shutil.rmtree(scratch, ignore_errors=True)

    winner = min(timings, key=timings.get)
    _copy_command_cache[template] = winner
    return winner


def copy_profile(template: Path, dest: Path) -> None:
    command = _pick_copy_command(template)
    subprocess.run([*command, str(template), str(dest)], check=True, capture_output=True)


def strip_singleton_entries(directory: Path) -> None:
    """Remove `Singleton*` (SingletonLock, SingletonCookie, SingletonSocket)
    left over from however the template's last Chrome session ended."""
    for entry in directory.glob("Singleton*"):
        entry.unlink()


def write_native_messaging_manifest(
    directory: Path, wrapper_path: Path, manifest_name: str, extension_id: str
) -> None:
    """Write `<directory>/NativeMessagingHosts/<manifest_name>.json` per
    SPEC-V3's "Native-messaging host manifest location" — the user-data-dir
    root, not inside `Default/`, because Chrome resolves user-level
    native-messaging manifests relative to `--user-data-dir` itself."""
    manifest_dir = directory / "NativeMessagingHosts"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "name": manifest_name,
        "description": "Native bridge for YouTube Scraper",
        "path": str(wrapper_path),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }
    manifest_path = manifest_dir / f"{manifest_name}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def reset_profile(
    data_dir: Path,
    template: Path,
    wrapper_path: Path,
    manifest_name: str,
    extension_id: str,
) -> None:
    """The full sequence. Callers are responsible for the in-use check
    (`singleton_lock.check_not_in_use`) before calling this — it is not
    repeated here so the pure-copy steps stay independently testable."""
    shutil.rmtree(data_dir, ignore_errors=True)
    copy_profile(template, data_dir)
    strip_singleton_entries(data_dir)
    write_native_messaging_manifest(data_dir, wrapper_path, manifest_name, extension_id)
