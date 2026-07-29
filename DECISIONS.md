# Implementation decisions

`SPEC-V3.md` and `PLAN.md` are the authority on what to build. This file logs
places where implementation required filling a gap they leave open, or a
judgment call among reasonable options — so those choices are visible in one
place instead of only scattered across module docstrings, and so they aren't
silently re-litigated later without noticing they were deliberate.

Each entry links to where the reasoning lives in full.

## Phase 1 — Payload parser

**Recommendation position assignment lives in the accumulator, never in
`extract_recommendations`.** PLAN.md splits the two deliverables and states
the accumulator "assigns both raw_position and normalised_position";
`extract_recommendations` stays fully position-free to match that literally.
A single private helper (`_iter_sidebar_entries`) does the only
shape-detection walk, shared by both, so there's exactly one place that
understands the payload's shape.
→ `src/scraper/parser/recommendations.py`, module docstring.

**`RawRecommendation.channels` is a tuple (zero-to-many), not a single
nullable `channel_id`.** Real captures contain collaboration-upload lockups
using `avatarStackViewModel` (a "Collaborators" dialog with several distinct,
real channel IDs) instead of SPEC-V3's documented single-channel
`decoratedAvatarViewModel` shape. Modelling this as zero-to-many channels
avoids discarding legitimate channel data. Confirmed via `AskUserQuestion`
before implementing — user's explicit preference was to not lose data by
nulling it out.
→ `src/scraper/parser/models.py`, `RecommendationChannel` docstring.
→ Consequence for Phase 2: recommendations needed a join table
  (`recommendation_channels`), not a single `channel_id` column.

## Phase 2 — Storage

**Recommendations and comments are deduplicated by `(video_id,
recommended_video_id)` / `(video_id, comment_id)`, not scoped by `run_id`.**
This means a retry naturally reconciles with a prior attempt's rows via the
same UNIQUE constraints, rather than needing per-run bookkeeping to avoid
duplicates across attempts.
→ `src/scraper/storage/schema.sql`, comments above the `recommendations` and
  `comments` tables.

**`discard_partial(video_id, run_id)` accepts `run_id` per PLAN.md's exact
signature but doesn't use it to scope the delete**, for the same reason as
above — the delete is a full wipe by `video_id`.
→ `src/scraper/storage/db.py`, `Storage.discard_partial` docstring.

**The `videos` table only ever holds terminal rows** (completed or failed) —
there is no persisted in-progress status. This is what makes acceptance
criterion 16 (no video stuck running after a crash) true structurally,
rather than by cleanup logic that has to run correctly on every startup.
→ `src/scraper/storage/schema.sql`, comment above the `videos` table.

## Phase 3 — Protocol and controller socket

**Socket framing byte order: big-endian (network byte order), not "native
byte order."** SPEC-V3 pins native byte order specifically for Chrome's
native-messaging stdin/stdout boundary (Phase 4) because Chrome itself
dictates that format. Nothing pins the byte order on the controller's own
Unix socket, so big-endian was chosen as the ordinary, unambiguous default.
→ `src/scraper/controller/framing.py`, module docstring.

**Input caps (payload size, payloads/video, total bytes/video) have no
SPEC-V3-mandated defaults.** SPEC-V3 explicitly leaves circuit-breaker limits
configurable without pinning numbers ("all circuit breakers are
configurable... maximum payloads per video"). The values chosen are
provisional operational tuning, generous relative to SPEC-V3's own observed
sizes (largest single response 275 KiB, whole session 2-3.4 MB).
→ `src/scraper/config.py`, comment above `MAX_PAYLOAD_BYTES`.

**`video_kind` is derived by checking for the `videoDetails` key in a
payload's body**, rather than by a payload/endpoint identifier. SPEC-V3's
message table doesn't name a specific `endpoint` value for the player
response (only documents it as a separate top-level `playerResponse` capture
in the fixture shape, not a wire-protocol field), so routing on the presence
of `videoDetails` — a documented top-level key of `ytInitialPlayerResponse`
— was used instead of inventing an unverified endpoint-string convention.
→ `src/scraper/controller/video_session.py`, `handle_payload`.

**`CommentCollectionFailure` (Phase 1's own exception, not one of SPEC-V3's
error codes) is reported as `SCHEMA_UNRECOGNISED`.** SPEC-V3's fixed error
code list has no code for "comment header present with an implausible
zero-thread count." `SCHEMA_UNRECOGNISED` is the closest existing code: both
mean "the payload didn't match the shape the schema is expected to have,"
just for comments instead of recommendations. This is a judgment call, not
something SPEC-V3 pins down — worth revisiting if a more specific code is
ever wanted.
→ `src/scraper/controller/video_session.py`, `handle_video_done`.

**A protocol-version mismatch aborts by closing the connection without a
`hello_ack`, not by sending an error message.** SPEC-V3's message table has
no dedicated wire message for this case. Silently closing (rather than
degrading, e.g. by proceeding with a mismatched version) satisfies "aborts
the run with a clear error rather than degrading" in the absence of a
defined message to carry that error.
→ `src/scraper/controller/connection.py`, `handle_connection`.
