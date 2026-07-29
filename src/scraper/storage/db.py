"""SQLite storage, per SPEC-V3's "Storage semantics" and PLAN.md's Phase 2.

Storage takes already-parsed data (RecommendationAccumulator's
AccumulatedRecommendation, and Comment) rather than raw payloads — parsing
is Phase 1's job and stays decoupled from persistence. A batch here
corresponds to one captured payload: one raw file, plus whatever
recommendations and comments that payload's parse yielded.
"""

import json
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scraper.parser.models import (
    AccumulatedRecommendation,
    Comment,
    CommentState,
    ImageSource,
    VideoKind,
)

from .lock import WriterLock
from .raw import write_raw_payload

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_BUSY_TIMEOUT_MS = 5000


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _image_sources_json(sources: Sequence[ImageSource]) -> str:
    return json.dumps([{"url": s.url, "width": s.width, "height": s.height} for s in sources])


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with the pragmas SPEC-V3 requires and ensure the
    schema exists. `synchronous=FULL` is deliberate, not a default left in
    place — see PLAN.md's Phase 2 deliverables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the controller (Phase 3) calls into Storage
    # from per-connection threads. Storage serializes access itself via
    # self._lock, so this is safe — see Storage's docstring.
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA_PATH.read_text())
    return conn


@dataclass(frozen=True)
class VideoRecord:
    video_id: str
    run_id: str
    status: str
    failure_code: str | None
    failure_message: str | None
    video_kind: str | None
    comment_state: str | None
    reported_comment_count: int | None
    completion_reason: str | None


class Storage:
    """Owns the writer lock and the connection for the lifetime of a run.

    Also serializes all connection access with an internal RLock: the
    controller (Phase 3) calls into Storage from multiple per-connection
    threads, and a single sqlite3.Connection is not safe for concurrent use
    from more than one thread at a time even with check_same_thread=False.
    This is a separate concern from the cross-process WriterLock — that one
    keeps a second controller process out; this one keeps this process's own
    threads from interleaving."""

    def __init__(self, db_path: Path, raw_root: Path, lock_path: Path | None = None) -> None:
        self._db_path = db_path
        self._raw_root = raw_root
        self._writer_lock = WriterLock(lock_path or db_path.with_name(db_path.name + ".lock"))
        self._thread_lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    def open(self) -> Storage:
        self._writer_lock.acquire()
        try:
            self._conn = connect(self._db_path)
        except Exception:
            self._writer_lock.release()
            raise
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._writer_lock.release()

    def __enter__(self) -> Storage:
        return self.open()

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        assert self._conn is not None, "Storage is not open"
        return self._conn

    # -- Runs -----------------------------------------------------------

    def start_run(self, run_id: str, started_at: str | None = None) -> None:
        with self._thread_lock:
            self.connection.execute(
                "INSERT OR IGNORE INTO runs (run_id, started_at) VALUES (?, ?)",
                (run_id, started_at or _now()),
            )

    def end_run(self, run_id: str, ended_at: str | None = None) -> None:
        with self._thread_lock:
            self.connection.execute(
                "UPDATE runs SET ended_at = ? WHERE run_id = ?", (ended_at or _now(), run_id)
            )

    # -- Batches ----------------------------------------------------------

    def record_batch(
        self,
        *,
        run_id: str,
        video_id: str,
        batch_id: str,
        seq: int,
        source: str,
        body: dict,
        recommendations: Sequence[AccumulatedRecommendation] = (),
        comments: Sequence[Comment] = (),
        received_at: str | None = None,
    ) -> bool:
        """Write one payload's raw file and parsed rows, atomically, only if
        `batch_id` hasn't been recorded before. Returns True if this call
        wrote anything, False if it was a no-op replay."""
        with self._thread_lock:
            conn = self.connection

            already_received = conn.execute(
                "SELECT 1 FROM received_batches WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            if already_received is not None:
                return False

            raw_path = write_raw_payload(
                self._raw_root, video_id, run_id, seq, source, json.dumps(body).encode("utf-8")
            )
            received_at = received_at or _now()

            conn.execute("BEGIN")
            try:
                conn.execute(
                    """INSERT INTO raw_payloads
                       (video_id, run_id, seq, source, batch_id, path, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (video_id, run_id, seq, source, batch_id, str(raw_path), received_at),
                )
                conn.execute(
                    "INSERT INTO received_batches (batch_id, video_id, run_id, received_at) "
                    "VALUES (?, ?, ?, ?)",
                    (batch_id, video_id, run_id, received_at),
                )

                for rec in recommendations:
                    self._insert_recommendation(conn, video_id, run_id, rec)

                for comment in comments:
                    self._insert_comment(conn, video_id, run_id, comment)

                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

            return True

    @staticmethod
    def _insert_recommendation(
        conn: sqlite3.Connection, video_id: str, run_id: str, rec: AccumulatedRecommendation
    ) -> None:
        cur = conn.execute(
            """INSERT OR IGNORE INTO recommendations
               (video_id, run_id, recommended_video_id, title, view_count_text, published_text,
                duration_text, duration_accessibility_label, thumbnail_sources_json,
                animated_preview_sources_json, raw_position, normalised_position)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                video_id,
                run_id,
                rec.video_id,
                rec.title,
                rec.view_count_text,
                rec.published_text,
                rec.duration_text,
                rec.duration_accessibility_label,
                _image_sources_json(rec.thumbnail_sources),
                _image_sources_json(rec.animated_preview_sources),
                rec.raw_position,
                rec.normalised_position,
            ),
        )
        if cur.rowcount == 0:
            return  # already existed (idempotent replay) — leave its channels alone

        recommendation_id = cur.lastrowid
        for position, channel in enumerate(rec.channels):
            conn.execute(
                """INSERT OR IGNORE INTO recommendation_channels
                   (recommendation_id, position, channel_id, name, handle, avatar_sources_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    recommendation_id,
                    position,
                    channel.channel_id,
                    channel.name,
                    channel.handle,
                    _image_sources_json(channel.avatar_sources),
                ),
            )

    @staticmethod
    def _insert_comment(
        conn: sqlite3.Connection, video_id: str, run_id: str, comment: Comment
    ) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO comments
               (video_id, run_id, comment_id, text, published_text, reply_level,
                author_channel_id, author_display_name, author_avatar_url,
                author_is_verified, author_is_creator, author_is_artist,
                like_count_text, reply_count_text, creator_heart_tooltip)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                video_id,
                run_id,
                comment.comment_id,
                comment.text,
                comment.published_text,
                comment.reply_level,
                comment.author.channel_id,
                comment.author.display_name,
                comment.author.avatar_url,
                int(comment.author.is_verified),
                int(comment.author.is_creator),
                int(comment.author.is_artist),
                comment.like_count_text,
                comment.reply_count_text,
                comment.creator_heart_tooltip,
            ),
        )

    # -- Video status -----------------------------------------------------

    def mark_video_completed(
        self,
        *,
        video_id: str,
        run_id: str,
        video_kind: VideoKind,
        comment_state: CommentState,
        reported_comment_count: int | None,
        completion_reason: str,
        now: str | None = None,
    ) -> None:
        now = now or _now()
        with self._thread_lock:
            self.connection.execute(
                """INSERT INTO videos
                   (video_id, run_id, status, failure_code, failure_message, video_kind,
                    comment_state, reported_comment_count, completion_reason, created_at,
                    updated_at)
                   VALUES (?, ?, 'completed', NULL, NULL, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (video_id) DO UPDATE SET
                     run_id = excluded.run_id,
                     status = 'completed',
                     failure_code = NULL,
                     failure_message = NULL,
                     video_kind = excluded.video_kind,
                     comment_state = excluded.comment_state,
                     reported_comment_count = excluded.reported_comment_count,
                     completion_reason = excluded.completion_reason,
                     updated_at = excluded.updated_at""",
                (
                    video_id,
                    run_id,
                    video_kind.value,
                    comment_state.value,
                    reported_comment_count,
                    completion_reason,
                    now,
                    now,
                ),
            )

    def mark_video_failed(
        self,
        *,
        video_id: str,
        run_id: str,
        failure_code: str,
        failure_message: str,
        now: str | None = None,
    ) -> None:
        """Writes the video as failed and discards any partial recommendation
        and comment rows, atomically. Raw files and their raw_payloads rows
        are kept — they're the diagnostic evidence for the failure."""
        now = now or _now()
        with self._thread_lock:
            conn = self.connection
            conn.execute("BEGIN")
            try:
                conn.execute(
                    """INSERT INTO videos
                       (video_id, run_id, status, failure_code, failure_message, video_kind,
                        comment_state, reported_comment_count, completion_reason, created_at,
                        updated_at)
                       VALUES (?, ?, 'failed', ?, ?, NULL, NULL, NULL, NULL, ?, ?)
                       ON CONFLICT (video_id) DO UPDATE SET
                         run_id = excluded.run_id,
                         status = 'failed',
                         failure_code = excluded.failure_code,
                         failure_message = excluded.failure_message,
                         video_kind = NULL,
                         comment_state = NULL,
                         reported_comment_count = NULL,
                         completion_reason = NULL,
                         updated_at = excluded.updated_at""",
                    (video_id, run_id, failure_code, failure_message, now, now),
                )
                self.discard_partial(video_id, run_id)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def discard_partial(self, video_id: str, run_id: str) -> None:
        """Deletes video_id's recommendation and comment rows (channels
        cascade). `run_id` is accepted to match PLAN.md's signature but
        doesn't scope the delete: recommendations and comments are keyed by
        video_id alone (see schema.sql), not by run, precisely so a retry
        reconciles via the same UNIQUE constraints instead of accumulating
        stale rows from a failed attempt. Raw files and raw_payloads rows
        are never touched — they're retained per SPEC-V3."""
        with self._thread_lock:
            conn = self.connection
            conn.execute("DELETE FROM recommendations WHERE video_id = ?", (video_id,))
            conn.execute("DELETE FROM comments WHERE video_id = ?", (video_id,))

    def skip_completed(self, video_ids: Sequence[str]) -> tuple[list[str], list[str]]:
        """Returns (to_run, skipped). Only videos with status='completed'
        are skipped; failed and never-attempted videos are retried."""
        if not video_ids:
            return [], []
        with self._thread_lock:
            placeholders = ",".join("?" for _ in video_ids)
            rows = self.connection.execute(
                f"SELECT video_id FROM videos WHERE status = 'completed' "
                f"AND video_id IN ({placeholders})",
                tuple(video_ids),
            ).fetchall()
        completed_ids = {row["video_id"] for row in rows}
        to_run = [v for v in video_ids if v not in completed_ids]
        skipped = [v for v in video_ids if v in completed_ids]
        return to_run, skipped

    def get_video(self, video_id: str) -> VideoRecord | None:
        with self._thread_lock:
            row = self.connection.execute(
                "SELECT * FROM videos WHERE video_id = ?", (video_id,)
            ).fetchone()
        if row is None:
            return None
        return VideoRecord(
            video_id=row["video_id"],
            run_id=row["run_id"],
            status=row["status"],
            failure_code=row["failure_code"],
            failure_message=row["failure_message"],
            video_kind=row["video_kind"],
            comment_state=row["comment_state"],
            reported_comment_count=row["reported_comment_count"],
            completion_reason=row["completion_reason"],
        )
