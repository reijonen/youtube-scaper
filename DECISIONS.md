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

## Phase 4 — Native host bridge

**The bridge's `controller_unavailable` notification has no SPEC-V3-defined
wire shape.** SPEC-V3 names the behaviour ("the bridge reports a structured
controller-unavailable error and the extension pauses") but not a message
schema — and this message can't be one of the controller protocol's 12
types, since by definition the controller is unreachable when it fires. It
mirrors the `{type, protocolVersion}` convention used elsewhere, synthesized
locally by the bridge, never touching the controller socket.
→ `src/scraper/native_host.py`, `CONTROLLER_UNAVAILABLE_MESSAGE_TYPE`.

**Known limitation: while stuck in the reconnect backoff loop, the bridge
doesn't detect Chrome closing stdin.** It only checks for stdin EOF while
actively forwarding (inside `_forward_until_disconnect`); during a
disconnected retry loop it just sleeps and retries. Detecting EOF on a pipe
without consuming real data isn't reliably possible without OS-specific
tricks (there's no cross-platform non-consuming peek for arbitrary pipes,
unlike sockets' `MSG_PEEK`). In practice this is likely fine: Chrome
terminates the native host process directly (signal) rather than relying on
stdin EOF as the sole shutdown signal. Left undone rather than adding a
fragile workaround for a scenario outside PLAN.md's Phase 4 exit criteria.
→ `src/scraper/native_host.py`, `Bridge.run`.

## Phase 5 — Chrome lifecycle

**`EXTENSION_ID` is a 32-character placeholder in `config.py`, not the real
extension ID.** SPEC-V3's "Extension identity" derives the real ID from the
SHA-256 of the pinned RSA public key, which Phase 6 generates — it can't
exist yet. The placeholder matches Chrome's real ID shape (32 lowercase
`a`-`p` letters) so the manifest-writing code and its tests exercise the
actual format; Phase 6 only needs to overwrite this one constant, not change
the interface.
→ `src/scraper/config.py`, comment above `EXTENSION_ID`.

**The copy-method benchmark (`ditto` vs `cp -Rc`) is cached per template
path for the process's lifetime, not re-run before every video.** PLAN.md
asks for a benchmark to pick the faster of the two, but re-timing it before
each video's reset would mean copying the whole template three times
(twice to benchmark, once for real) on every single video instead of once
per controller run.
→ `src/scraper/chrome/reset.py`, `_pick_copy_command`.

**Startup handshake deadline (30s) and graceful-termination deadline before
SIGKILL (10s) have no SPEC-V3-mandated values**, the same situation as
Phase 3's input caps — SPEC-V3 requires both bounds to exist but doesn't pin
numbers. Provisional operational tuning.
→ `src/scraper/config.py`, comment above `CHROME_STARTUP_HANDSHAKE_DEADLINE_S`.

**`ChromeSession`'s in-use check only covers `data_dir` and `template` at
`__enter__` time**, not continuously during the session. SPEC-V3's
in-use check exists to stop a reset from clobbering a directory a live
Chrome process still holds; once this session's own Chrome is running and
holding the lock itself, there's nothing further to guard against until the
next video's `__enter__`.
→ `src/scraper/chrome/session.py`, `ChromeSession.__enter__`.

**Signal handling converts SIGINT/SIGTERM into a raised `SystemExit`
inside `ChromeSession.__enter__`'s caller frame, rather than a
flag-and-poll loop.** Raising unwinds whatever `with ChromeSession(...):`
block is active exactly like any other exception would, so `__exit__` runs
the same teardown (kill Chrome, delete `data-dir`) regardless of whether the
block ended normally, via an error, or via a signal — satisfying SPEC-V3's
"interruption follows the same shutdown path as a normal exit" without a
separate code path to keep in sync.
→ `src/scraper/chrome/session.py`, `ChromeSession._install_signal_handlers`.

**Phase 5's real-Chrome exit criterion (fresh profile launches, handshake
deadline fires, `data-dir` is removed) was verified as a manual smoke test
against `gates/data-dir-template`, not an automated pytest test.** PLAN.md
itself scopes the automated Phase 5 tests to the reset sequence against a
fake template with no Chrome at all; launching real Chrome per test run
would be slow and machine-dependent. The manual run confirmed: Chrome
launched against a fresh copy, the extension (not yet built) never
connected, `HandshakeTimeout` fired at the configured deadline, and Chrome
exited cleanly (code 0) with `data-dir` removed.
→ Manual run, not committed to the repo.

## Phase 6 — Extension

**The recommendation cap is enforced by the controller, not the extension,
via a mid-video `stop`.** Confirmed with the user before implementing (two
options were presented: the collector self-counting sidebar entries in JS,
or the controller — which alone knows the true post-filter, post-dedup
stored count — deciding). `VideoSession.handle_payload` now returns
`[payload_ack, stop]` once the accumulated stored count reaches
`config["maxRecommendations"]`, ahead of any `video_done` from the
extension. `stop` therefore has two meanings depending on when it arrives:
mid-video (before the collector's own completion report) it means "the cap
was hit, report `video_done(reason=max_recommendations_reached)`"; after the
collector's own completion report, it's the ordinary end-of-session signal.
This required extending `dispatch`'s return contract from `dict | None` to
`dict | list[dict] | None` and teaching `connection.py` to write each
message in the list as its own frame.
→ `src/scraper/controller/video_session.py`, `handle_payload`.
→ `extension/src/collector.ts`, `Collector.handleStop`.

**Chain exhaustion (the other completion reason) *is* decided by the
collector**, unlike the cap — SPEC-V3 states this explicitly ("Collector
script... decides when a video is complete") and it needs no full parse,
only checking whether a payload's sidebar entry list ends in a
`continuationItemRenderer`. This is the same JSON path
`src/scraper/parser/recommendations.py`'s `_iter_sidebar_entries` uses,
ported to a much smaller existence check rather than a duplicated parser.
→ `extension/src/collector.ts`, `sidebarEntries` / `hasFurtherContinuation`.

**Detection scope is honestly incomplete for four of the twelve error
codes.** `PAGE_READY_TIMEOUT`, `UNEXPECTED_NAVIGATION`, and
`SHORTS_EXCLUDED` are wired up (all mechanical: a deadline, a host/path
check). `CONSENT_WALL`, `AGE_RESTRICTED`, `LOGIN_REQUIRED`, and
`VIDEO_UNAVAILABLE` are not — triggering them correctly would need a
verified `playabilityStatus` value from a real capture of each state, and
none exist in `gates/captures/` (only an ordinary video, a finished live
stream, and a premiere). PLAN.md's ground rules forbid guessing YouTube's
payload shape. If one of these states is hit in practice it still fails
loudly — as `PAGE_READY_TIMEOUT` (no initial payload ever arrives) or
`SCHEMA_UNRECOGNISED` (a payload arrives but doesn't match) — just not
under its more specific code. Revisit if a capture of one of these states
is ever obtained.
→ `extension/src/service-worker.ts`, module docstring.

**The collector <-> service-worker channel is a long-lived
`chrome.runtime.Port`, not per-message `sendMessage` calls.** Not part of
SPEC-V3's wire protocol (that's the controller <-> extension side, fixed by
the spec's message table) — this is purely internal, so it's a free design
choice. A Port was chosen over one-off `sendMessage` because the service
worker must be able to push to the collector unprompted (a cap-triggered
`stop`, a `pause` on native disconnect), not only reply to collector-
initiated requests.
→ `extension/src/internal-protocol.ts`, module docstring.

**`hello_ack.config`'s shape (`CollectionConfig`) is a free design choice
formalised now, not something SPEC-V3 pins down.** SPEC-V3 only names the
category ("collection configuration") and separately requires several
circuit breakers to be configurable without giving field names. Fixed as
`{maxRecommendations, scrollDelayMs, delayJitter, pageReadyTimeoutMs,
ackTimeoutMs, maxScrollRounds, maxPageDurationMs}`, mirrored field-for-field
between `config.py` (provisional values, same pattern as the Phase 3 input
caps) and `protocol.ts`. Phase 7 only has to assemble this dict when
constructing `VideoSession`; the shape itself doesn't need revisiting.
→ `src/scraper/config.py`, comment above `PAGE_READY_TIMEOUT_MS`.
→ `extension/src/protocol.ts`, `CollectionConfig`.

**Native-port reconnect backoff uses `setTimeout`, not `chrome.alarms`.**
SPEC-V3 ties `chrome.alarms` specifically to the heartbeat's scheduling
floor, not to reconnection generally, and a `setTimeout`-based exponential
backoff (250ms-10s) fits a reconnect attempt that resolves within seconds
of an active disconnect event — which itself keeps the service worker
alive — far better than alarms' coarse minimum granularity would.
→ `extension/src/native-port.ts`, module docstring.

**Extension build output lands directly in `extension/`, not
`extension/dist/`.** SPEC-V3 states the extension is loaded unpacked from
`.../scraper/extension` itself ("Extension identity"), not a subdirectory.
Phase 0's `.gitignore` had anticipated a `dist/` subdirectory before this
was pinned down; fixed to ignore the three built bundles directly under
`extension/` instead.
→ `.gitignore`, "Extension build output" section.

**The extension's signing key lives at `keys/extension.pem` (project
root), gitignored, generated fresh — never the throwaway key at
`gates/keys/probe.pem`.** The extension ID is deterministically derived
from it (SHA-256 of the DER public key, first 16 bytes, each nibble mapped
to a-p — verified against the probe's own known ID before trusting the
algorithm for the real key). Losing this file means generating a new
keypair and a new ID, which breaks the pinned `allowed_origins` in every
copy of the native-messaging manifest until `config.EXTENSION_ID` and
`extension/manifest.json`'s `key` field are both re-pinned together. It
must be backed up outside the repo.
→ `.gitignore`, "Extension signing key" section.
→ `src/scraper/config.py`, comment above `EXTENSION_ID`.

**Confirmed empirically, not assumed: retail Google Chrome ignores
`--load-extension` on the command line even with Developer Mode on and no
enterprise policies present** (verified via `chrome://policy`,
`chrome://version`, and CDP inspection of `chrome://extensions` — spent
real effort trying to script around this before concluding it's
intentional anti-malware hardening, not a local misconfiguration). This is
exactly why SPEC-V3's "One-time template creation" has the user load the
extension through the `chrome://extensions` UI by hand rather than the
controller automating it — there is no scriptable alternative on Stable-
channel Chrome. The user did this once, manually, against a copy of
`data-dir-template`; Phase 6's exit criterion (extension loads with the
expected ID, connects through the bridge, completes a handshake) was then
verified against that real template using a real `ChromeSession` and a real
`ControllerServer`, not a fake one.
→ Manual run, not committed to the repo.
