/**
 * Isolated-world collector script (SPEC-V3, "Collector script"). Performs
 * the ready handshake with the service worker, receives captured payloads
 * from capture.ts over postMessage (accepting only same-window messages),
 * forwards them to the service worker one unacknowledged batch at a time,
 * drives scrolling, and decides completion or structured failure.
 */

import type { CapturedPayload, YtsWindowMessage } from "./capture-protocol";
import type {
  AssignMessage,
  CollectorToWorkerMessage,
  PayloadAckMessage,
  PayloadNackMessage,
  WorkerToCollectorMessage,
} from "./internal-protocol";
import { PORT_NAME } from "./internal-protocol";
import type { ErrorCode } from "./protocol";

const SIDEBAR_RESULTS_PATH = [
  "contents",
  "twoColumnWatchNextResults",
  "secondaryResults",
  "secondaryResults",
];
const WATCH_NEXT_FEED_TARGET = "watch-next-feed";

function get(obj: unknown, key: string): unknown {
  if (obj === null || typeof obj !== "object") return undefined;
  return (obj as Record<string, unknown>)[key];
}

/** Finds the sidebar's raw entry list in either the initial-payload shape
 * or the continuation-payload shape (SPEC-V3, "Recommendations" — the same
 * two shapes src/scraper/parser/recommendations.py's _iter_sidebar_entries
 * detects). Returns null if neither shape is recognised in this payload. */
function sidebarEntries(body: unknown): unknown[] | null {
  let node: unknown = body;
  for (const key of SIDEBAR_RESULTS_PATH) node = get(node, key);
  const results = get(node, "results");
  if (Array.isArray(results) && results.length > 0) {
    const contents = get(get(results[0], "itemSectionRenderer"), "contents");
    if (Array.isArray(contents)) return contents;
  }

  const endpoints = get(body, "onResponseReceivedEndpoints");
  if (Array.isArray(endpoints)) {
    for (const endpoint of endpoints) {
      const action = get(endpoint, "appendContinuationItemsAction");
      if (action && get(action, "targetId") === WATCH_NEXT_FEED_TARGET) {
        const items = get(action, "continuationItems");
        if (Array.isArray(items)) return items;
      }
    }
  }

  return null;
}

/** Does this payload's sidebar entry list end in a continuation token?
 * null means "this payload doesn't carry a sidebar list at all" (e.g. the
 * player-response payload) — not evidence either way. */
function hasFurtherContinuation(body: unknown): boolean | null {
  const entries = sidebarEntries(body);
  if (entries === null || entries.length === 0) return null;
  const last = entries[entries.length - 1];
  return get(last, "continuationItemRenderer") !== undefined;
}

/** Depth-first search for every value under `key`, mirroring
 * src/scraper/parser/_walk.py's find_all_by_key — kept as one
 * implementation of "find this key wherever it appears" per that module's
 * own docstring, just duplicated across the language boundary since the
 * collector can't import Python. */
function* findAllByKey(node: unknown, key: string): Generator<unknown> {
  if (node !== null && typeof node === "object") {
    if (!Array.isArray(node)) {
      const record = node as Record<string, unknown>;
      if (key in record) yield record[key];
      for (const value of Object.values(record)) yield* findAllByKey(value, key);
    } else {
      for (const item of node) yield* findAllByKey(item, key);
    }
  }
}

/** Mirrors comments.py's find_comments_header + _parse_count_text: the
 * comments header's count, if this payload carries one and it parses as a
 * plain digit string. null covers both "no header in this payload" and
 * "header present but count not yet resolved" (e.g. just "Comments" with
 * no number, before a later continuation fills it in) — same ambiguity
 * comments.py's own docstring describes. */
function commentsHeaderCount(body: unknown): number | null {
  const header = findAllByKey(body, "commentsHeaderRenderer").next().value;
  if (header === undefined) return null;
  const runs = get(get(header, "countText"), "runs");
  if (!Array.isArray(runs) || runs.length === 0) return null;
  const text = String(get(runs[0], "text") ?? "").replace(/,/g, "");
  return /^\d+$/.test(text) ? Number(text) : null;
}

/** Presence-only check mirroring comments.py's extract_comments source key
 * (commentThreadRenderer) — a cheap proxy for "at least one comment thread
 * has arrived," without needing the full entity-store join extract_comments
 * does for real extraction. */
function hasCommentThread(body: unknown): boolean {
  return !findAllByKey(body, "commentThreadRenderer").next().done;
}

/** SPEC-V3, acceptance criterion 10: "No video plays." --autoplay-policy
 * document-user-activation-required (see config.py, CHROME_LAUNCH_FLAGS)
 * turned out not to be enough on its own: Chromium exempts *muted* media
 * from that policy regardless of the flag, and --mute-audio is also set —
 * so the video can still visually autoplay, just silently. Observed live.
 * Enforced directly here instead of relying on the launch flag: pause any
 * video element the instant it exists, and again the instant anything
 * tries to play it, for the lifetime of the page. */
function suppressAutoplay(): void {
  const pauseAll = () => {
    for (const video of document.querySelectorAll("video")) {
      if (!video.paused) video.pause();
    }
  };
  pauseAll();
  new MutationObserver(pauseAll).observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
  // The "play" event doesn't bubble, but capturing at the document catches
  // it on any descendant, including video elements added after this runs.
  document.addEventListener(
    "play",
    (event) => {
      const target = event.target as HTMLVideoElement;
      if (typeof target?.pause === "function") target.pause();
    },
    true,
  );
}

class Collector {
  private readonly port: chrome.runtime.Port;
  private assignment: AssignMessage | null = null;
  private assignmentWaiters: Array<(msg: AssignMessage) => void> = [];

  private queue: CapturedPayload[] = [];
  private draining = false;
  private inFlight: { batchId: string; resolve: (ok: boolean, retryable: boolean) => void } | null =
    null;

  private stopped = false;
  private paused = false;
  private capturedAnything = false;
  private recognisedAnything = false;
  // Specifically ytInitialData, not "any initial-source capture" — the
  // player response (ytInitialPlayerResponse) reliably arrives first and
  // fast regardless of whether the sidebar ever loads, so gating page-ready
  // on "either initial global" let a missing ytInitialData sail past
  // PAGE_READY_TIMEOUT undetected and scroll blind for a long time instead
  // of failing fast and clearly. See DECISIONS.md, Phase 7.
  private sawInitialData = false;
  private lastContinuationVerdict: boolean | null = null;
  // Counts only sidebar-shaped payloads (hasFurtherContinuation() !== null)
  // — the signal driveScrolling() uses to decide whether a scroll round
  // made progress. Raw "did anything get captured" was tried first and was
  // wrong: YouTube can re-fire ytInitialPlayerResponse (an "initial"-source
  // capture, unrelated to pagination) independently of scrolling, which
  // kept resetting the stall counter and scrolled for the full
  // maxScrollRounds ceiling — observed live as runaway scrolling with no
  // recommendations ever arriving. See DECISIONS.md, Phase 7.
  private recognisedPayloadCount = 0;
  private stalledRounds = 0;

  // "wait for comments" tracking (config.waitForComments) — see
  // commentsResolved()'s docstring for what these mean together.
  private commentsCount: number | null = null;
  private sawCommentThread = false;
  // Set instead of completing immediately when a stopping condition fires
  // (chain exhausted, or the controller's recommendation-cap stop) while
  // comments aren't resolved yet. The pending reason is honoured as soon
  // as comments resolve, or once maxScrollRounds/maxPageDurationMs forces
  // the wait to end anyway.
  private pendingCompletionReason: "max_recommendations_reached" | "chain_exhausted" | null =
    null;
  private readonly startedAt = Date.now();

  constructor() {
    suppressAutoplay();
    this.port = chrome.runtime.connect({ name: PORT_NAME });
    this.port.onMessage.addListener((raw: unknown) =>
      this.onWorkerMessage(raw as WorkerToCollectorMessage),
    );
    window.addEventListener("message", (event: MessageEvent) => this.onWindowMessage(event));
  }

  async run(): Promise<void> {
    this.postToWorker({ type: "assign_request" });
    this.assignment = await this.waitForAssignment();
    console.log(
      `[yts] run=${this.assignment.runId} video=${this.assignment.videoId} collector assigned`,
    );

    // Tell capture.ts it's safe to flush its buffer and stop buffering.
    const ready: YtsWindowMessage = { __yts: "collector-ready" };
    window.postMessage(ready, window.location.origin);

    void this.watchPageReadyDeadline();
    await this.driveScrolling();
  }

  private postToWorker(msg: CollectorToWorkerMessage): void {
    this.port.postMessage(msg);
  }

  private waitForAssignment(): Promise<AssignMessage> {
    if (this.assignment) return Promise.resolve(this.assignment);
    return new Promise((resolve) => this.assignmentWaiters.push(resolve));
  }

  private onWorkerMessage(msg: WorkerToCollectorMessage): void {
    switch (msg.type) {
      case "assign":
        this.assignment = msg;
        for (const waiter of this.assignmentWaiters.splice(0)) waiter(msg);
        return;
      case "payload_ack":
        this.resolveInFlight(msg, true, false);
        return;
      case "payload_nack":
        this.resolveInFlight(msg, false, msg.retryable);
        return;
      case "done_ack":
      case "failed_ack":
        this.stopped = true;
        return;
      case "stop":
        this.handleStop();
        return;
      case "pause":
        this.paused = true;
        return;
      case "resume":
        this.paused = false;
        return;
    }
  }

  private resolveInFlight(
    msg: PayloadAckMessage | PayloadNackMessage,
    ok: boolean,
    retryable: boolean,
  ): void {
    if (!this.inFlight || this.inFlight.batchId !== msg.batchId) return;
    const { resolve } = this.inFlight;
    this.inFlight = null;
    resolve(ok, retryable);
  }

  private handleStop(): void {
    // Mid-video (we haven't reported completion ourselves yet): the
    // controller-side recommendation cap was reached — see DECISIONS.md,
    // Phase 6. The controller holds the true stored (deduped) count, not
    // this script, so it is authoritative on this reason. A `stop` that
    // arrives after we've already reported completion is just the ordinary
    // end-of-session signal and needs no further action.
    if (this.stopped) return;
    if (!this.commentsResolved()) {
      this.pendingCompletionReason = "max_recommendations_reached";
      return;
    }
    this.stopped = true;
    this.reportDone("max_recommendations_reached");
  }

  /** True when there's no reason left to keep scrolling for comments —
   * either the feature is off, the comments header resolved to a real
   * zero, or at least one thread has actually arrived. Bundled with
   * pendingCompletionReason: a stopping condition that fires while this is
   * false doesn't complete immediately, it waits (still bounded by
   * maxScrollRounds/maxPageDurationMs in driveScrolling). */
  private commentsResolved(): boolean {
    if (!this.assignment?.config.waitForComments) return true;
    return this.commentsCount === 0 || this.sawCommentThread;
  }

  private onWindowMessage(event: MessageEvent): void {
    if (event.source !== window) return;
    const data = event.data as YtsWindowMessage | undefined;
    if (data?.__yts !== "captured-payload") return;

    this.capturedAnything = true;
    this.sawInitialData ||= data.payload.endpoint === "ytInitialData";

    const verdict = hasFurtherContinuation(data.payload.body);
    if (verdict !== null) {
      this.recognisedAnything = true;
      this.lastContinuationVerdict = verdict;
      this.recognisedPayloadCount += 1;
    }

    const count = commentsHeaderCount(data.payload.body);
    if (count !== null) this.commentsCount = count;
    this.sawCommentThread ||= hasCommentThread(data.payload.body);

    this.queue.push(data.payload);
    void this.drainQueue();
  }

  private hasPendingWork(): boolean {
    return this.inFlight !== null || this.queue.length > 0;
  }

  /** Waits until nothing captured so far is still queued or awaiting an
   * ack from the controller. "stopped" means the session ended while
   * waiting (caller should just return); "timeout" means pending work
   * never settled within ackTimeoutMs — SPEC-V3's "acknowledgement
   * timeout" circuit breaker, which nothing previously enforced. */
  private async waitForPendingWorkToSettle(
    ackTimeoutMs: number,
  ): Promise<"settled" | "timeout" | "stopped"> {
    const deadline = Date.now() + ackTimeoutMs;
    while (this.hasPendingWork()) {
      if (this.stopped) return "stopped";
      if (Date.now() > deadline) return "timeout";
      await sleep(100);
    }
    return "settled";
  }

  private async drainQueue(): Promise<void> {
    if (this.draining) return;
    this.draining = true;
    try {
      while (this.queue.length > 0 && !this.stopped) {
        const payload = this.queue.shift();
        if (!payload) break;
        await this.sendBatch(payload);
      }
    } finally {
      this.draining = false;
    }
  }

  private sendBatch(payload: CapturedPayload): Promise<void> {
    const assignment = this.assignment;
    if (!assignment) return Promise.resolve();
    const batchId = crypto.randomUUID();

    return new Promise((resolve) => {
      const attempt = () => {
        this.inFlight = {
          batchId,
          resolve: (ok, retryable) => {
            if (ok || !retryable) {
              resolve();
              return;
            }
            setTimeout(attempt, 250);
          },
        };
        this.postToWorker({
          type: "forward_payload",
          navEpoch: assignment.navEpoch,
          batchId,
          payload,
        });
      };
      attempt();
    });
  }

  private async watchPageReadyDeadline(): Promise<void> {
    const config = this.assignment?.config;
    if (!config) return;
    await sleep(config.pageReadyTimeoutMs);
    if (!this.sawInitialData && !this.stopped) {
      this.stopped = true;
      this.reportFailed("PAGE_READY_TIMEOUT", "ytInitialData not captured within deadline");
    }
  }

  private async driveScrolling(): Promise<void> {
    const config = this.assignment?.config;
    if (!config) return;

    let rounds = 0;
    while (!this.stopped) {
      // A pending completion reason (chain exhausted, or the controller's
      // recommendation-cap stop) that arrived while comments weren't
      // resolved yet becomes final the moment they resolve.
      if (this.pendingCompletionReason && this.commentsResolved()) {
        this.stopped = true;
        this.reportDone(this.pendingCompletionReason);
        return;
      }

      if (Date.now() - this.startedAt > config.maxPageDurationMs) {
        this.stopped = true;
        if (this.pendingCompletionReason) {
          this.reportDone(this.pendingCompletionReason);
        } else {
          this.reportFailed("CIRCUIT_BREAKER", "maximum page duration exceeded");
        }
        return;
      }
      if (this.lastContinuationVerdict === false) {
        if (this.commentsResolved()) {
          this.stopped = true;
          this.reportDone("chain_exhausted");
          return;
        }
        // Recommendation chain is done, but comments aren't resolved yet —
        // keep scrolling in case that triggers comment loading too,
        // bounded by maxScrollRounds/maxPageDurationMs like everything
        // else here.
        this.pendingCompletionReason = "chain_exhausted";
      }
      if (rounds >= config.maxScrollRounds) {
        this.stopped = true;
        if (this.pendingCompletionReason) {
          this.reportDone(this.pendingCompletionReason);
        } else {
          this.reportFailed("CIRCUIT_BREAKER", "maximum scroll rounds exceeded");
        }
        return;
      }
      if (this.paused) {
        await sleep(250);
        continue;
      }

      // No need to scroll for more while the last capture is still
      // unacknowledged — scrolling further before the controller has
      // durably accepted what we already have just risks piling up
      // unacknowledged captures on a slow connection, for no benefit.
      const settleResult = await this.waitForPendingWorkToSettle(config.ackTimeoutMs);
      if (settleResult === "stopped") return;
      if (settleResult === "timeout") {
        this.stopped = true;
        this.reportFailed("CIRCUIT_BREAKER", "acknowledgement timed out before next scroll");
        return;
      }

      const before = this.recognisedPayloadCount;
      window.scrollTo(0, document.documentElement.scrollHeight);
      rounds += 1;
      await sleep(jittered(config.scrollDelayMs, config.delayJitter));
      const producedSomething = this.recognisedPayloadCount > before;

      if (producedSomething) {
        this.stalledRounds = 0;
        continue;
      }
      // While waiting specifically for comments (recommendation side is
      // already done — pendingCompletionReason is set), "no new sidebar
      // progress" is expected, not a stall: recognisedPayloadCount tracks
      // sidebar-shaped payloads only, and there won't be more of those.
      // maxScrollRounds/maxPageDurationMs above still bound how long this
      // waits.
      if (this.pendingCompletionReason) continue;
      this.stalledRounds += 1;
      if (this.stalledRounds >= 3) {
        this.stopped = true;
        this.reportFailed("CHAIN_STALLED", "no new payload after repeated scroll attempts");
        return;
      }
    }

    if (!this.capturedAnything) {
      this.reportFailed("PAGE_READY_TIMEOUT", "nothing captured before the page settled");
    } else if (!this.recognisedAnything) {
      this.reportFailed(
        "SCHEMA_UNRECOGNISED",
        "no captured payload had a recognised sidebar structure",
      );
    }
  }

  private reportDone(reason: "max_recommendations_reached" | "chain_exhausted"): void {
    if (!this.assignment) return;
    console.log(`[yts] video=${this.assignment.videoId} collector done: ${reason}`);
    this.postToWorker({ type: "report_done", navEpoch: this.assignment.navEpoch, reason });
  }

  private reportFailed(errorCode: ErrorCode, message: string): void {
    if (!this.assignment) return;
    console.log(`[yts] video=${this.assignment.videoId} collector failed: ${errorCode} ${message}`);
    this.postToWorker({
      type: "report_failed",
      navEpoch: this.assignment.navEpoch,
      errorCode,
      message,
    });
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function jittered(baseMs: number, fraction: number): number {
  const delta = baseMs * fraction * (Math.random() * 2 - 1);
  return Math.max(0, baseMs + delta);
}

void new Collector().run();
