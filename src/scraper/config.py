"""Fixed paths and CLI defaults, per SPEC-V3 "Fixed paths" and "v1 scope"."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

CHROME_EXECUTABLE = Path(
    "/Users/sor/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)
DATA_DIR = PROJECT_ROOT / "data-dir"
DATA_DIR_TEMPLATE = PROJECT_ROOT / "data-dir-template"
EXTENSION_DIR = PROJECT_ROOT / "extension"
CONTROLLER_SOCKET = PROJECT_ROOT / "run" / "controller.sock"
DATABASE_PATH = PROJECT_ROOT / "data" / "db.sqlite3"
NATIVE_HOST_WRAPPER = PROJECT_ROOT / "bin" / "scraper-native-host"
NATIVE_HOST_MANIFEST_NAME = "com.sor.yts"

DEFAULT_MAX_RECOMMENDATIONS = 100
DEFAULT_INTER_VIDEO_DELAY_MS = 5000
DEFAULT_SCROLL_DELAY_MS = 1000
DEFAULT_DELAY_JITTER = 0.25
