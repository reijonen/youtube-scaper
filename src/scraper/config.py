"""Fixed paths and CLI defaults, per SPEC-V3 "Fixed paths" and "v1 scope"."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

CHROME_EXECUTABLE = Path("/Users/sor/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
DATA_DIR = PROJECT_ROOT / "data-dir"
DATA_DIR_TEMPLATE = PROJECT_ROOT / "data-dir-template"
EXTENSION_DIR = PROJECT_ROOT / "extension"
CONTROLLER_SOCKET = PROJECT_ROOT / "run" / "controller.sock"
DATABASE_PATH = PROJECT_ROOT / "data" / "db.sqlite3"
DATABASE_WRITER_LOCK_PATH = PROJECT_ROOT / "data" / "db.sqlite3.lock"
RAW_PAYLOAD_DIR = PROJECT_ROOT / "data" / "raw"
NATIVE_HOST_WRAPPER = PROJECT_ROOT / "bin" / "scraper-native-host"
NATIVE_HOST_MANIFEST_NAME = "com.sor.yts"

DEFAULT_MAX_RECOMMENDATIONS = 100
DEFAULT_INTER_VIDEO_DELAY_MS = 5000
DEFAULT_SCROLL_DELAY_MS = 1000
DEFAULT_DELAY_JITTER = 0.25

# Controller-side input caps (SPEC-V3, Input hardening: "The controller
# enforces bounded caps on payload size, payloads per video, and total bytes
# per video"). SPEC-V3 explicitly leaves circuit-breaker limits configurable
# without pinning defaults ("All circuit breakers are configurable... maximum
# payloads per video"); these values are provisional operational tuning, not
# derived from any documented number, and are generous relative to the
# observed sizes in SPEC-V3 ("largest single response 275 KiB", "whole video
# session 2-3.4 MB").
MAX_PAYLOAD_BYTES = 32 * 1024 * 1024  # SPEC-V3's own extension-to-host ceiling
MAX_PAYLOADS_PER_VIDEO = 500
MAX_TOTAL_BYTES_PER_VIDEO = 100 * 1024 * 1024

# Idle read timeout on a bridge connection. Not a video-level deadline —
# service-worker restarts are expected (SPEC-V3, Service-worker lifetime),
# so a connection going quiet just means the server stops waiting on that
# socket; the video's in-memory session state survives for a new connection
# to resume.
CONNECTION_IDLE_TIMEOUT_S = 30.0
