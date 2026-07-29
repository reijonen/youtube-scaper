"""The per-video protocol state machine. One VideoSession corresponds to one
video collection, as handed out in a single hello_ack (SPEC-V3: "One video is
collected per browser session, so the protocol carries no queue... The
controller hands over exactly one video in the handshake reply").

A video can span more than one bridge *connection* (service-worker restarts
are expected — SPEC-V3, Service-worker lifetime), so this session's state
outlives any single connection. Connection generations are tracked here:
each accepted connection gets a new generation number, and only messages
from the current generation are acted on — this is the mechanism behind
SPEC-V3's "ignoring messages from superseded connections."
"""

import logging
import sqlite3
import threading
from dataclasses import dataclass

from scraper.parser import (
    CommentCollectionFailure,
    RecommendationAccumulator,
    VideoKind,
    extract_comments,
    find_comments_header,
    parse_video_kind,
    resolve_comment_state,
)
from scraper.storage import Storage

from . import protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Caps:
    max_payload_bytes: int
    max_payloads_per_video: int
    max_total_bytes_per_video: int


class VideoSessionError(Exception):
    """Raised for a prerequisite state violation that isn't untrusted-input
    validation (that's ProtocolError) but is still a bug or a sequencing
    error the caller should not paper over — e.g. video_done arriving before
    any player-response payload was ever seen."""


class VideoSession:
    def __init__(
        self,
        *,
        run_id: str,
        video_id: str,
        video_url: str,
        config: dict,
        storage: Storage,
        caps: Caps,
    ) -> None:
        self.run_id = run_id
        self.video_id = video_id
        self.video_url = video_url
        self.config = config
        self._storage = storage
        self._caps = caps

        self._lock = threading.Lock()
        self._generation = 0
        self._current_generation = 0
        self._done = False
        self._cap_stop_sent = False

        self._accumulator = RecommendationAccumulator()
        self._comments_collected = 0
        self._last_header = None
        self._video_kind: VideoKind | None = None
        self._payload_count = 0
        self._total_bytes = 0
        self._seen_batch_ids: set[str] = set()

    # -- Connection lifecycle --------------------------------------------

    def new_connection_generation(self) -> int:
        """Call once per accepted connection. Supersedes any prior
        connection: its subsequent messages will be ignored."""
        with self._lock:
            self._generation += 1
            self._current_generation = self._generation
            return self._generation

    def is_current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._current_generation

    @property
    def is_done(self) -> bool:
        with self._lock:
            return self._done

    # -- Message handlers ---------------------------------------------------
    # Each returns the response message(s) to send — a dict, a list of dicts
    # if more than one frame must go out for a single incoming message, or
    # None if the message should produce no response (superseded
    # generation, or an intentionally unacknowledged case).

    def handle_hello(self, msg: dict, generation: int) -> dict | list[dict] | None:
        if not self.is_current(generation):
            return None
        return protocol.build_hello_ack(self.run_id, self.video_id, self.video_url, self.config)

    def handle_ping(self, msg: dict, generation: int) -> dict | list[dict] | None:
        if not self.is_current(generation):
            return None
        return protocol.build_pong()

    def handle_payload(self, msg: dict, generation: int) -> dict | list[dict] | None:
        if not self.is_current(generation):
            return None

        batch_id = msg["batchId"]
        body = msg["body"]
        encoded_size = len(protocol.encode_message(body))

        cap_violation = self._check_caps(batch_id, encoded_size)
        if cap_violation is not None:
            return cap_violation

        if batch_id in self._seen_batch_ids:
            # Already processed under this session's lifetime (in-memory
            # idempotency, ahead of storage's own batch_id ledger — covers
            # the case where the extension retries a batch it never got an
            # ack for, but the controller had in fact already committed it).
            return protocol.build_payload_ack(batch_id)

        recommendations = self._accumulator.ingest_payload(body)
        comments = extract_comments(body)
        header = find_comments_header(body)
        if header is not None:
            self._last_header = header
        if "videoDetails" in body:
            # SPEC-V3 doesn't name a specific payload/endpoint value for the
            # player response; "videoDetails" is a documented top-level key
            # of ytInitialPlayerResponse (SPEC-V3, "Video kind"), so its
            # presence is used to route to parse_video_kind rather than
            # trusting an unverified endpoint-string convention.
            self._video_kind = parse_video_kind(body)
        self._comments_collected += len(comments)

        try:
            self._storage.record_batch(
                run_id=self.run_id,
                video_id=self.video_id,
                batch_id=batch_id,
                seq=self._payload_count,
                source=msg["source"],
                body=body,
                recommendations=recommendations,
                comments=comments,
            )
        except Exception as exc:
            logger.exception("commit failed for batch %s", batch_id)
            return protocol.build_payload_nack(batch_id, retryable=_is_retryable(exc))

        self._seen_batch_ids.add(batch_id)
        self._payload_count += 1
        self._total_bytes += encoded_size
        ack = protocol.build_payload_ack(batch_id)

        # Only the controller's parser knows the true stored (deduped,
        # non-video-entries-discarded) count; the extension cannot compute
        # this itself. So the cap is enforced here, not in the collector:
        # once reached, `stop` is sent alongside this ack, mid-video, ahead
        # of any video_done from the extension. The collector's contract
        # (extension/src/collector.ts) is to treat a `stop` received before
        # it has itself reported completion as "the cap was hit — stop
        # scrolling and send video_done(reason=max_recommendations_reached)"
        # — as opposed to a `stop` arriving after its own video_done, which
        # is just the ordinary end-of-session teardown signal.
        max_recommendations = self.config.get("maxRecommendations")
        if (
            not self._cap_stop_sent
            and max_recommendations is not None
            and len(self._accumulator.recommendations) >= max_recommendations
        ):
            self._cap_stop_sent = True
            return [ack, protocol.build_stop()]

        return ack

    def _check_caps(self, batch_id: str, encoded_size: int) -> dict | None:
        if encoded_size > self._caps.max_payload_bytes:
            self._fail("CIRCUIT_BREAKER", f"payload of {encoded_size} bytes exceeds cap")
            return protocol.build_payload_nack(batch_id, retryable=False)
        if self._payload_count >= self._caps.max_payloads_per_video:
            self._fail("CIRCUIT_BREAKER", "payloads-per-video cap reached")
            return protocol.build_payload_nack(batch_id, retryable=False)
        if self._total_bytes + encoded_size > self._caps.max_total_bytes_per_video:
            self._fail("CIRCUIT_BREAKER", "total-bytes-per-video cap reached")
            return protocol.build_payload_nack(batch_id, retryable=False)
        return None

    def handle_video_done(self, msg: dict, generation: int) -> dict | None:
        if not self.is_current(generation):
            return None
        if self._video_kind is None:
            raise VideoSessionError(
                "video_done received before any player-response payload was seen"
            )

        try:
            comment_state = resolve_comment_state(
                self._last_header, self._comments_collected, self._video_kind
            )
        except CommentCollectionFailure as exc:
            # CommentCollectionFailure isn't one of SPEC-V3's fixed error
            # codes. SCHEMA_UNRECOGNISED is the closest existing code: both
            # mean "the payload didn't match the shape we expect the schema
            # to have" (SPEC-V3 uses it for recommendations; this is the
            # same signal for comments). This mapping is a judgment call,
            # not something SPEC-V3 pins down.
            self._fail("SCHEMA_UNRECOGNISED", str(exc))
            return protocol.build_video_done_ack(self.video_id)

        self._storage.mark_video_completed(
            video_id=self.video_id,
            run_id=self.run_id,
            video_kind=self._video_kind,
            comment_state=comment_state,
            reported_comment_count=self._last_header.count if self._last_header else None,
            completion_reason=msg["reason"],
        )
        with self._lock:
            self._done = True
        return protocol.build_video_done_ack(self.video_id)

    def handle_video_failed(self, msg: dict, generation: int) -> dict | None:
        if not self.is_current(generation):
            return None
        self._fail(msg["errorCode"], msg["message"])
        return protocol.build_video_failed_ack(self.video_id)

    def _fail(self, error_code: str, message: str) -> None:
        self._storage.mark_video_failed(
            video_id=self.video_id,
            run_id=self.run_id,
            failure_code=error_code,
            failure_message=message,
        )
        with self._lock:
            self._done = True

    def dispatch(self, msg: dict, generation: int) -> dict | None:
        handler = {
            "hello": self.handle_hello,
            "ping": self.handle_ping,
            "payload": self.handle_payload,
            "video_done": self.handle_video_done,
            "video_failed": self.handle_video_failed,
        }[msg["type"]]
        return handler(msg, generation)


def _is_retryable(exc: Exception) -> bool:
    """sqlite3.OperationalError typically means a transient condition (busy,
    locked, disk I/O) — worth retrying. Anything else is treated as
    unexpected/fatal rather than blindly retried."""
    return isinstance(exc, sqlite3.OperationalError)
