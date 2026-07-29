# Wire protocol

The contract between the controller, the native-host bridge, and the extension. Extracted
from `SPEC.md` because it is the one part three separately-built components must agree on
exactly.

`PROTOCOL_VERSION` is **1**. Every message carries `type` and `protocolVersion`.

## Connection topology

```
extension  ↔  native host       (Chrome native messaging, over stdin/stdout)
native host ↔  controller        (Unix domain socket)
```

One persistent connection on each hop. A new native host or socket is never opened per
message. Reconnection uses bounded exponential backoff.

Because each video gets a new Chrome process, the controller sees a sequence of bridge
connections over a run. It must also tolerate a second bridge connecting while an older one
is still draining — after a service-worker restart, for example — by tagging each bridge
connection with a generation and ignoring messages from superseded generations.

## Framing

Both hops are length-prefixed, never newline-delimited. **The two hops use different byte
orders, and this is not an inconsistency to clean up:**

| Hop | Prefix | Why |
|---|---|---|
| extension ↔ native host | 32-bit **native** byte order (`struct` format `=I`) | Chrome dictates this format; it is not ours to choose. |
| native host ↔ controller | 32-bit **big-endian** / network order (`!I`) | Nothing external pins this hop, so the ordinary unambiguous default was chosen. |

Then UTF-8 JSON in both cases.

Size limits on the Chrome boundary are asymmetric: host→extension is capped at 1 MB,
extension→host at 64 MiB. Captured payloads flow in the roomy direction. Nothing the
controller sends approaches the 1 MB ceiling.

`body` is sent as a nested JSON object, never as an encoded string, so payloads are not
inflated by escaping. One captured payload per message; payloads are never split across
messages. A payload whose encoded message would exceed 32 MiB is not sent — it produces a
structured failure instead, which sits well inside the 64 MiB ceiling.

## Invariants

- Every message carries a protocol version and the controller-issued run identifier.
- The first message on a new connection is a handshake. The extension announces its
  protocol version and whether this is a fresh start or a service-worker restart; the
  controller replies with the authoritative run identifier. The extension never invents a
  run identifier. A protocol-version mismatch aborts the run with a clear error rather than
  degrading — in practice by closing the connection without a `hello_ack`, since the
  message table has no error message to carry it.
- At most one unacknowledged batch is in flight per video at any time.
- Data is never acknowledged before its SQLite transaction commits. The sequence is:
  collector sends batch → controller opens a transaction → controller writes the payload and
  a received-batch record → commit succeeds → controller sends the acknowledgement →
  collector continues.
- Every batch carries a unique identifier used for both correlation and idempotent storage.
- Failure to commit produces an explicit negative acknowledgement carrying whether the
  failure is retryable. A timeout is never the only signal that something went wrong.
- The collector keeps an unacknowledged batch in memory and retries it after reconnecting.
  Captured payloads are never silently discarded.
- Video completion must be acknowledged before the run advances past that video.
- On native-messaging disconnect: scrolling stops, the unacknowledged batch is retained,
  reconnection uses bounded exponential backoff, and collection resumes only after
  reconnecting.
- If the controller is unreachable, the bridge reports a structured controller-unavailable
  error and the extension pauses.

## Messages

One video is collected per browser session, so the protocol carries no queue, no batch of
URLs, and no index. The controller hands over exactly one video in the handshake reply.

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
| `stop` | | See below — `stop` has two meanings. |

Twelve types total, five up and seven down. Do not add messages that aren't in these tables.

### `stop` is overloaded by design

The recommendation cap is enforced by the controller, not the extension, because only the
controller knows the true post-filter, post-dedup stored count. So `stop` means one of two
things depending on when it arrives:

- **Mid-video**, before the collector's own completion report: the cap was hit — report
  `video_done(reason=max_recommendations_reached)`.
- **After** the collector's completion report: the ordinary end-of-session signal.

Chain exhaustion, the other completion reason, *is* decided by the collector — it needs no
full parse, only a check for whether a payload's sidebar entry list ends in a
`continuationItemRenderer`.

### `hello_ack.config`

The `CollectionConfig` shape. Mirrored field-for-field between `src/scraper/config.py` and
`extension/src/protocol.ts`.

```ts
{
  maxRecommendations: number;
  scrollDelayMs: number;
  delayJitter: number;
  pageReadyTimeoutMs: number;
  ackTimeoutMs: number;
  maxScrollRounds: number;
  maxPageDurationMs: number;
  waitForComments: boolean;
}
```

Values are operational tuning, not pinned by this document. `waitForComments` off by
default: when on, neither chain exhaustion nor the cap completes the video until the
comments header resolves to a real count — still bounded by `maxScrollRounds` and
`maxPageDurationMs`.

### Error codes

`PAGE_READY_TIMEOUT`, `CONSENT_WALL`, `VIDEO_UNAVAILABLE`, `AGE_RESTRICTED`,
`LOGIN_REQUIRED`, `SHORTS_EXCLUDED`, `UNEXPECTED_NAVIGATION`, `SCHEMA_UNRECOGNISED`,
`CHAIN_STALLED`, `CIRCUIT_BREAKER`, `PAYLOAD_TOO_LARGE`, `CONTROLLER_UNAVAILABLE`.

Every code is recorded against the video. A video that failed is never recorded as having
legitimately yielded nothing.

Four of these have no detection wired up today — see `BL-014` in `BACKLOG.md`.

## Out of band: `controller_unavailable`

Not one of the twelve. By definition the controller is unreachable when it fires, so it
cannot travel the controller socket — the bridge synthesizes it locally and sends it up to
the extension, mirroring the `{type, protocolVersion}` convention used everywhere else.
→ `src/scraper/native_host.py`, `CONTROLLER_UNAVAILABLE_MESSAGE_TYPE`
