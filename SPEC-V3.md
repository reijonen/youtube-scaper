# YouTube Scraper — Specification v3

This document contains only decisions that have been made and verified. Sections are
added as decisions are settled. Anything absent is still undecided.

v3 supersedes v2. The change from v2 is the extraction method: data is captured from
YouTube's own JSON payloads rather than from the DOM.

## Purpose

Collect, for a given list of YouTube video IDs, the watch page's recommended videos and
its comments, with a clean client-side baseline per video.

## v1 scope

Invocation:

```
python -m scraper controller --video-ids ID1,ID2,ID3
```

`--video-ids` is required. If it is missing or empty, the controller prints an error and
exits non-zero without touching any profile directory.

Options, all with sensible defaults:

| Option | Default | Meaning |
|---|---|---|
| `--video-ids` | *required* | Comma-separated YouTube video IDs |
| `--max-recommendations` | `100` | Stored recommendations per video, after filtering to videos. Reaching it ends collection normally |
| `--inter-video-delay-ms` | `5000` | Idle pause between videos, on top of Chrome teardown and profile reset |
| `--scroll-delay-ms` | `1000` | Dwell between scroll actions within a page, on top of waiting for the payload |
| `--delay-jitter` | `0.25` | Fraction of each delay applied as uniform random jitter, both directions |

Before the first launch, the controller checks which of the requested video IDs already
completed successfully in the database, skips those, and prints the skipped IDs. Videos
that previously failed are not skipped; they are retried.

Video IDs are processed in the order given. If every requested ID is already present, the
controller reports that and exits without launching Chrome.

For each remaining video ID, in sequence:

1. Reset the disposable Chrome profile from the golden template.
2. Launch Chrome.
3. Navigate the worker tab to that video's watch page.
4. Capture the initial page payload.
5. Send captured payloads to the controller and wait for durable acknowledgement.
6. Scroll to trigger YouTube to load more content, and capture what it loads.
7. Repeat until the recommendation continuation chain is exhausted.
8. Report the video complete.
9. Shut Chrome down and delete the disposable profile.

Recommendations and comments are associated with the source video ID they were extracted
from.

## Components

1. A long-running Python controller.
2. A small Python Chrome native-messaging bridge, launched by Chrome.
3. A Manifest V3 Chrome extension written in TypeScript.
4. A disposable Chrome user-data directory, recreated from a golden template.
5. SQLite storage accessed exclusively by the controller process.

## Non-goals

Do not implement or depend on:

- Chrome DevTools Protocol, WebDriver, Selenium, Puppeteer, Playwright, ChromeDriver.
- Chrome address-bar automation.
- macOS mouse or keyboard automation.
- DOM scraping as a data source.
- Outbound requests to YouTube's internal API constructed by the extension.
- The official YouTube Data API.
- Shorts.
- Expanding comment reply threads.
- Multiple concurrent Chrome tabs.
- Multiple SQLite-writing processes.
- URL or video discovery inside the extension.
- Persistence of website cookies between videos.
- Chrome or Google account login, or Chrome Sync.

---

# Extraction

## Method

All collected data comes from YouTube's own JSON, obtained two ways:

1. **The initial page payload.** The watch page embeds `ytInitialData`, which contains
   the first page of sidebar recommendations and the source video's own metadata.
2. **Every subsequent payload the page fetches.** As the page is scrolled, YouTube loads
   further recommendations and all comments by issuing requests to its internal endpoint
   `POST /youtubei/v1/next`. The extension observes the responses to those requests.

The extension never constructs a request to YouTube's internal API. It only observes
requests the page makes on its own behalf, so every request carries the page's genuine
headers, cookies, and attestation state. This keeps the collector immune to changes in
YouTube's request-signing or proof-of-origin requirements, which apply to request
construction and not to observation.

The DOM is never a data source. It remains a *signal* source only: the extension reads
page geometry to decide when to scroll.

## What is collected

**Recommendations.** Only video entries from the watch page's recommendation sidebar.
Non-video entries — Mixes, radio entries, playlists, shelves, promoted slots — are
discarded rather than stored with a type marker.

Because discarding them leaves gaps, every stored recommendation carries two positions:

- its **raw position**, its index in the sidebar as YouTube delivered it, including the
  discarded entries
- its **normalised position**, its index among the kept video entries only, contiguous
  from 1

Both are recorded so that "this video was third in the sidebar" and "this video was the
third *video* in the sidebar" remain distinguishable after the fact.

**Shorts.** Supplied video IDs are guaranteed by the upstream pipeline to exclude Shorts,
so the collector does not filter them. It does verify: a watch-page navigation that
resolves to a `/shorts/` path is recorded as `SHORTS_EXCLUDED` and the video is skipped
rather than collected. This is a safety net against a bad input, not a filter the design
depends on.

**Comments.** Top-level comments only. Reply threads are never expanded and are not
collected. The comment sort order is left at YouTube's default, "Top comments"; the
extension does not change it. That ordering is ranked and is not guaranteed stable
between observations, which is accepted.

## Measured payload structure

Verified against three captures taken on cookie-less runtime profiles: an ordinary video,
a finished live stream, and a premiere. Stored in `gates/captures/`.

### Recommendations

Sidebar items live at:

```
contents
  .twoColumnWatchNextResults
  .secondaryResults
  .secondaryResults
  .results[0]
  .itemSectionRenderer
  .contents[]
```

Each entry is a `lockupViewModel`, with a trailing `continuationItemRenderer` carrying
the token for the next page. Continuation pages deliver the same `lockupViewModel`
entries.

YouTube has migrated this surface to its view-model architecture. There is no
`compactVideoRenderer`; anything written against that shape is obsolete.

Per item:

| Field | Location |
|---|---|
| video ID | `contentId` |
| item type | `contentType`, e.g. `LOCKUP_CONTENT_TYPE_VIDEO` |
| title | `metadata.lockupMetadataViewModel.title.content` |
| channel name | first metadata row, first text part |
| view count, published time | second metadata row's text parts, rendered strings |
| duration | thumbnail bottom-overlay badge `text`, with a spelled-out accessibility label |
| thumbnails | `contentImage.thumbnailViewModel.image.sources[]`, several resolutions |
| animated preview | `animatedThumbnailOverlayViewModel` |
| channel ID | avatar's `browseEndpoint.browseId` |
| channel handle | avatar's `browseEndpoint.canonicalBaseUrl` |
| channel avatar | avatar `image.sources[]` |

Metadata rows are not positionally stable — most items carry two rows, some carry three.
The parser reads rows by content, never by fixed index.

### Comments

Comments are split across two structures in the same response and must be joined.

- The item tree carries `commentThreadRenderer` shells containing `commentViewModel`,
  which holds only keys — `commentId`, `commentKey`, `toolbarStateKey` — and no content.
- The actual content lives in the response's entity store as `commentEntityPayload`,
  joined on the comment key.

`commentEntityPayload` provides the comment ID, full text, published time, reply level,
and an `author` block with channel ID, display name, avatar URL, and `isVerified`,
`isCreator`, `isArtist` flags. Its `toolbar` block provides like count, reply count, and
a heart tooltip identifying creator hearts.

Comment text arrives complete. Nothing needs expanding to obtain it.

`commentsHeaderRenderer.countText` sometimes carries the total comment count YouTube
reports for the video, and sometimes does not. Both were observed: one capture gave
`["571", " Comments"]`, another gave only `["Comments"]` with no number despite the video
having comments. The count is therefore stored as nullable and is never load-bearing —
when present it lets a collected count be read against the true total, and when absent
the collected count stands alone.

### Video kind

Read from the snapshotted `ytInitialPlayerResponse`. All four states were observed
directly except where noted:

| Kind | Signal |
|---|---|
| `live` | `videoDetails.isLive` is true; `liveBroadcastDetails.isLiveNow` is true; `lengthSeconds` is `"0"` |
| `upcoming` | `videoDetails.isUpcoming` is true; `playabilityStatus.status` is `LIVE_STREAM_OFFLINE`; `liveBroadcastDetails.startTimestamp` is in the future |
| `past_live` | `videoDetails.isLiveContent` is true, `isLive` unset, and `liveBroadcastDetails` carries an `endTimestamp` |
| `video` | none of the above |

### Comment availability

Stored on the video record as an explicit state, never inferred from a count of zero:

Determined from three independent signals — whether a comment header was present, how many
threads were actually collected, and the video kind. The reported count is not one of
them, because it is not always present.

| State | Determination |
|---|---|
| `collected` | One or more comment threads were collected |
| `none_present` | Header present, zero threads collected, and the header reported a count of exactly zero |
| `live_chat_instead` | No comment header, and the video kind is `live` or `upcoming` |
| `disabled` | No comment header, and the video kind is not `live` or `upcoming` |

Anything else is **not** a state. A header present with zero threads collected and a count
that is either non-zero or absent is a collection failure, and is recorded as one. Zero
comments is only ever a legitimate result when something in the payload positively says
so.

Of these, `collected`, `none_present`, and `live_chat_instead` were each observed in the
gate captures. `disabled` is derived by elimination and has not yet been seen; a video
with comments turned off would confirm it.

### Observed sizes

The largest single `/youtubei/v1/next` response across all three captures was 275 KiB,
and a whole video session totalled 2–3.4 MB across all endpoints. This sits far inside
the message and storage budgets.

## Capture mechanism

`chrome.webRequest` and `declarativeNetRequest` cannot read response bodies and are not
usable for this. Capture is done by a dedicated content script running in the page's own
JavaScript world.

The extension therefore has two content scripts on watch pages:

- A **capture script**, declared with `"world": "MAIN"` and `"run_at": "document_start"`.
- A **collector script**, running in the default isolated world.

### Capture script

Runs in the page's world so that it can see and wrap the page's own globals. It must be
installed before any YouTube script executes; `document_start` in the main world is a
hard requirement, not a preference.

It does three things and nothing else:

1. **Captures the initial payload deterministically.** It installs an accessor on
   `window.ytInitialData` before the inline script that assigns it runs, so the value is
   captured at the moment of assignment. It does not poll for the global to appear.

   It must take a **deep snapshot at the moment of assignment**, not hold a reference.
   YouTube mutates `ytInitialData` in place as the page paginates: continuation results
   are appended into the same object. A held reference read later therefore returns the
   accumulated end state, not the initial payload, and silently double-counts everything
   that also arrived over the wire. This was observed directly in the gate captures.
2. **Wraps `fetch` and `XMLHttpRequest`** so that responses to `POST /youtubei/v1/next`
   are read out and forwarded. Response bodies are always read from a clone; the original
   body stream is never consumed, so page behaviour is unchanged. Requests to other paths
   are passed through untouched and unread.

   Only `/youtubei/v1/next` is captured. The page also calls `/youtubei/v1/player`,
   `/youtubei/v1/log_event`, `/youtubei/v1/feedback`, and `/youtubei/v1/player/heartbeat`;
   none carry recommendations or comments and all are ignored. Both transports must be
   wrapped: `next` was observed on `fetch`, but `log_event` and `heartbeat` arrive over
   `XMLHttpRequest`, so the transport split is not stable enough to rely on.
3. **Forwards captured payloads to the collector script** over `window.postMessage`.

It holds no state beyond a buffer, makes no network requests, defines no global
identifiers, and runs entirely inside a closure so it does not collide with page code.

Because payloads can arrive before the collector script has finished its handshake with
the service worker, the capture script buffers captured payloads and flushes them once
the collector signals readiness. Nothing captured before the collector is listening may
be dropped.

### Collector script

Runs in the isolated world, where extension APIs are available. It:

- Performs the ready handshake with the service worker and receives its assignment.
- Receives captured payloads from the capture script, accepting only messages whose
  `event.source` is the current window.
- Forwards payloads to the service worker in bounded batches, one unacknowledged batch in
  flight at a time.
- Drives scrolling and decides when a video is complete.

The `postMessage` channel is inside the page and is therefore not trustworthy against the
page itself. No integrity is claimed for it. The defence against malformed or hostile
input is the controller's size caps and schema validation, applied to everything the
extension sends, exactly as for any other untrusted input.

## Completion condition

Because collection is driven by YouTube's own continuation chain, completion is directly
observable rather than inferred from page stability.

A video is complete when either:

- `--max-recommendations` stored recommendations have been collected, or
- the recommendation continuation chain is exhausted — the most recent recommendation
  payload contained no further continuation token.

In practice the cap is the operative rule. Across all three gate captures the chain was
still offering a continuation token at 52, 80, and 48 items respectively, so exhaustion
is not a condition that can be relied on to arrive.

Both are **normal completion**, not failures, and the video record stores which of the
two ended collection. That distinction is the signal for whether a recommendation set was
truncated by our cap or genuinely ran out.

Circuit breakers are different: tripping one is a failure, not completion.

The comment chain is deeper than the recommendation chain, so collection stopping on
recommendation exhaustion means the comment set for a video is typically partial. This is
intended: recommendations are the primary target and comments are collected
opportunistically alongside them. Comment sets are therefore recorded as bounded by the
recommendation chain, not as complete.

Scrolling continues while a continuation token remains outstanding. If a scroll produces
no new payload within a bounded timeout, the extension retries scrolling a bounded number
of times before declaring the chain stalled and reporting a structured failure.

All circuit breakers are configurable: maximum page duration, maximum scroll rounds,
maximum payloads per video, page-ready timeout, and acknowledgement timeout.

## Detecting silent extraction failure

Normal completion must never be indistinguishable from a broken collector.

- If no `/youtubei/v1/next` payload and no initial payload are captured within the
  page-ready deadline, the extension reports a structured failure with a distinct error
  code. It does not report completion.
- If payloads are captured but none contains a recognised recommendation structure, that
  is a structured failure with its own error code — this is the signal that YouTube's
  JSON schema has moved.
- The controller tracks yield per video across a run and treats a collapse in yield as a
  run-level failure rather than continuing to produce empty results.
- A redirect to an unexpected host, a consent wall, a CAPTCHA, a login prompt, or an
  unavailable-video page produces a structured error rather than a hang.

## Known data ceiling

Sidebar recommendation entries carry rendered view counts and rendered publish dates
(`"1.2M views"`, `"3 years ago"`) and not exact values. Exact figures exist only for the
source video. This is a property of what YouTube sends, not of the capture method, and no
capture technique recovers it.

---

# Runtime and process architecture

## Fixed paths

```
Chrome executable:
/Users/sor/Applications/Google Chrome.app/Contents/MacOS/Google Chrome

Disposable runtime Chrome user-data directory:
/Users/sor/Dev/youtube/research/pipelines/extraction/tools/scraper/data-dir

Golden Chrome profile template:
/Users/sor/Dev/youtube/research/pipelines/extraction/tools/scraper/data-dir-template

Extension source directory:
/Users/sor/Dev/youtube/research/pipelines/extraction/tools/scraper/extension

Unix socket:
/Users/sor/Dev/youtube/research/pipelines/extraction/tools/scraper/run/controller.sock

SQLite database:
/Users/sor/Dev/youtube/research/pipelines/extraction/tools/scraper/data/db.sqlite3

Native-host executable wrapper:
/Users/sor/Dev/youtube/research/pipelines/extraction/tools/scraper/bin/scraper-native-host

Native-host manifest name:
com.sor.yts
```

Chrome is always launched with:

```
--user-data-dir=<runtime dir>
--profile-directory=Default
--no-first-run
--no-default-browser-check
--autoplay-policy=document-user-activation-required
--mute-audio
```

There is no separate `--profile` flag.

Autoplay is suppressed at the browser level and nowhere else. The extension does not
touch the player. This matters for correctness, not just bandwidth: if a video plays to
its end, YouTube's autoplay-next navigates the worker tab away mid-collection, and the
autoplay-next preference lives in profile state that is destroyed before every video, so
it is always at its default. Blocking playback at the browser prevents the video from
ever ending, which prevents the navigation.

As a general guard, and independent of autoplay, the service worker treats any top-level
navigation of the worker tab that it did not initiate as a terminal failure for the
current video.

The SQLite database, Unix socket, logs, and any Python state live outside both Chrome
user-data directories.

## Native-messaging host manifest location

Chrome resolves user-level native-messaging host manifests relative to the **user-data
directory**, not to the default Chrome application-support path. With a custom
`--user-data-dir`, the manifest must be at:

```
<user-data-dir>/NativeMessagingHosts/com.sor.yts.json
```

This is at the user-data-dir root, not inside `Default/`.

Because the runtime directory is destroyed and recreated for every video, the controller
writes this manifest programmatically immediately after each template copy, filling in
the absolute executable path and the extension origin from configuration. This keeps the
manifest, the wrapper path, and the extension ID from drifting apart.

Manifest shape:

```json
{
  "name": "com.sor.yts",
  "description": "Native bridge for YouTube Scraper",
  "path": "/Users/sor/Dev/youtube/research/pipelines/extraction/tools/scraper/bin/scraper-native-host",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://<FIXED_EXTENSION_ID>/"]
}
```

The wrapper at `bin/scraper-native-host` must be executable, must reference an absolute
interpreter path (Chrome provides a minimal environment and an unspecified working
directory), and must not depend on `PATH`, the current directory, or shell profile
configuration.

## Extension identity

The custom extension is loaded unpacked from the stable absolute path
`.../scraper/extension`, which lives outside both profile directories and therefore
survives every reset.

Its extension ID must be fixed via a `key` field in `manifest.json`, because the
native-host manifest's `allowed_origins` requires an exact origin and the ID must not
change between sessions or between profile resets.

## Golden-profile model

Collection never runs against `data-dir-template`. The template is immutable during
ordinary operation and contains only:

- Chrome profile structure.
- I still don't care about cookies (`edibdbjcniadpccecjdfdjjppcpchdlm`).
- The custom extension registration.
- Chrome settings required by the collector.
- No Google account, no Chrome Sync, no intentional YouTube cookies, history, local
  storage, or login state.

### One-time template creation

Launch Chrome manually against the template directory:

```bash
"/Users/sor/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir="/Users/sor/Dev/youtube/research/pipelines/extraction/tools/scraper/data-dir-template" \
  --profile-directory="Default" \
  --no-first-run \
  --no-default-browser-check
```

Then:

1. Do not sign into Chrome. Do not enable Chrome Sync.
2. Install I still don't care about cookies from the Chrome Web Store.
3. Enable developer mode at `chrome://extensions`.
4. Load the custom extension from the extension source directory.
5. Record the custom extension's ID and confirm it matches the pinned `key`.
6. Clear all browsing data for all time.
7. Close Chrome completely and confirm no Chrome process holds the template.

No ad blocker is installed. The collector does not read the DOM, so an ad blocker buys
nothing, and because the entire data path is now the network layer, a filtering extension
is a liability there rather than a help.

Clearing browsing data does not remove extension-specific storage, so extension
configuration survives while website cookies and storage are cleared.

### Template maintenance

Template refresh is an explicit, separate operation. It never happens implicitly during a
collection run. It must verify Chrome is closed, back up the existing template, allow
Chrome and Web Store extensions to update, verify the custom extension still loads with
the same ID, clear all website browsing data, close Chrome, and only then replace the
golden template.

## Per-video runtime cycle

The disposable profile is destroyed and recreated **between every video**, not once per
run. Each video is collected in its own Chrome process against a freshly copied profile,
so that no client-side state carried by one video's page load can influence the next
video's recommendations.

Before launching Chrome for a video:

1. Verify that no Chrome process is using `data-dir` or `data-dir-template`. This is
   checked by inspecting the `Singleton*` lock entries in those directories and by
   checking for open file handles against them — not by looking for any Chrome process on
   the system. An unrelated personal Chrome instance running against a different
   user-data-dir is not a conflict and must not block a run.
2. Delete `data-dir` recursively if it exists.
3. Copy `data-dir-template` to `data-dir` with a reliable recursive macOS copy.
4. Delete `Singleton*` entries from the copied `data-dir`, so a template that was closed
   uncleanly cannot make the runtime Chrome refuse to start or attempt a handoff to a
   dead process.
5. Write `data-dir/NativeMessagingHosts/com.sor.yts.json`.
6. Launch Chrome against `data-dir`.

After the video completes or fails terminally:

1. Stop assigning work.
2. Wait for any in-flight SQLite transaction to finish.
3. Ask the extension to stop.
4. Terminate Chrome gracefully, escalating to a forced kill after a bounded deadline.
5. Wait until the Chrome process has fully exited.
6. Delete `data-dir`.

At controller startup, the previous runtime directory is deleted before the first copy,
so recovery is deterministic even if the previous process crashed before cleanup. The
individual Chrome database files inside a profile are never edited or "cleaned" as a
reset mechanism; the entire directory is replaced.

The controller never proceeds past the launch step blindly: if the extension has not
completed its handshake within a bounded deadline after Chrome starts, the controller
aborts loudly. A silently unloaded extension must never present as an idle-but-healthy
run.

## Privacy scope of the reset

The reset guarantees that profile-local website state does not persist from one video to
the next: cookies, HTTP cache, Cache Storage, local storage, IndexedDB, service workers,
history, form data, download history, and YouTube client-side state.

It does not provide anonymity and does not prevent server-side correlation through IP
address, browser and device characteristics, installed-extension effects, request timing
and behaviour, or Google-side logging. The baseline it produces is a clean *client* state
baseline, not a clean *identity* baseline.

## Process architecture

One Python package, two process modes:

```
python -m scraper controller
python -m scraper native-host
```

### Controller

Started directly by the user. Owns:

- The video-ID list from the command line.
- The profile reset cycle and the Chrome process lifecycle.
- Exclusive access to SQLite, enforced with a lock so two controllers cannot run at once.
- The Unix-domain socket.
- Receiving captured payloads, committing them, and acknowledging only after commit.
- Run, video, and batch status tracking.

The controller installs signal handlers so that interruption and termination follow the
same shutdown path as a normal exit, including Chrome teardown and profile removal.

### Native-host bridge

Chrome launches the bridge when the extension calls
`chrome.runtime.connectNative("com.sor.yts")`. It:

- Reads and writes Chrome native-messaging frames on stdin/stdout.
- Maintains one persistent Unix-domain socket connection to the controller.
- Forwards messages bidirectionally.
- Writes logs only to stderr.
- Contains no scheduling and no SQLite logic.

Because each video gets a new Chrome process, the controller sees a sequence of bridge
connections over a run. It must also tolerate a second bridge connecting while an older
one is still draining — for example after a service-worker restart — by tagging each
bridge connection with a generation and ignoring messages from superseded generations.

## Message framing

Chrome native messaging frames a message as a 32-bit length in **native byte order**,
followed by UTF-8 JSON. Size limits are asymmetric: a message from the host to the
extension may be at most 1 MB; a message from the extension to the host may be up to
64 MiB. Captured payloads flow in the roomy direction; anything the controller sends must
respect the 1 MB ceiling.

The controller Unix socket uses length-prefixed framing, not newline-delimited JSON.

## Connection topology

```
one persistent extension ↔ native-host connection
one persistent native-host ↔ controller Unix-socket connection
```

A new native host or socket is never opened per message. Reconnection uses bounded
exponential backoff.

## Service-worker lifetime

An open `connectNative()` port does **not** keep a Manifest V3 service worker alive.
Opening a port does not reset the idle timer; only messages crossing it do, and the
roughly 30-second idle timeout still applies. The quiet periods in this design — waiting
for a page to load, waiting for a payload after a scroll, waiting for a commit
acknowledgement — are long enough to hit it.

Therefore:

- The extension sends a periodic heartbeat over the native port, with `chrome.alarms` as
  the floor for scheduling it.
- Service-worker restart is treated as an expected event, not an error. On startup the
  service worker rehydrates its state from `chrome.storage.session`, reconnects to the
  native host, and re-announces its state to the controller before doing anything else.
- Durable state lives in `chrome.storage.session`, never in service-worker globals alone:
  worker tab ID, current video ID, current navigation epoch, pending acknowledgement
  identifiers, and current navigation state. The controller and SQLite remain the
  authoritative source of truth.

## Extension architecture

- Manifest V3, TypeScript, built with a minimal bundler such as esbuild.
- A service worker, a MAIN-world capture script, and an isolated-world collector script.
- Vanilla DOM APIs. No UI framework, no required popup or visible UI.
- No `activeTab`: collection is automatic and must not require a user gesture.

Host permissions and content-script matches must cover `https://*.youtube.com/*`, not
only `https://youtube.com/*`. YouTube canonicalises watch pages to `www.youtube.com`, so
a match pattern limited to the bare apex host would never fire.

### Service-worker responsibilities

The service worker owns the native-host connection, the worker tab ID, the current video
assignment, navigation, routing between the collector script and the native host, retry
state, and stop state.

It must verify that every collector message originates from the dedicated worker tab, and
it must validate every controller-supplied URL before navigating:

```ts
function isAllowedYouTubeUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      (url.hostname === "youtube.com" || url.hostname.endsWith(".youtube.com"))
    );
  } catch {
    return false;
  }
}
```

Arbitrary schemes and arbitrary hosts received over native messaging are never navigated
to.

## Navigation model

Exactly one dedicated worker tab is used. The service worker creates it once per Chrome
session and stores its ID; navigation is always a top-level `chrome.tabs.update` to the
target URL. Links are never clicked, and YouTube's single-page-application navigation is
never relied upon, so each URL produces a clean content-script lifecycle and a clean
initial payload.

Every navigation is stamped by the service worker with a monotonically increasing
**navigation epoch**. The epoch is handed to the collector script during its ready
handshake and is stamped on every message it sends. The service worker hard-drops any
message carrying a stale epoch, so an in-flight payload from an abandoned page load can
never be attributed to the current one.

The collector script's first action is a request/response handshake that returns its
assignment — video ID, navigation epoch, and collection configuration. It never infers
its assignment from the URL alone.

## Protocol invariants

- Every message carries a protocol version and the controller-issued run identifier.
- The first message on a new connection is a handshake. The extension announces its
  protocol version and whether this is a fresh start or a service-worker restart; the
  controller replies with the authoritative run identifier. The extension never invents a
  run identifier. A protocol-version mismatch aborts the run with a clear error rather
  than degrading.
- At most one unacknowledged batch is in flight per video at any time.
- Data is never acknowledged before its SQLite transaction commits. The sequence is:
  collector sends batch → controller opens a transaction → controller writes the payload
  and a received-batch record → commit succeeds → controller sends the acknowledgement →
  collector continues.
- Every batch carries a unique identifier used for both correlation and idempotent
  storage.
- Failure to commit produces an explicit negative acknowledgement carrying whether the
  failure is retryable. A timeout is never the only signal that something went wrong.
- The collector keeps an unacknowledged batch in memory and retries it after
  reconnecting. Captured payloads are never silently discarded.
- Video completion must be acknowledged before the run advances past that video.
- On native-messaging disconnect: scrolling stops, the unacknowledged batch is retained,
  reconnection uses bounded exponential backoff, and collection resumes only after
  reconnecting.
- If the controller is unreachable, the bridge reports a structured
  controller-unavailable error and the extension pauses.

## Messages

One video is collected per browser session, so the protocol carries no queue, no batch of
URLs, and no index. The controller hands over exactly one video in the handshake reply.

Every message carries `type` and `protocolVersion`.

**Extension → controller**

| Type | Fields | Notes |
|---|---|---|
| `hello` | `restart` | First message on a connection. No `runId`; the extension does not have one yet. `restart` distinguishes a fresh service worker from a restarted one. |
| `payload` | `runId`, `videoId`, `navEpoch`, `batchId`, `source`, `endpoint`, `capturedAt`, `body` | One captured payload per message. `source` is `initial` or `network`. |
| `video_done` | `runId`, `videoId`, `navEpoch`, `reason` | |
| `video_failed` | `runId`, `videoId`, `navEpoch`, `errorCode`, `message` | |
| `ping` | `runId` | Heartbeat. |

**Controller → extension**

| Type | Fields | Notes |
|---|---|---|
| `hello_ack` | `runId`, `videoId`, `videoUrl`, `config` | Assigns the single video for this session and issues the authoritative run identifier. |
| `payload_ack` | `batchId` | Sent only after the transaction commits. |
| `payload_nack` | `batchId`, `retryable` | |
| `video_done_ack` | `videoId` | |
| `video_failed_ack` | `videoId` | |
| `pong` | | |
| `stop` | | |

`body` is sent as a nested JSON object, never as an encoded string, so payloads are not
inflated by escaping. One captured payload per message; payloads are never split across
messages. A payload whose encoded message would exceed 32 MiB is not sent; it produces a
structured failure instead. This sits well inside the 64 MiB extension-to-host ceiling,
and no controller-to-extension message approaches the 1 MB ceiling in the other
direction.

### Error codes

`PAGE_READY_TIMEOUT`, `CONSENT_WALL`, `VIDEO_UNAVAILABLE`, `AGE_RESTRICTED`,
`LOGIN_REQUIRED`, `SHORTS_EXCLUDED`, `UNEXPECTED_NAVIGATION`, `SCHEMA_UNRECOGNISED`,
`CHAIN_STALLED`, `CIRCUIT_BREAKER`, `PAYLOAD_TOO_LARGE`, `CONTROLLER_UNAVAILABLE`.

Every code is recorded against the video. A video that failed is never recorded as having
legitimately yielded nothing.

## Storage semantics

Only the controller touches SQLite. It holds a lock for the lifetime of the run so a
second controller cannot start. The database runs in WAL mode with a busy timeout, and
with full synchronous commits, because acknowledgements assert durability and a
non-fsynced commit would make that assertion false.

Every video has exactly one record, carrying a terminal status of either completed or
failed, and in the failed case the error code and message. Recommendations and comments
are linked to the source video ID they were extracted from.

- **Skipping.** A requested video ID is skipped only if it previously **completed**.
  Previously failed videos are retried.
- **Partial results are not kept.** If a video fails after already committing
  recommendations or comments, those rows are deleted and the video record is written as
  failed with its reason. A truncated result set must never be readable as a complete
  one, and must never cause the video to be skipped on a later run.
- **Writes are idempotent.** Replaying an unacknowledged batch after a reconnect produces
  no duplicates.

The concrete column set for recommendations and comments follows from the fields YouTube
actually delivers, and is fixed against measured payloads rather than assumed.

### Raw payload retention

Raw payloads are kept on disk, outside SQLite and outside both Chrome profile
directories:

```
data/raw/<video_id>/<run_id>/<seq>-<source>.json.gz
```

They are gzipped, since this content compresses heavily.

The write order is: raw file written and flushed, then the SQLite transaction commits
recording the parsed rows and the raw file's path, then the acknowledgement is sent. In
that order the database can never reference a raw file that does not exist.

Raw files are retained for videos that fail, even though their parsed rows are deleted.
On a failure the raw payload is the diagnostic evidence, and the run-scoped path keeps a
retry's output from colliding with the failed attempt's.

Because raw is retained, a field we did not extract on the first pass can be recovered by
re-parsing rather than by re-collecting. Re-parsing historical raw payloads is a
first-class operation, not a recovery hack.

## Input hardening

The service worker treats controller-supplied URLs as untrusted, and the controller
treats extension-supplied payloads as untrusted. The controller enforces bounded caps on
payload size, payloads per video, and total bytes per video, so that unexpected page
behaviour cannot fill the disk.

## Rejected alternatives

These were considered and deliberately not chosen. Recorded so they are not re-litigated.

**`chrome.browsingData.remove()` as the reset mechanism.** Cleanup might never run after
a crash; a profile holds more local state than the API's website-data categories cover; a
filesystem-level replacement is easier to reason about; and the extension would need the
sensitive `browsingData` permission for nothing else.

**Incognito mode as the isolation mechanism.** Extensions require separate incognito
authorisation, extension behaviour differs in incognito, and the golden-template approach
gives explicit lifecycle control that incognito does not.

**Enterprise force-install policy instead of a golden template.** Chrome policy can
install extensions into a fresh profile automatically, which would remove the need for a
template. It brings managed-browser configuration, extension-install timing, policy
maintenance, and distribution requirements for the custom extension. Not worth it for a
local single-user collector.

**Calling YouTube's internal API directly.** Extracting the API key, client version, and
visitor data from the page and issuing `/youtubei/v1/next` requests ourselves would be
faster and would remove the need to scroll. It also makes us responsible for header,
token, and attestation correctness in perpetuity, and breaks the moment proof-of-origin
enforcement extends to that endpoint. Observation has neither exposure.

**The official YouTube Data API.** It cannot serve recommendations at all —
`search.list`'s `relatedToVideoId` parameter was deprecated in June 2023 and stopped
returning results in August 2023, with no replacement. It can serve comments well, but
those are not the comments the watch page presented, which is what this collector is
about.

**`chrome.webRequest` / `declarativeNetRequest` for capture.** Neither can read response
bodies. This is not a preference; they cannot do the job.

**An ad blocker in the profile.** It was there to clean up the DOM. Nothing reads the
DOM, and a filtering extension sitting on the network layer is a liability now that the
network layer is the entire data path.

## Logging

Structured logs throughout.

- The native host never writes to stdout; stdout is reserved for the length-prefixed
  native-messaging protocol. Its logs go to stderr, which the controller captures because
  it owns the Chrome process.
- Python logs to stderr or to a file outside `data-dir`.
- Extension logs carry the run ID and the current video ID.
- Controller logs carry message type, video ID, batch ID, transaction result, and
  duration.
- Captured payloads are not logged unless explicitly enabled.

## Acceptance criteria

1. Running without `--video-ids` prints an error and exits non-zero, touching no profile
   directory.
2. A Chrome instance holding `data-dir` or `data-dir-template` blocks the run; an
   unrelated Chrome instance on a different user-data-dir does not.
3. Every video begins by replacing `data-dir` from `data-dir-template`, with `Singleton*`
   entries removed and the native-host manifest written into the copy.
4. Chrome starts from the exact configured executable path, and the runtime profile
   contains both required extensions.
5. The extension completes its handshake within the startup deadline; failure to do so
   aborts the run with a clear error.
6. The extension uses one tab and performs a top-level navigation to each watch page.
7. The initial payload is captured on every watch page, including when it is assigned
   before the collector script is ready.
8. Wrapping `fetch` and `XMLHttpRequest` does not alter page behaviour; the page renders
   and paginates exactly as it does without the extension.
9. The extension makes no outbound request to YouTube's internal API.
10. No video plays. A video short enough to finish during collection does not cause the
    worker tab to navigate away.
11. Collection stops at the recommendation cap or at chain exhaustion, and the video
    record says which. Neither is recorded as a failure.
12. Only video entries are stored, each carrying both its raw and its normalised sidebar
    position.
13. SQLite writes are idempotent under batch replay.
14. Video completion is acknowledged before the run advances.
15. `data-dir` is removed after each video and after a clean shutdown, and the golden
    template is unchanged by any run.
16. Killing the controller mid-batch loses no acknowledged data and leaves no video stuck
    in a running state on the next start.
17. Killing the service worker mid-video is recovered from rather than being reported as
    completion.
18. A video yielding no recognised recommendation payload reports a structured failure
    rather than normal completion.
19. A video that fails after partial collection leaves a failed record with its reason and
    no orphaned recommendation or comment rows, and is retried rather than skipped on the
    next run.
20. Re-running with an already-completed video ID skips it and prints which IDs were
    skipped.
21. Payload interpretation is covered by tests against saved payload fixtures, so a
    YouTube schema change is caught without a live run.
