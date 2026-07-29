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

## Phase 7 — Integration

Phase 7's own scope (wiring `python -m scraper controller` to the pieces every
earlier phase built) was small. Most of this phase turned out to be real bugs
in the extension that only manifest against live Chrome and live YouTube
traffic — timing, service-worker restarts, real redirects — none of which the
unit test suite (which mocks the extension boundary entirely) could have
caught. Each is real, not speculative: found via an actual run, fixed, then
re-verified live.

**Real bugs found and fixed, in the order they surfaced:**

1. **`onCommitted`'s navigation guard was a one-shot token.** It failed the
   video the instant YouTube's own same-video redirect (`?v=X` →
   `?v=X&themeRefresh=1`) committed a second time, since the token was
   consumed by the first commit. Fixed to compare the committed URL's `v`
   query param against the assigned video ID instead of requiring an exact
   URL match — tolerates YouTube's own redirects on the same video, still
   catches a real navigate-away.
   → `extension/src/service-worker.ts`, `chrome.webNavigation.onCommitted`.

2. **`ensureWorkerTab()` always created a new blank tab** instead of reusing
   the tab Chrome already has open at launch, leaving two tabs open where
   SPEC-V3 says one. Fixed to adopt the existing tab via `chrome.tabs.query`.
   → `extension/src/service-worker.ts`, `ensureWorkerTab`.

3. **The service worker re-navigated the tab on every `hello_ack`**,
   including the ones following its own ~30s MV3 idle-timeout restarts —
   restarting page load in a loop that, in the worst observed case, never
   let a real watch page finish loading at all. Fixed to only navigate when
   the assigned video ID differs from what's already assigned; a restart's
   `hello_ack` re-announces state without re-navigating, matching SPEC-V3's
   "re-announces its state... before doing anything else."
   → `extension/src/service-worker.ts`, `handleControllerMessage`'s
     `hello_ack` case.

4. **A real race in `chrome.runtime.onConnect`**: the handler validated the
   sender tab with `await loadState()` *before* registering
   `port.onMessage.addListener`. The collector's `assign_request`, sent
   synchronously right after connecting, could arrive before the listener
   existed and was silently dropped — Chrome doesn't queue Port messages for
   a not-yet-registered listener. Observed live as "collector connected"
   with no assignment ever following. Fixed by registering the listener
   synchronously in the same tick as `onConnect` firing, buffering messages
   until validation completes.
   → `extension/src/service-worker.ts`, `chrome.runtime.onConnect`.

5. **Scroll-stall detection treated any captured payload as progress**,
   including duplicate `ytInitialPlayerResponse` re-captures unrelated to
   pagination (YouTube can refire that property independently of
   scrolling). The stall counter kept resetting and the loop scrolled
   continuously up to the `maxScrollRounds` ceiling — observed live as
   runaway scrolling the user had to force-kill Chrome to stop. Fixed to
   only count payloads recognised as sidebar-shaped
   (`hasFurtherContinuation() !== null`) toward progress.
   → `extension/src/collector.ts`, `recognisedPayloadCount`.

6. **The consent-redirect host check was too strict.** The golden template's
   cookie-consent extension ("I still don't care about cookies" — see
   `gates/README.md`, Gate A) can route through a genuine top-level redirect
   to `consent.google.com` (never `*.youtube.com`) before landing back on
   the video — the normal case here, since `data-dir` starts cookie-less
   every video. `isAllowedYouTubeUrl`'s host check would have failed the
   video immediately on that redirect, and even a same-host YouTube
   interstitial with no `v=` param would have tripped it too. Both are now
   tolerated as transitional; a real host mismatch or a different video's
   `v=` param still fails the run.
   → `extension/src/service-worker.ts`, `isTransitionalConsentUrl`,
     `chrome.webNavigation.onCommitted`.

7. **`PAGE_READY_TIMEOUT` was gated on the wrong signal.** `sawInitialPayload`
   was satisfied by capturing *either* `ytInitialPlayerResponse` or
   `ytInitialData` — since the player response reliably arrives first and
   fast regardless of whether the sidebar ever loads, a missing
   `ytInitialData` sailed past the 15s deadline undetected and the collector
   scrolled blind. Split into a signal specific to `ytInitialData`, so a
   genuinely missing sidebar now fails fast and clearly instead of silently
   scrolling for a long time with nothing to show for it.
   → `extension/src/collector.ts`, `sawInitialData`.

8. **A double `start()` invocation raced two native connections against each
   other.** `start()` was reachable from three places for the same module
   evaluation — an unconditional top-level call (needed because MV3 idle-
   timeout restarts fire neither Chrome event) plus `onInstalled` and
   `onStartup`, and the latter fires for real on every genuine Chrome
   launch, i.e. every video. `NativePort.connect()` had no reentrancy guard
   of its own: a second call silently overwrote the port reference without
   disconnecting the first, leaving two live bridge connections and two
   `hello_ack`-triggered `navigateToVideo()` calls racing each other. This
   is the actual explanation for the erratic extra-reload behaviour
   observed live — confirmed by re-running the same three videos
   back-to-back afterward with zero repeated reloads, in about 9 seconds
   total. Fixed with a `started` guard.
   → `extension/src/service-worker.ts`, `start`.

9. **Scrolling had no relationship to acknowledgement.** `driveScrolling`
   scrolled on a flat timer regardless of whether the last captured batch
   had actually been durably accepted by the controller — flagged directly
   by the user ("there's no need to scroll before the data gets accepted").
   Fixed to wait until nothing is queued or in-flight before the next
   scroll, bounded by `ackTimeoutMs` (SPEC-V3's "acknowledgement timeout"
   circuit breaker, previously wired into the config but never actually
   enforced anywhere).
   → `extension/src/collector.ts`, `waitForPendingWorkToSettle`.

10. **No video plays" wasn't actually true.** `--autoplay-policy=document-
    user-activation-required` blocks *unmuted* autoplay, but Chromium
    exempts muted media from that policy regardless of the flag's value —
    and `--mute-audio` is also set, so the video could still visually
    autoplay, just silently. Observed directly by the user watching a real
    run. Enforced defensively in the collector instead of relying on the
    launch flag: pause any `<video>` the instant it exists (a
    `MutationObserver`) and again the instant anything tries to play it (a
    capturing `play` listener on `document`, since the event doesn't
    bubble). Verified live via CDP: `video.paused === true`,
    `currentTime === 0`, sampled every 1.5s across a 9-second window.
    → `extension/src/collector.ts`, `suppressAutoplay`.

**Extension logging didn't exist.** SPEC-V3's Logging section requires
"Extension logs carry the run ID and the current video ID," but there wasn't
a single `console.log` anywhere in `extension/src/` — bugs 3 and 4 above were
very hard to diagnose until logging was added first.
→ `extension/src/service-worker.ts`, `log`; `collector.ts`'s own
  `console.log` calls at assignment/done/failed.

**`--wait-for-comments`, a new CLI flag, added at the user's suggestion.**
Nothing about the collector's stopping conditions (recommendation cap,
chain exhaustion) is comment-aware — a low `--max-recommendations` can end
collection before the page ever gets a chance to load comments, which is
exactly what happened in testing and produced a `SCHEMA_UNRECOGNISED`
failure (correctly: `resolve_comment_state` treats an unresolved header
count with zero threads as genuinely ambiguous, per its own SPEC-derived
docstring — this was never a bug in that function). The premise the user
raised — that YouTube's own comments header carries a real count, just not
always in the first payload — is correct (`comments.py`'s own docstring
already said as much). Off by default (`DEFAULT_WAIT_FOR_COMMENTS = False`):
turning it on trades a stricter completion guarantee for scrolling further
than the recommendation side alone would have stopped at. When on, neither
chain-exhaustion nor the recommendation-cap `stop` completes the video until
the comments header resolves to a real count (zero, or at least one thread
actually collected) — still bounded by `maxScrollRounds`/
`maxPageDurationMs` like everything else. Verified live: a real run with
`--wait-for-comments` produced `status=completed`,
`comment_state=collected`, `reported_comment_count=2445858`, 20 comments
actually stored.
→ `extension/src/collector.ts`, `commentsResolved` / `pendingCompletionReason`.
→ `extension/src/protocol.ts`, `CollectionConfig.waitForComments`.
→ `src/scraper/__main__.py`, `--wait-for-comments`.

**`MAX_SCROLL_ROUNDS` default dropped 200 → 20, plus a new
`--max-scroll-rounds` flag.** The old default let a video stuck on repeated
reloads (bug 8, before it was found) scroll for a very long time before its
own circuit breaker gave up — a human had to kill Chrome by hand. SPEC-V3
already requires "maximum scroll rounds" to be configurable (Completion
condition); this makes it actually so, conservative by default, with going
faster/higher an explicit opt-in rather than what everyone gets.
→ `src/scraper/config.py`, `MAX_SCROLL_ROUNDS`.
→ `src/scraper/__main__.py`, `--max-scroll-rounds`.

**The controller-side per-video backstop deadline dropped from ~30 minutes
to 5.** It was originally sized as a multiple of the extension's own
`MAX_PAGE_DURATION_MS` breaker, on the theory that it only needs to catch
that breaker failing to fire. That theory was wrong: a live run hit a
different failure mode entirely — the bridge kept reconnecting every ~30s
forever, each connection going silent with no `hello`, `video_done`, or
`video_failed` ever following the first one. `MAX_PAGE_DURATION_MS` never
applies to that case since the extension-side collection loop never gets
going, so tying the backstop to it left the controller waiting up to 30
minutes on a video that was clearly stuck within under a minute.
→ `src/scraper/controller/runner.py`, `_VIDEO_DEADLINE_S`.

**Multi-video profile reset verified empirically, not just by reading the
code**, at the user's request. Polled Chrome's PID and `data-dir`'s inode
every 200ms across a real two-video run: video 1 ran as PID 57720 against
inode 38616217, then a gap where neither Chrome nor `data-dir` existed at
all, then video 2 ran as PID 57962 against inode 38618758 — a different
process against a genuinely different directory (macOS assigns a new inode
when a directory is deleted and recreated), not the same Chrome instance
navigating within one profile. Confirmed via code reading too: `copy_profile`
only ever reads from `template` and writes to `data_dir`; the golden
template is never mutated by any reset.
→ `src/scraper/chrome/reset.py`, `copy_profile`.

**Acceptance criteria walk.** All 21 from SPEC-V3, each mapped to either an
automated test or a live-run observation made during this phase:

| # | Criterion | Evidence |
|---|---|---|
| 1 | No `--video-ids` → error, exit non-zero, no profile touched | `test_runner.py::test_empty_video_ids_errors_without_touching_anything` |
| 2 | In-use `data-dir`/`data-dir-template` blocks; unrelated Chrome doesn't | `test_singleton_lock.py` (7 tests covering live/dead PID, in-use detection) |
| 3 | Reset replaces `data-dir`, strips `Singleton*`, writes native-host manifest | `test_reset.py::test_reset_profile_full_sequence` |
| 4 | Chrome from the exact configured path; runtime profile has both extensions | `test_process.py::test_launch_builds_expected_argv`; both extensions' presence confirmed via `Secure Preferences` inspection of the real template |
| 5 | Handshake within deadline; timeout aborts the run with a clear error | `test_session.py::test_handshake_timeout_raises_and_still_tears_down`; `test_runner.py::test_handshake_timeout_aborts_run`; live-verified (real handshake completing in ~1-2s) |
| 6 | One tab, top-level nav to each watch page | Bug 2 above, fixed and live-verified (single tab adopted, no second tab) |
| 7 | Initial payload captured even when assigned before the collector is ready | `capture.ts`'s buffer/flush design (buffers until `collector-ready`); live-verified (`ytInitialPlayerResponse`/`ytInitialData` always arrived) |
| 8 | Wrapping fetch/XHR doesn't alter page behaviour | Code inspection: `capture.ts` always calls the original and only reads from a `.clone()`, never consumes the real response/body; live runs showed normal page rendering and pagination |
| 9 | No outbound request to YouTube's internal API from the extension | Code inspection: `capture.ts` only wraps/observes `fetch`/XHR, never calls them itself for `/youtubei/*` |
| 10 | No video plays; a short video doesn't navigate the worker tab away | Bug 10 above, fixed and live-verified via CDP (`video.paused === true` throughout) |
| 11 | Stops at cap or chain exhaustion; recorded as such, not a failure | `test_happy_path.py`, `test_cap_stop.py`; live-verified (`completed`, `max_recommendations_reached`) |
| 12 | Only video entries stored, with both raw and normalised sidebar position | `test_recommendations.py::test_plain_video_accumulator_dedupes_to_80` (checks `normalised_position` sequence); `recommendations.py`'s `RecommendationAccumulator` |
| 13 | SQLite writes idempotent under batch replay | `test_batches.py::test_replaying_same_batch_id_is_a_noop`, `test_replaying_recommendation_across_different_batches_does_not_duplicate` |
| 14 | Completion acknowledged before the run advances | By construction: `handle_video_done` commits then acks in one handler call; the runner only advances once `is_done`, which is set after commit. `test_happy_path.py` |
| 15 | `data-dir` removed after each video/clean shutdown; template unchanged | Empirical PID/inode verification above; `copy_profile` never writes to `template` |
| 16 | Killing the controller mid-batch loses no acked data, no stuck running state | No `running` status exists in the schema (only `completed`/`failed`) — nothing can be "stuck." `record_batch` commits atomically before acking. Directly observed across several forced kills this session: no orphaned or inconsistent rows |
| 17 | Killing the service worker mid-video is recovered from, not reported as completion | `test_generations.py`; live-observed repeated SW restarts (`hello` with `restart`) mid-run with no premature completion |
| 18 | No recognised recommendation payload → structured failure, not completion | `driveScrolling`'s post-loop check (`SCHEMA_UNRECOGNISED` when `!recognisedAnything`); same mechanism observed live for the analogous comments case |
| 19 | Failed video: reason recorded, no orphaned rows, retried not skipped | `test_video_status.py::test_failed_video_leaves_reason_raw_files_and_zero_child_rows`, `test_failed_video_is_not_skipped_and_is_retried`; live-verified across repeated runs |
| 20 | Re-running a completed ID skips it, prints which | `test_runner.py::test_rerun_skips_completed_video`; live-verified (`skipping already-completed video IDs: ...` actually printed in a real run) |
| 21 | Payload interpretation tested against saved fixtures | `tests/parser/*.py`, all parametrised over `tests/captures/*.json` |

## Operational notes

Not design decisions, but gotchas discovered the hard way this session that will bite
again if forgotten.

**`data-dir-template`'s extension can run stale code after a rebuild.** Chrome caches an
already-registered unpacked extension's service-worker code per profile. Rebuilding
`extension/` (`npm run build`) updates the files on disk, but a Chrome instance that has
previously loaded the extension from `data-dir-template` — or a fresh `data-dir` copied
from it — can keep running the old service-worker code until the extension is explicitly
reloaded. This showed up as edits appearing to have no effect, or old bugs seeming to
reappear, purely because the running code was stale, not because the fix was wrong.

Force a reload after every extension rebuild, before trusting a live run against the
template: open `chrome://extensions` in a window running against `data-dir-template` (or
the freshly copied `data-dir`) and click the reload icon on the extension, or trigger
`chrome.developerPrivate.reload(extensionId, {...})` over CDP. Do this on the template
itself if you want the fix to be picked up by every subsequent per-video copy —
reloading only inside a throwaway `data-dir` copy doesn't change the template.
