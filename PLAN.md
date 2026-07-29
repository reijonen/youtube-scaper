# Implementation plan

`SPEC-V3.md` is the authority on *what* to build and *why*. This file is the order to
build it in, and the exit criteria for each phase.

Work one phase at a time. Each phase ends with something runnable and tested. Do not start
a phase before the previous one's exit criteria pass.

**Chrome does not enter the picture until Phase 5.** Phases 1–4 are pure Python, testable
with `pytest` and nothing else. Resist the urge to bring the browser forward.

---

## Ground rules

1. **`SPEC-V3.md` decisions are settled.** Do not redesign them. If something in it looks
   wrong or impossible, stop and ask rather than working around it.
2. **Do not guess at YouTube's payload structure.** Four real captures are in
   `gates/captures/`. Every field path must come from those files. If a field you want
   isn't in them, say so rather than inventing a path.
3. **Verify current library versions online** before pinning anything. Do not assume
   versions from memory.
4. **Ask before deviating.** From the spec, from this plan, or from the phase order.
5. Nothing in `gates/` is production code. It is a throwaway probe and its captures. The
   captures are the test fixtures and must be kept; the probe extension is reference
   material for Phase 6 and can be deleted afterwards.

---

## Phase 0 — Scaffolding

Set up the project skeleton. No business logic.

**Layout** (`src` layout, `pyproject.toml` as the single config source):

```
pyproject.toml
.python-version
src/scraper/
  __init__.py
  __main__.py          # dispatches: controller | native-host
  config.py            # paths and CLI defaults from SPEC-V3
  parser/
  storage/
  controller/
  native_host.py
tests/
  conftest.py
extension/             # Phase 6
gates/                 # existing, leave alone
```

Use `uv` for environment and dependency management, `pytest` configured under
`[tool.pytest.ini_options]` in `pyproject.toml`, and `ruff` for lint/format. Verify the
current versions of each before pinning.

Prefer the standard library. This project needs no HTTP client, no ORM, and no async
framework; `sqlite3`, `socket`, `struct`, `subprocess`, `json`, `gzip`, and `logging`
cover almost everything.

**Exit:** `uv run pytest` runs and collects zero tests without error.
`python -m scraper --help` prints usage for both subcommands.

---

## Phase 1 — Payload parser

The most important phase. Everything downstream depends on getting this right, and it is
fully testable without a browser.

### Input

A capture file has this shape:

```json
{
  "pageUrl": "...",
  "initialData": { ... },        // snapshot of ytInitialData at assignment
  "initialDataFinal": { ... },   // IGNORE THIS — see gotchas
  "playerResponse": { ... },     // snapshot of ytInitialPlayerResponse
  "responses": [ { "seq": 6, "endpoint": "/youtubei/v1/next", "body": { ... } } ]
}
```

In production the parser receives `initialData`, `playerResponse`, and each `/next` body
as separate payloads. Write it to parse **one payload at a time** with no knowledge of the
others, and let the caller accumulate. Do not write a function that takes a whole capture
file — that shape only exists in the fixtures.

### Deliverables

- `parse_video_kind(player_response) -> VideoKind` — `video | past_live | live | upcoming`,
  per the Video kind table in SPEC-V3.
- `extract_recommendations(payload) -> list[RawRecommendation]` — works identically on the
  initial payload and on continuation payloads.
- `extract_comments(payload) -> list[Comment]` — requires joining the item tree to the
  entity store, see gotchas.
- `find_comments_header(payload) -> CommentsHeader | None` — presence, and a nullable
  count.
- `resolve_comment_state(header, threads_collected, video_kind) -> CommentState` — per the
  Comment availability table in SPEC-V3.
- An accumulator that deduplicates recommendations by video ID, preserves first-seen
  order, and assigns both `raw_position` and `normalised_position`.

### Gotchas — read these before writing code

- **Sidebar items are `lockupViewModel`, not `compactVideoRenderer`.** Anything you may
  recall about `compactVideoRenderer` is obsolete. The path and every field is documented
  in SPEC-V3 under "Measured payload structure".
- **`metadataRows` is not positionally stable.** Most items carry two rows, some carry
  three. Read rows by their content, never by fixed index.
- **Comments need a two-part join.** The item tree holds `commentViewModel` shells
  containing only keys; the actual content is in the response's entity store as
  `commentEntityPayload`. Join on the comment key. A parser that only walks the item tree
  will find comment IDs and no text.
- **Ignore `initialDataFinal`.** It exists only to prove the snapshot worked. Parsing it
  double-counts everything.
- **Filter to `LOCKUP_CONTENT_TYPE_VIDEO`.** No non-video entry appears in any of the four
  captures, so this path is untested — write it anyway, and make sure `raw_position` still
  reflects the pre-filter index so gaps are visible.
- View counts and publish dates are **rendered strings** (`"704K views"`, `"3 years ago"`).
  Store them as given. Do not attempt to parse them into numbers or dates in this phase.

### Fixtures and assertions

Four captures in `gates/captures/`. **Only `gate-c_video-plain.json` has trustworthy
counts** — the other three were taken with an earlier probe that held a reference to
`ytInitialData` while YouTube mutated it, so their `initialData` is the accumulated end
state rather than the initial payload.

| Fixture | videoId | kind | comment state |
|---|---|---|---|
| `gate-c_video-plain.json` | `KidSMk5sy9E` | `video` | `collected` |
| `gate-c_video-1.json` | `CK1XU0BySDY` | `past_live` | `collected` |
| `gate-c_video-livestream.json` | `vW372tfHf7U` | `live` | `live_chat_instead` |
| `gate-c_video-premiere.json` | `CbwErrmcFWs` | `upcoming` | `none_present` |

Assert **exact counts only on `gate-c_video-plain.json`**: 20 recommendations from the
initial payload, 60 more across the continuations, 80 after deduplication, and 31 comments.

For the other three, assert video kind, comment state, that parsing raises nothing, and
that every recommendation carries a non-empty video ID, title, and channel ID. Do not
assert their totals.

Note the comment header differs across fixtures: `CK1XU0BySDY` reports `["571",
" Comments"]`, `KidSMk5sy9E` reports only `["Comments"]` with no number, and `CbwErrmcFWs`
reports `["0", " Comments"]`. All three must parse.

**Exit:** `uv run pytest` green. The parser produces complete rows from all four fixtures.

---

## Phase 2 — Storage

Schema follows from what Phase 1 actually produces. Write the schema after the parser
exists, not before.

### Deliverables

- `schema.sql` with tables for runs, videos, recommendations, and comments.
- Connection setup: WAL mode, `synchronous=FULL`, a busy timeout, foreign keys on.
  `synchronous=FULL` is deliberate — acknowledgements assert durability, and WAL with
  `NORMAL` does not fsync on commit, which would make that assertion false.
- A single-writer lock held for the lifetime of the run, so a second controller cannot
  start against the same database.
- Idempotent writes: replaying a batch produces no duplicates.
- Raw payload storage: gzipped to
  `data/raw/<video_id>/<run_id>/<seq>-<source>.json.gz`. Write and flush the file
  **before** committing the transaction that records its path, so the database can never
  reference a missing file.
- Video status model: terminal status of completed or failed, failure code and message,
  video kind, comment availability state, nullable reported comment count, and which
  condition ended collection (cap reached or chain exhausted).
- `skip_completed(video_ids) -> (to_run, skipped)` — skips only videos that completed;
  previously failed videos are retried.
- `discard_partial(video_id, run_id)` — deletes a failed video's recommendation and
  comment rows while keeping the video record and its failure reason, and keeping its raw
  files.

### Tests

- Replaying the same batch twice yields the same row count.
- A failed video leaves a failed record, its reason, its raw files, and zero child rows.
- `skip_completed` skips completed videos and returns failed ones for retry.
- Two connections cannot both acquire the writer lock.

**Exit:** `uv run pytest` green. A fixture capture can be parsed and written end to end,
and re-running it changes nothing.

---

## Phase 3 — Protocol and controller socket

Still no Chrome, still no extension. A fake client stands in for the bridge.

### Deliverables

- Message types matching the table in SPEC-V3 exactly — 5 extension-to-controller, 7
  controller-to-extension. Do not add messages that aren't in the spec.
- Length-prefixed framing over the Unix socket. Not newline-delimited.
- Handshake: the extension sends `hello` with no run ID; the controller replies
  `hello_ack` with the authoritative run ID and the single video for that session. A
  protocol version mismatch aborts with a clear error rather than degrading.
- Acknowledgement discipline: `payload_ack` is sent only after the transaction commits;
  a commit failure produces `payload_nack` carrying whether it is retryable.
- Connection generations: the controller tags each bridge connection and ignores messages
  from superseded ones.
- Heartbeat handling.
- Input caps: payload size, payloads per video, total bytes per video. Extension payloads
  are untrusted.

### Tests

Drive the socket with a scripted fake client. Cover: full happy path; commit failure
producing a nack; a second connection superseding the first; an oversized payload being
rejected; a mid-batch disconnect leaving no partial rows committed.

**Exit:** `uv run pytest` green. The controller runs a whole video's lifecycle against a
fake bridge with no browser present.

---

## Phase 4 — Native host bridge

### Deliverables

- Native-messaging framing on stdin/stdout: a 32-bit length in **native byte order**
  (`struct` format `=I`), then UTF-8 JSON.
- **Nothing may ever be written to stdout except protocol frames.** Logs go to stderr.
  A stray `print()` corrupts the stream and breaks the connection in a way that is
  miserable to debug. Consider redirecting `sys.stdout` at startup to make this
  structurally impossible.
- A persistent Unix socket connection to the controller, forwarding both ways.
- Reconnect with bounded exponential backoff. If the controller is unreachable, emit a
  structured controller-unavailable message rather than exiting silently.
- No scheduling logic, no SQLite, no knowledge of videos.

### Tests

Feed framed messages to the process's stdin and assert on framed output. Test a partial
frame arriving in pieces, an oversized frame, and controller-unavailable behaviour.

**Exit:** `uv run pytest` green. The bridge round-trips messages between a fake Chrome and
a real controller.

---

## Phase 5 — Chrome lifecycle

### Deliverables

- **In-use check.** Only `data-dir` and `data-dir-template` matter. An unrelated personal
  Chrome on a different user-data-dir must not block a run. Read the `SingletonLock`
  symlink — its target encodes a PID — and check whether that process is alive. Avoid
  `lsof +D`; it descends recursively and is slow on a populated profile.
- **Reset sequence**, in this exact order: delete `data-dir`; copy the template; delete
  `Singleton*` from the copy; write `data-dir/NativeMessagingHosts/com.sor.yts.json` with
  the absolute wrapper path and the extension origin from config; launch.
  Benchmark `/usr/bin/ditto` against `cp -Rc` (APFS clone) and use whichever is faster.
- Launch with the flags in SPEC-V3, including `--autoplay-policy=document-user-activation-required`
  and `--mute-audio`.
- **Startup deadline.** If the extension has not completed its handshake within a bounded
  time after Chrome starts, abort loudly. A silently unloaded extension must never look
  like an idle-but-healthy run.
- Teardown: graceful terminate, escalate to kill after a deadline, wait for full exit,
  then delete `data-dir`.
- Signal handlers so interruption follows the same shutdown path as a normal exit.
- The `bin/scraper-native-host` wrapper: executable, absolute interpreter path, no
  dependence on `PATH`, cwd, or shell profile.

### Tests

The reset sequence is testable against a fake template directory with no Chrome at all:
assert `Singleton*` removal, manifest contents, and that a stale lock naming a dead PID
does not block while one naming a live PID does.

**Exit:** Chrome launches against a fresh profile and exits cleanly, leaving no
`data-dir` behind. The extension is not built yet, so the startup deadline will fire —
that is the correct behaviour and proves the check works.

---

## Phase 6 — Extension

TypeScript, built with esbuild. Three entry points, no framework, no popup.

### Deliverables

- `manifest.json` per SPEC-V3, with a **newly generated** pinned `key`. Do not reuse the
  probe's key from `gates/keys/` — that is throwaway material. Generate a fresh RSA
  keypair, derive the extension ID from the SHA-256 of the DER public key, and put that ID
  in the native-host manifest config.
- **Capture script**, MAIN world, `run_at: document_start`.
  `gates/probe-extension/capture.js` is a working, verified reference for this — port its
  approach, but remove the `window.__ytProbe` global and the diagnostic helpers.
  - Deep-snapshot `ytInitialData` at assignment. **Never hold a reference** — YouTube
    mutates that object in place as the page paginates. This is verified, not theoretical.
  - Wrap both `fetch` and `XMLHttpRequest`. Capture only `/youtubei/v1/next`; ignore
    `/player`, `/log_event`, `/feedback`, `/player/heartbeat`.
  - Always read response bodies from a **clone**. Consuming the original breaks the page.
  - Buffer captured payloads until the collector signals readiness; nothing captured
    early may be dropped.
- **Collector script**, isolated world: handshake with the service worker, receive from
  the capture script accepting only `event.source === window`, forward in bounded batches
  with one unacknowledged batch in flight, drive scrolling, decide completion.
- **Service worker**: native port, worker tab, navigation with monotonic epochs, message
  routing, heartbeat over the native port with `chrome.alarms` as the scheduling floor,
  state in `chrome.storage.session`, URL validation before every navigation, and treating
  any top-level navigation it did not initiate as a terminal failure.
- esbuild build producing the three bundles.

### Gotchas

- An open native port does **not** keep the service worker alive. Only messages crossing
  it reset the idle timer. The heartbeat is load-bearing, not decorative.
- Service-worker restart is expected, not exceptional. On startup, rehydrate from
  `chrome.storage.session` and re-announce before doing anything else.

**Exit:** The extension loads in the template profile with the expected ID, connects
through the bridge, and completes a handshake with the controller.

---

## Phase 7 — Integration

Run the whole thing and walk the acceptance criteria in SPEC-V3 one by one. Every
numbered criterion should have a demonstration — a test, or a documented manual check
with its result.

Pay particular attention to the ones that are easy to fake and hard to actually satisfy:

- Wrapping `fetch`/`XHR` does not alter page behaviour.
- The extension makes no outbound request to YouTube's internal API.
- Killing the controller mid-batch loses no acknowledged data.
- Killing the service worker mid-video is recovered from, not reported as completion.
- A video yielding no recognised recommendation payload fails rather than completing.

**Exit:** `python -m scraper controller --video-ids <a few real IDs>` collects them end to
end, and a second run of the same IDs skips all of them and says so.
