-- One row per collection run.
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT
);

-- Exactly one row per video, written only on terminal status (SPEC-V3,
-- Storage semantics). There is no in-progress state persisted here: a
-- crash mid-video simply leaves no row, which is indistinguishable from
-- "never attempted" for skip_completed's purposes, and satisfies
-- acceptance criterion 16 (no video stuck in a running state).
CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs (run_id),
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    failure_code TEXT,
    failure_message TEXT,
    video_kind TEXT CHECK (video_kind IN ('video', 'past_live', 'live', 'upcoming')),
    comment_state TEXT
        CHECK (comment_state IN ('collected', 'none_present', 'live_chat_instead', 'disabled')),
    reported_comment_count INTEGER,
    completion_reason TEXT
        CHECK (completion_reason IN ('max_recommendations_reached', 'chain_exhausted')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Recommendations are identified by (video_id, recommended_video_id), not
-- by run: a retry that re-observes the same recommendation reconciles via
-- this UNIQUE constraint instead of producing a second row. video_id is not
-- an FK to videos(video_id) because these rows are written while a video is
-- still in progress, before any videos row exists.
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs (run_id),
    recommended_video_id TEXT NOT NULL,
    title TEXT,
    view_count_text TEXT,
    published_text TEXT,
    duration_text TEXT,
    duration_accessibility_label TEXT,
    thumbnail_sources_json TEXT NOT NULL,
    animated_preview_sources_json TEXT NOT NULL,
    raw_position INTEGER NOT NULL,
    normalised_position INTEGER NOT NULL,
    UNIQUE (video_id, recommended_video_id)
);

CREATE INDEX IF NOT EXISTS idx_recommendations_video_id ON recommendations (video_id);

-- A recommendation credits zero, one, or several channels (see
-- RecommendationChannel's docstring in parser/models.py for why).
CREATE TABLE IF NOT EXISTS recommendation_channels (
    id INTEGER PRIMARY KEY,
    recommendation_id INTEGER NOT NULL REFERENCES recommendations (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    channel_id TEXT NOT NULL,
    name TEXT,
    handle TEXT,
    avatar_sources_json TEXT NOT NULL,
    UNIQUE (recommendation_id, channel_id)
);

-- Identified by (video_id, comment_id), not by run, for the same reason as
-- recommendations above.
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs (run_id),
    comment_id TEXT NOT NULL,
    text TEXT,
    published_text TEXT,
    reply_level INTEGER,
    author_channel_id TEXT,
    author_display_name TEXT,
    author_avatar_url TEXT,
    author_is_verified INTEGER NOT NULL DEFAULT 0,
    author_is_creator INTEGER NOT NULL DEFAULT 0,
    author_is_artist INTEGER NOT NULL DEFAULT 0,
    like_count_text TEXT,
    reply_count_text TEXT,
    creator_heart_tooltip TEXT,
    UNIQUE (video_id, comment_id)
);

CREATE INDEX IF NOT EXISTS idx_comments_video_id ON comments (video_id);

-- One row per raw payload written to disk. `path` is only ever written
-- after the raw file itself is flushed and fsynced, so this table can never
-- reference a missing file (SPEC-V3, Raw payload retention).
CREATE TABLE IF NOT EXISTS raw_payloads (
    id INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs (run_id),
    seq INTEGER NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('initial', 'network')),
    batch_id TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (video_id, run_id, seq)
);

-- Idempotency ledger: a batch_id present here has already been fully
-- committed, so a replay after a reconnect is a no-op rather than a
-- duplicate write.
CREATE TABLE IF NOT EXISTS received_batches (
    batch_id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs (run_id),
    received_at TEXT NOT NULL
);
