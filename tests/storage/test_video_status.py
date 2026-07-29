from pathlib import Path

from scraper.parser.models import CommentState, VideoKind
from tests.storage.conftest import make_comment, make_recommendation


def test_mark_video_completed_then_skip_completed(storage):
    storage.start_run("run1")
    storage.mark_video_completed(
        video_id="vid1",
        run_id="run1",
        video_kind=VideoKind.VIDEO,
        comment_state=CommentState.COLLECTED,
        reported_comment_count=42,
        completion_reason="chain_exhausted",
    )

    record = storage.get_video("vid1")
    assert record.status == "completed"
    assert record.video_kind == "video"
    assert record.comment_state == "collected"
    assert record.reported_comment_count == 42
    assert record.completion_reason == "chain_exhausted"

    to_run, skipped = storage.skip_completed(["vid1", "vid2"])
    assert skipped == ["vid1"]
    assert to_run == ["vid2"]


def test_failed_video_leaves_reason_raw_files_and_zero_child_rows(storage):
    storage.start_run("run1")
    storage.record_batch(
        run_id="run1",
        video_id="vid1",
        batch_id="batch1",
        seq=0,
        source="initial",
        body={"x": 1},
        recommendations=[make_recommendation(video_id="rec1")],
        comments=[make_comment(comment_id="c1")],
    )

    storage.mark_video_failed(
        video_id="vid1",
        run_id="run1",
        failure_code="SCHEMA_UNRECOGNISED",
        failure_message="no recognised recommendation structure",
    )

    record = storage.get_video("vid1")
    assert record.status == "failed"
    assert record.failure_code == "SCHEMA_UNRECOGNISED"
    assert record.failure_message == "no recognised recommendation structure"

    conn = storage.connection
    assert conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM recommendation_channels").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 0
    # raw file and its row are retained as diagnostic evidence
    assert conn.execute("SELECT COUNT(*) FROM raw_payloads").fetchone()[0] == 1
    raw_path = conn.execute("SELECT path FROM raw_payloads").fetchone()["path"]
    assert Path(raw_path).exists()


def test_failed_video_is_not_skipped_and_is_retried(storage):
    storage.start_run("run1")
    storage.mark_video_failed(
        video_id="vid1",
        run_id="run1",
        failure_code="PAGE_READY_TIMEOUT",
        failure_message="timed out",
    )

    to_run, skipped = storage.skip_completed(["vid1"])
    assert to_run == ["vid1"]
    assert skipped == []


def test_video_completed_then_later_retried_and_marked_failed_updates_single_row(storage):
    storage.start_run("run1")
    storage.mark_video_completed(
        video_id="vid1",
        run_id="run1",
        video_kind=VideoKind.VIDEO,
        comment_state=CommentState.DISABLED,
        reported_comment_count=None,
        completion_reason="chain_exhausted",
    )
    storage.start_run("run2")
    storage.mark_video_failed(
        video_id="vid1",
        run_id="run2",
        failure_code="CHAIN_STALLED",
        failure_message="stalled",
    )

    assert storage.connection.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1
    record = storage.get_video("vid1")
    assert record.status == "failed"
    assert record.run_id == "run2"
    assert record.video_kind is None
