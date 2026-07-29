from scraper.parser import (
    RecommendationAccumulator,
    extract_comments,
    find_comments_header,
    parse_video_kind,
    resolve_comment_state,
)
from tests.conftest import load_capture, next_response_bodies


def _ingest_capture(storage, run_id: str, capture: dict) -> None:
    video_id = capture["playerResponse"]["videoDetails"]["videoId"]
    accumulator = RecommendationAccumulator()
    threads_collected = 0

    payloads = [
        ("initial", capture["initialData"]),
        *(("network", body) for body in next_response_bodies(capture)),
    ]

    for seq, (source, payload) in enumerate(payloads):
        recs = accumulator.ingest_payload(payload)
        comments = extract_comments(payload)
        threads_collected += len(comments)
        storage.record_batch(
            run_id=run_id,
            video_id=video_id,
            batch_id=f"{run_id}-{seq}",
            seq=seq,
            source=source,
            body=payload,
            recommendations=recs,
            comments=comments,
        )

    kind = parse_video_kind(capture["playerResponse"])
    header = None
    for _, payload in payloads:
        candidate = find_comments_header(payload)
        if candidate is not None:
            header = candidate  # prefer the most recent observation, see comments.py
    state = resolve_comment_state(header, threads_collected, kind)

    storage.mark_video_completed(
        video_id=video_id,
        run_id=run_id,
        video_kind=kind,
        comment_state=state,
        reported_comment_count=header.count if header else None,
        completion_reason="chain_exhausted",
    )


def test_plain_video_end_to_end_parse_and_write_then_replay_is_noop(storage):
    capture = load_capture("video")
    storage.start_run("run1")

    _ingest_capture(storage, "run1", capture)

    conn = storage.connection
    assert conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0] == 80
    assert conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 31

    video_id = capture["playerResponse"]["videoDetails"]["videoId"]
    record = storage.get_video(video_id)
    assert record.status == "completed"
    assert record.video_kind == "video"
    assert record.comment_state == "collected"

    # Re-running the exact same capture under the same run must change nothing.
    _ingest_capture(storage, "run1", capture)

    assert conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0] == 80
    assert conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 31


def test_all_fixtures_parse_and_write_without_error(capture_name, storage):
    capture = load_capture(capture_name)
    storage.start_run("run1")
    _ingest_capture(storage, "run1", capture)

    video_id = capture["playerResponse"]["videoDetails"]["videoId"]
    record = storage.get_video(video_id)
    assert record.status == "completed"
