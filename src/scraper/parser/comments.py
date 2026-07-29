"""Comment extraction, per SPEC-V3's "Comments" and "Comment availability"
sections.

Comments are split across two structures in the same payload: the item tree
holds `commentThreadRenderer` shells (key only, no content), and the entity
store (`frameworkUpdates.entityBatchUpdate.mutations`) holds the actual
`commentEntityPayload` content, joined on the comment key. Both halves are
searched for generically (via `find_all_by_key`) rather than at one fixed
path, because the shell can arrive nested inside either a
`reloadContinuationItemsCommand` (comments-section header/body reload) or an
`appendContinuationItemsAction` (deeper comment pagination) — the wrapper
varies, the shell and entity-store shapes don't.

Note: a comments header can be observed in more than one payload for the same
video, and its count can change between observations — the initial payload
may report no count at all (`["Comments"]`) while a later continuation
reports the true count (`["74", " Comments"]`), because YouTube fills it in
progressively. find_comments_header is per-payload only; a caller that wants
one answer per video across a whole run must decide how to reconcile
multiple observations (e.g. prefer the most recent non-null count) — that is
not this module's job, mirroring the accumulator split for recommendations.
"""

from ._walk import find_all_by_key
from .errors import CommentCollectionFailure
from .models import Comment, CommentAuthor, CommentsHeader, CommentState, VideoKind


def find_comments_header(payload: dict) -> CommentsHeader | None:
    header = next(find_all_by_key(payload, "commentsHeaderRenderer"), None)
    if header is None:
        return None
    return CommentsHeader(count=_parse_count_text(header))


def _parse_count_text(header: dict) -> int | None:
    runs = header.get("countText", {}).get("runs", [])
    if not runs:
        return None
    cleaned = runs[0].get("text", "").replace(",", "").strip()
    return int(cleaned) if cleaned.isdigit() else None


def _comment_entity_store(payload: dict) -> dict[str, dict]:
    mutations = (
        payload.get("frameworkUpdates", {}).get("entityBatchUpdate", {}).get("mutations", [])
    )
    store = {}
    for mutation in mutations:
        entity = mutation.get("payload", {}).get("commentEntityPayload")
        if entity is not None:
            store[mutation["entityKey"]] = entity
    return store


def _build_comment(entity: dict) -> Comment:
    properties = entity.get("properties", {})
    author_raw = entity.get("author", {})
    toolbar = entity.get("toolbar", {})

    author = CommentAuthor(
        channel_id=author_raw.get("channelId"),
        display_name=author_raw.get("displayName"),
        avatar_url=author_raw.get("avatarThumbnailUrl"),
        is_verified=bool(author_raw.get("isVerified", False)),
        is_creator=bool(author_raw.get("isCreator", False)),
        is_artist=bool(author_raw.get("isArtist", False)),
    )

    return Comment(
        comment_id=properties["commentId"],
        text=properties.get("content", {}).get("content"),
        published_text=properties.get("publishedTime"),
        reply_level=properties.get("replyLevel"),
        author=author,
        like_count_text=toolbar.get("likeCountNotliked"),
        reply_count_text=toolbar.get("replyCountA11y"),
        creator_heart_tooltip=toolbar.get("heartActiveTooltip"),
    )


def extract_comments(payload: dict) -> list[Comment]:
    """Extract top-level comments from one payload by joining item-tree
    shells to the entity store. Works identically regardless of which action
    wrapper carried the shells."""
    entity_store = _comment_entity_store(payload)
    comments = []

    for shell in find_all_by_key(payload, "commentThreadRenderer"):
        inner = shell.get("commentViewModel", {}).get("commentViewModel")
        if inner is None:
            continue
        key = inner.get("commentKey")
        entity = entity_store.get(key)
        if entity is None:
            continue
        comments.append(_build_comment(entity))

    return comments


def resolve_comment_state(
    header: CommentsHeader | None, threads_collected: int, video_kind: VideoKind
) -> CommentState:
    """Per SPEC-V3's Comment availability table. Raises CommentCollectionFailure
    for the one case that isn't a legitimate state: a header present with zero
    threads collected and a count that is neither absent nor exactly zero."""
    if threads_collected > 0:
        return CommentState.COLLECTED

    if header is None:
        if video_kind in (VideoKind.LIVE, VideoKind.UPCOMING):
            return CommentState.LIVE_CHAT_INSTEAD
        return CommentState.DISABLED

    if header.count == 0:
        return CommentState.NONE_PRESENT

    raise CommentCollectionFailure(
        f"comments header present with count={header.count!r} but zero threads collected"
    )
