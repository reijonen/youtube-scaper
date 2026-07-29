import json
from pathlib import Path

import pytest

CAPTURES_DIR = Path(__file__).resolve().parent / "captures"

CAPTURE_FILES = {
    "video": "gate-c_video-plain.json",
    "past_live": "gate-c_video-1.json",
    "live": "gate-c_video-livestream.json",
    "upcoming": "gate-c_video-premiere.json",
}


def load_capture(name: str) -> dict:
    path = CAPTURES_DIR / CAPTURE_FILES[name]
    with path.open() as f:
        return json.load(f)


def next_response_bodies(capture: dict) -> list[dict]:
    """Every /youtubei/v1/next response body, in seq order."""
    responses = sorted(capture["responses"], key=lambda r: r["seq"])
    return [r["body"] for r in responses if r["endpoint"] == "/youtubei/v1/next"]


@pytest.fixture(params=list(CAPTURE_FILES))
def capture_name(request) -> str:
    return request.param


@pytest.fixture
def capture(capture_name) -> dict:
    return load_capture(capture_name)
