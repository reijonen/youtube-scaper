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
  private sawInitialPayload = false;
  private lastContinuationVerdict: boolean | null = null;
  private stalledRounds = 0;
  private readonly startedAt = Date.now();

  constructor() {
    this.port = chrome.runtime.connect({ name: PORT_NAME });
    this.port.onMessage.addListener((raw: unknown) =>
      this.onWorkerMessage(raw as WorkerToCollectorMessage),
    );
    window.addEventListener("message", (event: MessageEvent) => this.onWindowMessage(event));
  }

  async run(): Promise<void> {
    this.postToWorker({ type: "assign_request" });
    this.assignment = await this.waitForAssignment();

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
    this.stopped = true;
    this.reportDone("max_recommendations_reached");
  }

  private onWindowMessage(event: MessageEvent): void {
    if (event.source !== window) return;
    const data = event.data as YtsWindowMessage | undefined;
    if (data?.__yts !== "captured-payload") return;

    this.capturedAnything = true;
    this.sawInitialPayload ||= data.payload.source === "initial";

    const verdict = hasFurtherContinuation(data.payload.body);
    if (verdict !== null) {
      this.recognisedAnything = true;
      this.lastContinuationVerdict = verdict;
    }

    this.queue.push(data.payload);
    void this.drainQueue();
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
    if (!this.sawInitialPayload && !this.stopped) {
      this.stopped = true;
      this.reportFailed("PAGE_READY_TIMEOUT", "no initial payload captured within deadline");
    }
  }

  private async driveScrolling(): Promise<void> {
    const config = this.assignment?.config;
    if (!config) return;

    let rounds = 0;
    while (!this.stopped) {
      if (Date.now() - this.startedAt > config.maxPageDurationMs) {
        this.stopped = true;
        this.reportFailed("CIRCUIT_BREAKER", "maximum page duration exceeded");
        return;
      }
      if (this.lastContinuationVerdict === false) {
        this.stopped = true;
        this.reportDone("chain_exhausted");
        return;
      }
      if (rounds >= config.maxScrollRounds) {
        this.stopped = true;
        this.reportFailed("CIRCUIT_BREAKER", "maximum scroll rounds exceeded");
        return;
      }
      if (this.paused) {
        await sleep(250);
        continue;
      }

      const before = this.queue.length + (this.inFlight ? 1 : 0);
      window.scrollTo(0, document.documentElement.scrollHeight);
      rounds += 1;
      await sleep(jittered(config.scrollDelayMs, config.delayJitter));
      const producedSomething = this.queue.length + (this.inFlight ? 1 : 0) > before;

      if (producedSomething) {
        this.stalledRounds = 0;
        continue;
      }
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
    this.postToWorker({ type: "report_done", navEpoch: this.assignment.navEpoch, reason });
  }

  private reportFailed(errorCode: ErrorCode, message: string): void {
    if (!this.assignment) return;
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
