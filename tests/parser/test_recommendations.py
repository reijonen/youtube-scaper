from scraper.parser import RecommendationAccumulator, extract_recommendations
from tests.conftest import load_capture, next_response_bodies


def test_plain_video_initial_payload_has_20_recommendations():
    capture = load_capture("video")
    recs = extract_recommendations(capture["initialData"])
    assert len(recs) == 20


def test_plain_video_continuations_add_60_more():
    capture = load_capture("video")
    total_from_continuations = sum(
        len(extract_recommendations(body)) for body in next_response_bodies(capture)
    )
    assert total_from_continuations == 60


def test_plain_video_accumulator_dedupes_to_80():
    capture = load_capture("video")
    accumulator = RecommendationAccumulator()
    accumulator.ingest_payload(capture["initialData"])
    for body in next_response_bodies(capture):
        accumulator.ingest_payload(body)

    assert len(accumulator.recommendations) == 80

    normalised_positions = [r.normalised_position for r in accumulator.recommendations]
    assert normalised_positions == list(range(1, 81))

    raw_positions = [r.raw_position for r in accumulator.recommendations]
    assert raw_positions == sorted(raw_positions)
    assert len(set(raw_positions)) == len(raw_positions)


def test_accumulator_dedupes_repeated_video_id():
    accumulator = RecommendationAccumulator()
    capture = load_capture("video")
    initial = capture["initialData"]

    first_pass = accumulator.ingest_payload(initial)
    second_pass = accumulator.ingest_payload(initial)

    assert len(first_pass) == 20
    assert second_pass == []
    assert len(accumulator.recommendations) == 20


def test_all_fixtures_yield_wellformed_recommendations(capture_name, capture):
    payloads = [capture["initialData"], *next_response_bodies(capture)]
    all_recs = [rec for payload in payloads for rec in extract_recommendations(payload)]

    for rec in all_recs:
        assert rec.video_id
        assert rec.title
        assert rec.channels
        for channel in rec.channels:
            assert channel.channel_id
