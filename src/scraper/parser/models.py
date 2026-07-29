"""Data shapes produced by the parser. Field names follow SPEC-V3's "Measured
payload structure" section; nothing here is guessed."""

from dataclasses import dataclass
from enum import Enum


class VideoKind(Enum):
    VIDEO = "video"
    PAST_LIVE = "past_live"
    LIVE = "live"
    UPCOMING = "upcoming"


class CommentState(Enum):
    COLLECTED = "collected"
    NONE_PRESENT = "none_present"
    LIVE_CHAT_INSTEAD = "live_chat_instead"
    DISABLED = "disabled"


@dataclass(frozen=True)
class ImageSource:
    url: str
    width: int | None
    height: int | None


@dataclass(frozen=True)
class RecommendationChannel:
    """One channel credited on a recommendation. Usually there is exactly
    one, via `decoratedAvatarViewModel` — SPEC-V3's documented single-avatar
    shape. Collaboration uploads instead use `avatarStackViewModel`, an
    avatar cluster whose "Collaborators" dialog lists each contributing
    channel with its own browseId; SPEC-V3 doesn't document that shape, it
    was found while implementing Phase 1. `handle` is None for collaborators
    because that shape has no clean structured handle field — only a
    combined "@handle • N subscribers" rendered string, which we don't parse
    (see the "rendered strings" gotcha in PLAN.md).

    Schema note for Phase 2: a recommendation can therefore reference zero,
    one, or several channels — the recommendations table cannot hold a single
    nullable channel_id column and needs a join table instead."""

    channel_id: str
    name: str | None
    handle: str | None
    avatar_sources: tuple[ImageSource, ...]


@dataclass(frozen=True)
class RawRecommendation:
    """One kept (LOCKUP_CONTENT_TYPE_VIDEO) sidebar entry, as extracted from a
    single payload. Carries no position — see RecommendationAccumulator."""

    video_id: str
    title: str | None
    channels: tuple[RecommendationChannel, ...]
    view_count_text: str | None
    published_text: str | None
    duration_text: str | None
    duration_accessibility_label: str | None
    thumbnail_sources: tuple[ImageSource, ...]
    animated_preview_sources: tuple[ImageSource, ...]


@dataclass(frozen=True)
class AccumulatedRecommendation(RawRecommendation):
    """A RawRecommendation with positions assigned across the whole
    continuation chain by RecommendationAccumulator."""

    raw_position: int
    normalised_position: int


@dataclass(frozen=True)
class CommentAuthor:
    channel_id: str | None
    display_name: str | None
    avatar_url: str | None
    is_verified: bool
    is_creator: bool
    is_artist: bool


@dataclass(frozen=True)
class Comment:
    comment_id: str
    text: str | None
    published_text: str | None
    reply_level: int | None
    author: CommentAuthor
    like_count_text: str | None
    reply_count_text: str | None
    creator_heart_tooltip: str | None


@dataclass(frozen=True)
class CommentsHeader:
    """A comments header observed in one payload. `count` is nullable because
    YouTube does not always report it (see SPEC-V3, Comments section)."""

    count: int | None
