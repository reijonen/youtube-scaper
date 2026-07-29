from pathlib import Path

from tests.storage.conftest import make_comment, make_recommendation


def _counts(storage):
    conn = storage.connection
    return {
        "recommendations": conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0],
        "recommendation_channels": conn.execute(
            "SELECT COUNT(*) FROM recommendation_channels"
        ).fetchone()[0],
        "comments": conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0],
        "raw_payloads": conn.execute("SELECT COUNT(*) FROM raw_payloads").fetchone()[0],
        "received_batches": conn.execute("SELECT COUNT(*) FROM received_batches").fetchone()[0],
    }


def test_record_batch_writes_recommendations_channels_and_comments(storage):
    storage.start_run("run1")

    wrote = storage.record_batch(
        run_id="run1",
        video_id="source1",
        batch_id="batch1",
        seq=0,
        source="initial",
        body={"some": "payload"},
        recommendations=[make_recommendation()],
        comments=[make_comment()],
    )

    assert wrote is True
    assert _counts(storage) == {
        "recommendations": 1,
        "recommendation_channels": 1,
        "comments": 1,
        "raw_payloads": 1,
        "received_batches": 1,
    }


def test_replaying_same_batch_id_is_a_noop(storage):
    storage.start_run("run1")
    kwargs = dict(
        run_id="run1",
        video_id="source1",
        batch_id="batch1",
        seq=0,
        source="initial",
        body={"some": "payload"},
        recommendations=[make_recommendation()],
        comments=[make_comment()],
    )

    first = storage.record_batch(**kwargs)
    second = storage.record_batch(**kwargs)

    assert first is True
    assert second is False
    assert _counts(storage) == {
        "recommendations": 1,
        "recommendation_channels": 1,
        "comments": 1,
        "raw_payloads": 1,
        "received_batches": 1,
    }


def test_replaying_recommendation_across_different_batches_does_not_duplicate(storage):
    """The same recommended video observed again in a later payload (a
    different batch_id, e.g. after a reconnect re-sends overlapping data)
    must not produce a second row — dedup is by (video_id, recommended_video_id),
    not by batch."""
    storage.start_run("run1")

    storage.record_batch(
        run_id="run1",
        video_id="source1",
        batch_id="batch1",
        seq=0,
        source="initial",
        body={},
        recommendations=[make_recommendation(video_id="recommended1")],
    )
    storage.record_batch(
        run_id="run1",
        video_id="source1",
        batch_id="batch2",
        seq=1,
        source="network",
        body={},
        recommendations=[make_recommendation(video_id="recommended1")],
    )

    assert _counts(storage)["recommendations"] == 1
    assert _counts(storage)["received_batches"] == 2


def test_raw_file_is_written_and_referenced(storage):
    storage.start_run("run1")
    storage.record_batch(
        run_id="run1",
        video_id="source1",
        batch_id="batch1",
        seq=3,
        source="network",
        body={"hello": "world"},
    )

    row = storage.connection.execute(
        "SELECT path FROM raw_payloads WHERE batch_id = 'batch1'"
    ).fetchone()
    assert row is not None

    raw_path = Path(row["path"])
    assert raw_path.exists()
    assert raw_path.name == "3-network.json.gz"
