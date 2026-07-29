import pytest

from scraper.parser.models import (
    AccumulatedRecommendation,
    Comment,
    CommentAuthor,
    RecommendationChannel,
)
from scraper.storage import Storage


@pytest.fixture
def storage(tmp_path):
    db_path = tmp_path / "db.sqlite3"
    raw_root = tmp_path / "raw"
    with Storage(db_path, raw_root) as s:
        yield s


def make_recommendation(video_id: str = "vid1", **overrides) -> AccumulatedRecommendation:
    fields = {
        "video_id": video_id,
        "title": "A title",
        "channels": (
            RecommendationChannel(
                channel_id="UCchannel", name="A channel", handle="/@achannel", avatar_sources=()
            ),
        ),
        "view_count_text": "1K views",
        "published_text": "1 day ago",
        "duration_text": "10:00",
        "duration_accessibility_label": "10 minutes",
        "thumbnail_sources": (),
        "animated_preview_sources": (),
        "raw_position": 1,
        "normalised_position": 1,
    }
    fields.update(overrides)
    return AccumulatedRecommendation(**fields)


def make_comment(comment_id: str = "c1", **overrides) -> Comment:
    fields = {
        "comment_id": comment_id,
        "text": "A comment",
        "published_text": "1 day ago",
        "reply_level": 0,
        "author": CommentAuthor(
            channel_id="UCauthor",
            display_name="Author",
            avatar_url="https://example.com/a.jpg",
            is_verified=False,
            is_creator=False,
            is_artist=False,
        ),
        "like_count_text": "5",
        "reply_count_text": "0 replies",
        "creator_heart_tooltip": None,
    }
    fields.update(overrides)
    return Comment(**fields)
