from scraper.parser import VideoKind, parse_video_kind

EXPECTED_KIND = {
    "video": VideoKind.VIDEO,
    "past_live": VideoKind.PAST_LIVE,
    "live": VideoKind.LIVE,
    "upcoming": VideoKind.UPCOMING,
}


def test_video_kind_matches_fixture(capture_name, capture):
    kind = parse_video_kind(capture["playerResponse"])
    assert kind == EXPECTED_KIND[capture_name]
