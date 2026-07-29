"""Video kind, per SPEC-V3's "Video kind" table. Read from the snapshotted
ytInitialPlayerResponse only."""

from .models import VideoKind


def parse_video_kind(player_response: dict) -> VideoKind:
    video_details = player_response.get("videoDetails", {})
    microformat = player_response.get("microformat", {}).get("playerMicroformatRenderer", {})
    live_broadcast_details = microformat.get("liveBroadcastDetails", {})

    if video_details.get("isLive") is True:
        return VideoKind.LIVE

    if video_details.get("isUpcoming") is True:
        return VideoKind.UPCOMING

    if video_details.get("isLiveContent") is True and video_details.get("isLive") is not True:
        if "endTimestamp" in live_broadcast_details:
            return VideoKind.PAST_LIVE

    return VideoKind.VIDEO
