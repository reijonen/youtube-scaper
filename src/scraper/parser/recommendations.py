"""Recommendation sidebar extraction, per SPEC-V3's "Recommendations" section.

Design note — where position assignment lives
-----------------------------------------------
PLAN.md splits this into two deliverables: `extract_recommendations`, which
parses one payload with no knowledge of any other, and an accumulator, which
"assigns both raw_position and normalised_position". That split is deliberate
and this module preserves it strictly: RawRecommendation carries no position
fields at all. Position only exists on AccumulatedRecommendation, produced by
RecommendationAccumulator.

The reason this needs care: raw_position is defined by SPEC-V3 as "its index
in the sidebar as YouTube delivered it" — the *whole* sidebar, not one page of
it. But the sidebar arrives as a sequence of separate payloads (the initial
page, then one per continuation), and extract_recommendations only ever sees
one payload at a time. Turning a payload-local index into a position that's
meaningful across the whole chain requires remembering, across payloads, how
many raw slots (including discarded non-video ones) have already gone by.
That memory has to live somewhere with cross-payload state — the accumulator.

Two designs were considered:
1. Have extract_recommendations attach a local index and a local total-entry
   count to each RawRecommendation, and have the accumulator just add running
   offsets. Rejected: it required RawRecommendation to carry bookkeeping
   fields that are meaningless outside position math, which risked leaking
   into the Phase 2 storage schema (which is derived directly from this
   shape), and it blurred PLAN.md's stated division of labour — position
   assignment would happen partly in extract_recommendations after all.
2. Have the accumulator re-walk the raw payload tree itself to count
   discarded slots, independently of extract_recommendations. Rejected: that
   duplicates the initial-vs-continuation shape detection that
   extract_recommendations already does, and two independent implementations
   of "how do I read this payload" will eventually drift when YouTube's
   schema changes.

What's implemented instead: the shape-detection walk (`_iter_sidebar_entries`)
is a single private helper. extract_recommendations calls it and returns only
filtered, position-free RawRecommendation objects, matching PLAN.md's wording
exactly. RecommendationAccumulator.ingest_payload takes the *raw payload*
(not extract_recommendations' output), and calls both
`_iter_sidebar_entries` (to learn how many raw slots this payload contributed,
for the running raw offset) and `extract_recommendations` (to get the kept
items) — so there is exactly one place that understands the payload's shape,
and exactly one place that assigns positions.

Caveat: none of the four gate captures contain a non-video sidebar entry, so
raw_position and normalised_position are numerically identical in every
fixture we can test against. This path is real but unverified against live
data — see PLAN.md's Phase 1 gotchas.
"""

from dataclasses import fields

from .models import (
    AccumulatedRecommendation,
    ImageSource,
    RawRecommendation,
    RecommendationChannel,
)

_VIDEO_CONTENT_TYPE = "LOCKUP_CONTENT_TYPE_VIDEO"

_RAW_RECOMMENDATION_FIELD_NAMES = tuple(f.name for f in fields(RawRecommendation))


def _iter_sidebar_entries(payload: dict) -> list[dict]:
    """Return every raw sidebar entry in `payload`, unfiltered, in order.
    Works on both the initial payload shape and the continuation payload
    shape. Returns [] if neither shape is recognised."""
    secondary_results = (
        payload.get("contents", {})
        .get("twoColumnWatchNextResults", {})
        .get("secondaryResults", {})
        .get("secondaryResults", {})
    )
    results = secondary_results.get("results") or []
    if results:
        contents = results[0].get("itemSectionRenderer", {}).get("contents")
        if contents is not None:
            return contents

    for endpoint in payload.get("onResponseReceivedEndpoints", []):
        action = endpoint.get("appendContinuationItemsAction")
        if action and action.get("targetId") == "watch-next-feed":
            return action.get("continuationItems", [])

    return []


def _image_sources(image: dict) -> tuple[ImageSource, ...]:
    return tuple(
        ImageSource(url=s["url"], width=s.get("width"), height=s.get("height"))
        for s in image.get("sources", [])
        if "url" in s
    )


def _metadata_text_rows(lockup: dict) -> list[dict]:
    content_metadata = (
        lockup.get("metadata", {})
        .get("lockupMetadataViewModel", {})
        .get("metadata", {})
        .get("contentMetadataViewModel", {})
    )
    return [row for row in content_metadata.get("metadataRows", []) if "metadataParts" in row]


def _single_channel_row_name(text_rows: list[dict]) -> str | None:
    for row in text_rows:
        parts = row["metadataParts"]
        if len(parts) == 1 and "text" in parts[0]:
            return parts[0]["text"].get("content")
    return None


def _view_count_and_published(text_rows: list[dict]) -> tuple[str | None, str | None]:
    for row in text_rows:
        parts = row["metadataParts"]
        if len(parts) >= 2 and "text" in parts[0] and "text" in parts[1]:
            return parts[0]["text"].get("content"), parts[1]["text"].get("content")
    return None, None


def _duration(lockup: dict) -> tuple[str | None, str | None]:
    overlays = (
        lockup.get("contentImage", {})
        .get("thumbnailViewModel", {})
        .get("image", {})
        .get("overlays", [])
    )
    for overlay in overlays:
        bottom = overlay.get("thumbnailBottomOverlayViewModel")
        if not bottom:
            continue
        for badge in bottom.get("badges", []):
            badge_vm = badge.get("thumbnailBadgeViewModel")
            if not badge_vm:
                continue
            text = badge_vm.get("text")
            label = badge_vm.get("rendererContext", {}).get("accessibilityContext", {}).get("label")
            return text, label
    return None, None


def _animated_preview_sources(lockup: dict) -> tuple[ImageSource, ...]:
    overlays = (
        lockup.get("contentImage", {})
        .get("thumbnailViewModel", {})
        .get("image", {})
        .get("overlays", [])
    )
    for overlay in overlays:
        animated = overlay.get("animatedThumbnailOverlayViewModel")
        if animated:
            return _image_sources(animated.get("thumbnail", {}))
    return ()


def _browse_endpoint(renderer_context: dict) -> dict:
    return (
        renderer_context.get("commandContext", {})
        .get("onTap", {})
        .get("innertubeCommand", {})
        .get("browseEndpoint", {})
    )


def _channels_from_decorated_avatar(
    decorated_avatar: dict, channel_name: str | None
) -> tuple[RecommendationChannel, ...]:
    """SPEC-V3's documented single-channel shape."""
    browse_endpoint = _browse_endpoint(decorated_avatar.get("rendererContext", {}))
    channel_id = browse_endpoint.get("browseId")
    if channel_id is None:
        return ()
    avatar_sources = _image_sources(
        decorated_avatar.get("avatar", {}).get("avatarViewModel", {}).get("image", {})
    )
    return (
        RecommendationChannel(
            channel_id=channel_id,
            name=channel_name,
            handle=browse_endpoint.get("canonicalBaseUrl"),
            avatar_sources=avatar_sources,
        ),
    )


def _channels_from_avatar_stack(avatar_stack: dict) -> tuple[RecommendationChannel, ...]:
    """A collaboration upload's avatar cluster. Not documented in SPEC-V3 —
    see RecommendationChannel's docstring. Each collaborator is listed in a
    "Collaborators" dialog with its own browseId and avatar; there is no
    clean structured handle field here, only a combined "@handle • N
    subscribers" rendered string, which is left unparsed."""
    list_items = (
        avatar_stack.get("rendererContext", {})
        .get("commandContext", {})
        .get("onTap", {})
        .get("innertubeCommand", {})
        .get("showDialogCommand", {})
        .get("panelLoadingStrategy", {})
        .get("inlineContent", {})
        .get("dialogViewModel", {})
        .get("customContent", {})
        .get("listViewModel", {})
        .get("listItems", [])
    )

    channels = []
    for item in list_items:
        list_item = item.get("listItemViewModel", {})
        browse_endpoint = _browse_endpoint(list_item.get("rendererContext", {}))
        channel_id = browse_endpoint.get("browseId")
        if channel_id is None:
            continue
        avatar_sources = _image_sources(
            list_item.get("leadingAccessory", {}).get("avatarViewModel", {}).get("image", {})
        )
        channels.append(
            RecommendationChannel(
                channel_id=channel_id,
                name=list_item.get("title", {}).get("content"),
                handle=None,
                avatar_sources=avatar_sources,
            )
        )
    return tuple(channels)


def _channels(lockup: dict, channel_name: str | None) -> tuple[RecommendationChannel, ...]:
    image = lockup.get("metadata", {}).get("lockupMetadataViewModel", {}).get("image", {})

    decorated_avatar = image.get("decoratedAvatarViewModel")
    if decorated_avatar is not None:
        return _channels_from_decorated_avatar(decorated_avatar, channel_name)

    avatar_stack = image.get("avatarStackViewModel")
    if avatar_stack is not None:
        return _channels_from_avatar_stack(avatar_stack)

    return ()


def _parse_lockup(lockup: dict) -> RawRecommendation:
    text_rows = _metadata_text_rows(lockup)
    view_count_text, published_text = _view_count_and_published(text_rows)
    duration_text, duration_accessibility_label = _duration(lockup)
    title = (
        lockup.get("metadata", {})
        .get("lockupMetadataViewModel", {})
        .get("title", {})
        .get("content")
    )
    thumbnail_sources = _image_sources(
        lockup.get("contentImage", {}).get("thumbnailViewModel", {}).get("image", {})
    )

    return RawRecommendation(
        video_id=lockup["contentId"],
        title=title,
        channels=_channels(lockup, _single_channel_row_name(text_rows)),
        view_count_text=view_count_text,
        published_text=published_text,
        duration_text=duration_text,
        duration_accessibility_label=duration_accessibility_label,
        thumbnail_sources=thumbnail_sources,
        animated_preview_sources=_animated_preview_sources(lockup),
    )


def extract_recommendations(payload: dict) -> list[RawRecommendation]:
    """Extract kept (LOCKUP_CONTENT_TYPE_VIDEO) recommendations from one
    payload. Works identically on the initial payload and on continuation
    payloads. Carries no position information — see module docstring."""
    recommendations = []
    for entry in _iter_sidebar_entries(payload):
        lockup = entry.get("lockupViewModel")
        if lockup is None:
            continue
        if lockup.get("contentType") != _VIDEO_CONTENT_TYPE:
            continue
        recommendations.append(_parse_lockup(lockup))
    return recommendations


class RecommendationAccumulator:
    """Deduplicates recommendations by video ID across a sequence of payloads
    (the initial payload followed by continuations), preserving first-seen
    order, and assigns raw_position and normalised_position globally across
    that whole sequence. See module docstring for why position assignment
    lives here rather than in extract_recommendations."""

    def __init__(self) -> None:
        self._seen_video_ids: set[str] = set()
        self._raw_offset = 0
        self._normalised_count = 0
        self.recommendations: list[AccumulatedRecommendation] = []

    def ingest_payload(self, payload: dict) -> list[AccumulatedRecommendation]:
        """Process one payload (in arrival order) and return the newly added
        recommendations, if any. Payloads already fully superseded by a
        previous call (e.g. a retried batch) should not be passed twice —
        this accumulator has no batch-id-level idempotency of its own; that
        lives in the storage layer (Phase 2)."""
        entries = _iter_sidebar_entries(payload)
        added: list[AccumulatedRecommendation] = []

        for local_index, entry in enumerate(entries):
            lockup = entry.get("lockupViewModel")
            if lockup is None or lockup.get("contentType") != _VIDEO_CONTENT_TYPE:
                continue

            raw = _parse_lockup(lockup)
            if raw.video_id in self._seen_video_ids:
                continue

            self._seen_video_ids.add(raw.video_id)
            self._normalised_count += 1
            accumulated = AccumulatedRecommendation(
                **{name: getattr(raw, name) for name in _RAW_RECOMMENDATION_FIELD_NAMES},
                raw_position=self._raw_offset + local_index + 1,
                normalised_position=self._normalised_count,
            )
            self.recommendations.append(accumulated)
            added.append(accumulated)

        self._raw_offset += len(entries)
        return added
