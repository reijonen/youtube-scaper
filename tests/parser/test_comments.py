import pytest

from scraper.parser import (
    CommentCollectionFailure,
    CommentsHeader,
    CommentState,
    VideoKind,
    extract_comments,
    find_comments_header,
    resolve_comment_state,
)
from tests.conftest import load_capture, next_response_bodies


def _all_payloads(capture: dict) -> list[dict]:
    return [capture["initialData"], *next_response_bodies(capture)]


def _total_comments(capture: dict) -> int:
    return sum(len(extract_comments(payload)) for payload in _all_payloads(capture))


def test_plain_video_has_31_comments():
    capture = load_capture("video")
    assert _total_comments(capture) == 31


def test_plain_video_header_count_is_reported_progressively():
    capture = load_capture("video")
    headers = [find_comments_header(p) for p in _all_payloads(capture)]
    non_none = [h for h in headers if h is not None]

    assert any(h.count is None for h in non_none), "initial payload reports no count yet"
    assert any(h.count == 74 for h in non_none), "a later payload fills the count in"


def test_past_live_has_comments_collected():
    capture = load_capture("past_live")
    assert _total_comments(capture) > 0

    headers = [find_comments_header(p) for p in _all_payloads(capture)]
    non_none = [h for h in headers if h is not None]
    assert non_none
    assert all(h.count == 571 for h in non_none)


def test_live_has_no_header_and_live_chat_instead_state():
    capture = load_capture("live")
    assert _total_comments(capture) == 0

    headers = [find_comments_header(p) for p in _all_payloads(capture)]
    assert all(h is None for h in headers)

    state = resolve_comment_state(None, threads_collected=0, video_kind=VideoKind.LIVE)
    assert state == CommentState.LIVE_CHAT_INSTEAD


def test_upcoming_has_zero_count_header_and_none_present_state():
    capture = load_capture("upcoming")
    assert _total_comments(capture) == 0

    headers = [find_comments_header(p) for p in _all_payloads(capture)]
    non_none = [h for h in headers if h is not None]
    assert non_none
    assert all(h.count == 0 for h in non_none)

    state = resolve_comment_state(
        CommentsHeader(count=0), threads_collected=0, video_kind=VideoKind.UPCOMING
    )
    assert state == CommentState.NONE_PRESENT


def test_resolve_comment_state_collected_regardless_of_header():
    assert (
        resolve_comment_state(None, threads_collected=5, video_kind=VideoKind.VIDEO)
        == CommentState.COLLECTED
    )


def test_resolve_comment_state_disabled_when_no_header_on_ordinary_video():
    assert (
        resolve_comment_state(None, threads_collected=0, video_kind=VideoKind.VIDEO)
        == CommentState.DISABLED
    )
    assert (
        resolve_comment_state(None, threads_collected=0, video_kind=VideoKind.PAST_LIVE)
        == CommentState.DISABLED
    )


def test_resolve_comment_state_raises_on_header_with_nonzero_or_absent_count_and_no_threads():
    with pytest.raises(CommentCollectionFailure):
        resolve_comment_state(
            CommentsHeader(count=None), threads_collected=0, video_kind=VideoKind.VIDEO
        )

    with pytest.raises(CommentCollectionFailure):
        resolve_comment_state(
            CommentsHeader(count=42), threads_collected=0, video_kind=VideoKind.VIDEO
        )


def test_all_fixtures_comments_have_required_fields(capture_name, capture):
    for payload in _all_payloads(capture):
        for comment in extract_comments(payload):
            assert comment.comment_id
            assert comment.text
