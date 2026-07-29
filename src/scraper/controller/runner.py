"""The `controller` CLI command's per-run, per-video orchestration loop
(SPEC-V3, "v1 scope" and "Per-video runtime cycle"; PLAN.md's Phase 7).

Ties together every earlier phase: Storage (Phase 2) for the completed-video
skip list and the writer lock, ControllerServer/VideoSession (Phase 3) for
one video's protocol state machine, and ChromeSession (Phase 5) for the
reset/launch/handshake/teardown cycle around the real extension (Phase 6).
Nothing here talks to Chrome or the socket directly — it only sequences the
pieces those phases already built.
"""

import logging
import random
import sys
import threading
import time
import uuid
from pathlib import Path

from scraper import config
from scraper.chrome.errors import ChromeInUseError, HandshakeTimeout
from scraper.chrome.session import ChromeSession
from scraper.storage import Storage, WriterLockHeld

from .server import ControllerServer
from .video_session import Caps, VideoSession

logger = logging.getLogger(__name__)

# Controller-side backstop for one video's total wall-clock time. SPEC-V3
# relies on the extension to always report completion or a structured
# failure (Service-worker lifetime: state survives restarts), but that
# assumes the Chrome process and service worker stay alive to do so.
#
# Originally this was tied to the extension's own MAX_PAGE_DURATION_MS
# breaker (a multiple of it, ~30 minutes) on the theory that it only needs
# to catch a MAX_PAGE_DURATION_MS breaker that itself never fires. That
# theory was wrong: a live run hit a *different* failure mode entirely — the
# bridge kept reconnecting every ~30s (CONNECTION_IDLE_TIMEOUT_S) forever,
# each connection going silent with no hello, video_done, or video_failed
# ever following the first one. MAX_PAGE_DURATION_MS never applies here
# because the extension-side collection loop never even gets going, so
# tying the backstop to it left the controller waiting up to 30 minutes on
# a video that was clearly stuck within under a minute — caught only by a
# human killing the process by hand. This is now a much shorter, mostly
# arbitrary ceiling instead: generous for a legitimately slow
# max-recommendations=100 collection, nowhere near 30 minutes for a video
# that's actually stuck. See DECISIONS.md, Phase 7.
_VIDEO_DEADLINE_S = 300.0


def _parse_video_ids(video_ids_arg: str) -> list[str]:
    return [v.strip() for v in video_ids_arg.split(",") if v.strip()]


def _jittered_delay_s(base_ms: int, jitter_fraction: float) -> float:
    delta = base_ms * jitter_fraction * random.uniform(-1, 1)
    return max(0.0, (base_ms + delta) / 1000)


def _watch_for_hello(session: VideoSession) -> threading.Event:
    """A hello, once handled, means the extension completed its handshake —
    see SPEC-V3 acceptance criterion 5. VideoSession has no dedicated event
    for this, so the handler is wrapped rather than adding one-off signalling
    machinery to Phase 3's protocol state machine for a single Phase 7
    caller."""
    hello_seen = threading.Event()
    original_handle_hello = session.handle_hello

    def spy(msg: dict, generation: int) -> dict | list[dict] | None:
        response = original_handle_hello(msg, generation)
        if response is not None:
            hello_seen.set()
        return response

    session.handle_hello = spy  # type: ignore[method-assign]
    return hello_seen


def _run_one_video(
    *,
    run_id: str,
    video_id: str,
    collection_config: dict,
    storage: Storage,
    caps: Caps,
    socket_path: Path,
    connection_idle_timeout_s: float,
    executable: Path,
    data_dir: Path,
    template: Path,
    wrapper_path: Path,
    manifest_name: str,
    extension_id: str,
    launch_flags: tuple[str, ...],
    handshake_deadline_s: float,
    graceful_terminate_deadline_s: float,
) -> None:
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    session = VideoSession(
        run_id=run_id,
        video_id=video_id,
        video_url=video_url,
        config=collection_config,
        storage=storage,
        caps=caps,
    )
    hello_seen = _watch_for_hello(session)

    server = ControllerServer(socket_path, session, idle_timeout=connection_idle_timeout_s)
    server.start()
    chrome_session = ChromeSession(
        executable=executable,
        data_dir=data_dir,
        template=template,
        wrapper_path=wrapper_path,
        manifest_name=manifest_name,
        extension_id=extension_id,
        launch_flags=launch_flags,
        graceful_terminate_deadline_s=graceful_terminate_deadline_s,
    )
    try:
        with chrome_session:
            chrome_session.wait_for_handshake(hello_seen, deadline_s=handshake_deadline_s)
            completed = server.wait_until_done(timeout=_VIDEO_DEADLINE_S)
            if not completed:
                logger.error(
                    "video %s: no completion within controller-side deadline of %.0fs; "
                    "recording as failed",
                    video_id,
                    _VIDEO_DEADLINE_S,
                )
                storage.mark_video_failed(
                    video_id=video_id,
                    run_id=run_id,
                    failure_code="CIRCUIT_BREAKER",
                    failure_message=(
                        f"no completion within controller-side deadline of {_VIDEO_DEADLINE_S:.0f}s"
                    ),
                )
    finally:
        server.stop()


def run_controller(
    *,
    video_ids_arg: str,
    max_recommendations: int,
    inter_video_delay_ms: int,
    scroll_delay_ms: int,
    delay_jitter: float,
    max_scroll_rounds: int,
    wait_for_comments: bool = config.DEFAULT_WAIT_FOR_COMMENTS,
    executable: Path = config.CHROME_EXECUTABLE,
    data_dir: Path = config.DATA_DIR,
    template: Path = config.DATA_DIR_TEMPLATE,
    wrapper_path: Path = config.NATIVE_HOST_WRAPPER,
    manifest_name: str = config.NATIVE_HOST_MANIFEST_NAME,
    extension_id: str = config.EXTENSION_ID,
    launch_flags: tuple[str, ...] = config.CHROME_LAUNCH_FLAGS,
    socket_path: Path = config.CONTROLLER_SOCKET,
    db_path: Path = config.DATABASE_PATH,
    raw_root: Path = config.RAW_PAYLOAD_DIR,
    lock_path: Path = config.DATABASE_WRITER_LOCK_PATH,
    handshake_deadline_s: float = config.CHROME_STARTUP_HANDSHAKE_DEADLINE_S,
    graceful_terminate_deadline_s: float = config.CHROME_GRACEFUL_TERMINATE_DEADLINE_S,
    connection_idle_timeout_s: float = config.CONNECTION_IDLE_TIMEOUT_S,
) -> int:
    """Runs the whole `controller` command. Returns the process exit code."""
    video_ids = _parse_video_ids(video_ids_arg)
    if not video_ids:
        print("error: --video-ids must contain at least one video ID", file=sys.stderr)
        return 2

    try:
        storage = Storage(db_path, raw_root, lock_path).open()
    except WriterLockHeld as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    run_id: str | None = None
    try:
        to_run, skipped = storage.skip_completed(video_ids)
        if skipped:
            print(f"skipping already-completed video IDs: {', '.join(skipped)}")
        if not to_run:
            print("all requested video IDs already completed; nothing to do")
            return 0

        run_id = uuid.uuid4().hex
        storage.start_run(run_id)
        caps = Caps(
            max_payload_bytes=config.MAX_PAYLOAD_BYTES,
            max_payloads_per_video=config.MAX_PAYLOADS_PER_VIDEO,
            max_total_bytes_per_video=config.MAX_TOTAL_BYTES_PER_VIDEO,
        )
        collection_config = {
            "maxRecommendations": max_recommendations,
            "scrollDelayMs": scroll_delay_ms,
            "delayJitter": delay_jitter,
            "pageReadyTimeoutMs": config.PAGE_READY_TIMEOUT_MS,
            "ackTimeoutMs": config.ACK_TIMEOUT_MS,
            "maxScrollRounds": max_scroll_rounds,
            "maxPageDurationMs": config.MAX_PAGE_DURATION_MS,
            "waitForComments": wait_for_comments,
        }

        for index, video_id in enumerate(to_run):
            logger.info("starting video %s (%d/%d)", video_id, index + 1, len(to_run))
            try:
                _run_one_video(
                    run_id=run_id,
                    video_id=video_id,
                    collection_config=collection_config,
                    storage=storage,
                    caps=caps,
                    socket_path=socket_path,
                    connection_idle_timeout_s=connection_idle_timeout_s,
                    executable=executable,
                    data_dir=data_dir,
                    template=template,
                    wrapper_path=wrapper_path,
                    manifest_name=manifest_name,
                    extension_id=extension_id,
                    launch_flags=launch_flags,
                    handshake_deadline_s=handshake_deadline_s,
                    graceful_terminate_deadline_s=graceful_terminate_deadline_s,
                )
            except (ChromeInUseError, HandshakeTimeout) as exc:
                # Both are environmental blockers, not this video's fault,
                # and nothing about the situation changes for the next video
                # in the list (same data_dir, same template) — so this
                # aborts the whole run per SPEC-V3 acceptance criterion 5,
                # rather than recording every remaining video as failed.
                print(f"error: aborting run: {exc}", file=sys.stderr)
                return 1

            if index < len(to_run) - 1:
                delay_s = _jittered_delay_s(inter_video_delay_ms, delay_jitter)
                time.sleep(delay_s)

        return 0
    finally:
        if run_id is not None:
            storage.end_run(run_id)
        storage.close()
