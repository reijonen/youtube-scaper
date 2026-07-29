/**
 * Service worker (SPEC-V3, "Service-worker responsibilities"). Owns the
 * native-host connection, the worker tab, the current video assignment,
 * navigation, routing between the collector script and the native host,
 * and heartbeating. See session-state.ts for why almost nothing here lives
 * in a plain module-level variable.
 *
 * Detection scope, honestly stated (see DECISIONS.md, Phase 6): only
 * PAGE_READY_TIMEOUT (collector-side deadline), UNEXPECTED_NAVIGATION
 * (host/path check below), and SHORTS_EXCLUDED (path check) are wired up
 * here. CONSENT_WALL, AGE_RESTRICTED, LOGIN_REQUIRED, and VIDEO_UNAVAILABLE
 * would need a verified `playabilityStatus` value from a real capture of
 * each state — none exist in gates/captures/, and PLAN.md's ground rules
 * forbid guessing YouTube's payload shape. If one of those states is hit in
 * practice it still fails loudly, just under PAGE_READY_TIMEOUT or
 * SCHEMA_UNRECOGNISED rather than its own specific code.
 */

import type { ControllerMessage, ErrorCode } from "./protocol";
import { buildHello, buildPing, buildVideoDone, buildVideoFailed } from "./protocol";
import { NativePort } from "./native-port";
import { loadState, updateState, type SessionState } from "./session-state";
import { PORT_NAME, type CollectorToWorkerMessage, type WorkerToCollectorMessage } from "./internal-protocol";

const HEARTBEAT_ALARM = "yts-heartbeat";
// Below the 30s floor Chrome enforces for *packed* extensions (Chrome 120+);
// unpacked extensions (how this is always loaded, per SPEC-V3) aren't held
// to that floor. Chosen to sit safely under the ~30s MV3 idle timeout.
const HEARTBEAT_PERIOD_MINUTES = 0.4;

let collectorPort: chrome.runtime.Port | null = null;

// SPEC-V3, "Logging": "Extension logs carry the run ID and the current
// video ID." state is read fresh each call rather than threaded through
// every call site, since this is a debug aid, not a hot path.
function log(message: string, ...rest: unknown[]): void {
  void loadState().then((state) => {
    console.log(`[yts] run=${state.runId ?? "-"} video=${state.videoId ?? "-"} ${message}`, ...rest);
  });
}

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

// The cookie-consent extension the golden template is set up with ("I still
// don't care about cookies" — see gates/README.md, Gate A) dismisses
// YouTube's consent wall automatically, which routes through a genuine
// top-level redirect to a consent host before landing back on the video.
// That host is never youtube.com, so without this it would trip
// UNEXPECTED_NAVIGATION on every cookie-less profile — which is the normal
// case here, since data-dir starts from a clean template every video.
function isTransitionalConsentUrl(url: URL): boolean {
  return (
    url.hostname === "consent.youtube.com" ||
    url.hostname === "consent.google.com" ||
    url.hostname.endsWith(".consent.google.com")
  );
}

// ---------------------------------------------------------------- native port

async function reportFailure(errorCode: ErrorCode, message: string): Promise<void> {
  const state = await loadState();
  log(`reportFailure ${errorCode}: ${message}`);
  if (!state.runId || !state.videoId) return;
  nativePort.send(
    buildVideoFailed({
      runId: state.runId,
      videoId: state.videoId,
      navEpoch: state.navEpoch,
      errorCode,
      message,
    }),
  );
}

async function handleControllerMessage(msg: ControllerMessage): Promise<void> {
  const state = await loadState();

  switch (msg.type) {
    case "hello_ack": {
      // Only navigate for a genuinely new video assignment. A restart's
      // hello_ack hands back the *same* video (one VideoSession per socket
      // connection lifetime — SPEC-V3, "One video is collected per browser
      // session"), and re-navigating on every reconnect would restart page
      // load in a loop each time the ~30s MV3 idle timeout fires, which is
      // often before a real watch page has finished loading and captured
      // anything. SPEC-V3, "Service-worker lifetime": a restart
      // "re-announces its state to the controller before doing anything
      // else" — not "re-navigates".
      const isNewVideo = state.videoId !== msg.videoId;
      const next = await updateState({
        runId: msg.runId,
        videoId: msg.videoId,
        videoUrl: msg.videoUrl,
        config: msg.config,
        everConnected: true,
      });
      log(`hello_ack videoUrl=${msg.videoUrl} isNewVideo=${isNewVideo}`);
      if (isNewVideo) {
        await navigateToVideo(next);
      }
      return;
    }
    case "payload_ack":
      await updateState({ pendingBatch: null });
      postToCollector({ type: "payload_ack", batchId: msg.batchId });
      return;
    case "payload_nack":
      await updateState({ pendingBatch: null });
      postToCollector({ type: "payload_nack", batchId: msg.batchId, retryable: msg.retryable });
      return;
    case "video_done_ack":
      postToCollector({ type: "done_ack" });
      return;
    case "video_failed_ack":
      postToCollector({ type: "failed_ack" });
      return;
    case "pong":
      return;
    case "stop":
      postToCollector({ type: "stop" });
      return;
    case "controller_unavailable":
      await updateState({ paused: true });
      postToCollector({ type: "pause" });
      return;
  }
}

async function handleNativeDisconnected(): Promise<void> {
  await updateState({ paused: true });
  postToCollector({ type: "pause" });
}

const nativePort = new NativePort(
  (msg) => void handleControllerMessage(msg),
  () => void handleNativeDisconnected(),
);

async function announceHello(): Promise<void> {
  const state = await loadState();
  nativePort.send(buildHello(state.everConnected));
  await resendPendingBatch(state);
}

async function resendPendingBatch(state: SessionState): Promise<void> {
  // A batch the collector sent but never got an ack for (connection dropped
  // mid-flight, or the service worker itself restarted) is retried here
  // rather than by the collector re-sending it — the collector only knows
  // about its own current navigation; this survives across restarts because
  // it's read from chrome.storage.session, not a module-level variable.
  if (!state.pendingBatch || !state.runId || !state.videoId) return;
  nativePort.send({
    type: "payload",
    protocolVersion: 1,
    runId: state.runId,
    videoId: state.videoId,
    navEpoch: state.pendingBatch.navEpoch,
    batchId: state.pendingBatch.batchId,
    source: state.pendingBatch.payload.source,
    endpoint: state.pendingBatch.payload.endpoint,
    capturedAt: state.pendingBatch.payload.capturedAt,
    body: state.pendingBatch.payload.body,
  });
}

// ---------------------------------------------------------------- worker tab / navigation

async function ensureWorkerTab(): Promise<number> {
  const state = await loadState();
  if (state.workerTabId !== null) {
    try {
      await chrome.tabs.get(state.workerTabId);
      return state.workerTabId;
    } catch {
      // Tab no longer exists (closed externally) — fall through and adopt
      // or create a fresh one.
    }
  }

  // This is a disposable, single-purpose profile — nothing else is ever
  // open in it — so the tab Chrome opens on launch (its default New Tab
  // Page) is adopted as the worker tab rather than leaving it idle and
  // opening a second one. SPEC-V3, acceptance criterion 6: "The extension
  // uses one tab."
  const [existing] = await chrome.tabs.query({});
  const tabId =
    existing?.id ?? (await chrome.tabs.create({ active: false, url: "about:blank" })).id;
  if (tabId === undefined) throw new Error("no worker tab id available");
  await updateState({ workerTabId: tabId });
  return tabId;
}

async function navigateToVideo(state: SessionState): Promise<void> {
  if (!state.videoUrl || !isAllowedYouTubeUrl(state.videoUrl)) {
    await reportFailure("UNEXPECTED_NAVIGATION", `refusing to navigate to disallowed URL`);
    return;
  }

  const tabId = await ensureWorkerTab();
  const nextEpoch = state.navEpoch + 1;
  await updateState({ navEpoch: nextEpoch, pendingBatch: null });
  log(`navigating tab ${tabId} to ${state.videoUrl} (epoch ${nextEpoch})`);
  await chrome.tabs.update(tabId, { url: state.videoUrl });
}

// A one-shot "did we initiate this" token turned out to be too strict:
// YouTube itself issues a same-video top-level redirect right after landing
// on a watch page (observed live: `?v=<id>` -> `?v=<id>&themeRefresh=1`),
// which produces a *second* onCommitted event for a navigation the worker
// never explicitly requested. What actually matters isn't "did we ask for
// this exact commit" but "did we end up somewhere other than the video we
// were assigned" — so this compares the committed URL's `v` param against
// the assigned videoId instead, which tolerates YouTube's own redirects on
// the same video while still catching a real navigate-away.
chrome.webNavigation.onCommitted.addListener((details) => {
  void (async () => {
    const state = await loadState();
    if (details.frameId !== 0 || details.tabId !== state.workerTabId) return;
    if (state.videoId === null) return; // nothing assigned yet (e.g. initial about:blank tab)

    let committedUrl: URL;
    try {
      committedUrl = new URL(details.url);
    } catch {
      await reportFailure("UNEXPECTED_NAVIGATION", `unparseable URL: ${details.url}`);
      return;
    }

    if (committedUrl.pathname.startsWith("/shorts/")) {
      await reportFailure("SHORTS_EXCLUDED", `resolved to a Shorts path: ${details.url}`);
      return;
    }

    if (isTransitionalConsentUrl(committedUrl)) {
      log(`transitional consent redirect, waiting for the real page: ${details.url}`);
      return;
    }

    if (!isAllowedYouTubeUrl(details.url)) {
      await reportFailure("UNEXPECTED_NAVIGATION", `left youtube.com: ${details.url}`);
      return;
    }

    const committedVideoId = committedUrl.searchParams.get("v");
    if (committedVideoId === null) {
      // A youtube.com page with no video in the URL — e.g. an interstitial
      // partway through the consent flow above. Not necessarily wrong; wait
      // for the next commit rather than failing immediately. If nothing
      // sensible ever follows, PAGE_READY_TIMEOUT (collector-side) and the
      // controller's own backstop deadline still bound how long this waits.
      log(`transitional navigation with no video id, waiting: ${details.url}`);
      return;
    }

    if (committedVideoId !== state.videoId) {
      await reportFailure(
        "UNEXPECTED_NAVIGATION",
        `top-level navigation left the assigned video: ${details.url}`,
      );
    }
  })();
});

// ---------------------------------------------------------------- collector port

function postToCollector(msg: WorkerToCollectorMessage): void {
  collectorPort?.postMessage(msg);
}

async function handleCollectorMessage(
  msg: CollectorToWorkerMessage,
): Promise<void> {
  const state = await loadState();

  if (msg.type === "assign_request") {
    if (!state.runId || !state.videoId || !state.videoUrl || !state.config) return;
    postToCollector({
      type: "assign",
      runId: state.runId,
      videoId: state.videoId,
      videoUrl: state.videoUrl,
      navEpoch: state.navEpoch,
      config: state.config,
    });
    return;
  }

  // Everything else is stamped with the epoch the collector was assigned;
  // a stale one means this message came from an abandoned page load.
  if (msg.navEpoch !== state.navEpoch) return;

  if (msg.type === "forward_payload") {
    if (!state.runId || !state.videoId) return;
    log(`forward_payload batch=${msg.batchId} endpoint=${msg.payload.endpoint}`);
    await updateState({
      pendingBatch: { batchId: msg.batchId, navEpoch: msg.navEpoch, payload: msg.payload },
    });
    nativePort.send({
      type: "payload",
      protocolVersion: 1,
      runId: state.runId,
      videoId: state.videoId,
      navEpoch: msg.navEpoch,
      batchId: msg.batchId,
      source: msg.payload.source,
      endpoint: msg.payload.endpoint,
      capturedAt: msg.payload.capturedAt,
      body: msg.payload.body,
    });
    return;
  }

  if (msg.type === "report_done") {
    if (!state.runId || !state.videoId) return;
    log(`report_done reason=${msg.reason}`);
    nativePort.send(
      buildVideoDone({
        runId: state.runId,
        videoId: state.videoId,
        navEpoch: msg.navEpoch,
        reason: msg.reason,
      }),
    );
    return;
  }

  if (msg.type === "report_failed") {
    await reportFailure(msg.errorCode, msg.message);
  }
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== PORT_NAME) return;

  // The collector sends its first message (assign_request) synchronously
  // right after connecting. Validating the sender with `await loadState()`
  // before registering the message listener left a real gap: a message
  // that arrived while that await was pending had nothing listening for it
  // and was silently dropped (observed live — the collector connected but
  // never received an assignment). The listener is now registered in the
  // same synchronous tick as onConnect firing, so nothing can be missed;
  // messages that arrive before validation finishes are queued and
  // replayed once it completes.
  const pendingMessages: unknown[] = [];
  let validated = false;
  let rejected = false;

  port.onMessage.addListener((raw: unknown) => {
    if (rejected) return;
    if (!validated) {
      pendingMessages.push(raw);
      return;
    }
    void handleCollectorMessage(raw as CollectorToWorkerMessage);
  });
  port.onDisconnect.addListener(() => {
    if (collectorPort === port) collectorPort = null;
  });

  void (async () => {
    const state = await loadState();
    if (port.sender?.tab?.id !== state.workerTabId) {
      // Not the dedicated worker tab — SPEC-V3 requires every collector
      // message to be verified as originating from it.
      rejected = true;
      port.disconnect();
      return;
    }

    collectorPort = port;
    log("collector connected");
    validated = true;
    for (const raw of pendingMessages.splice(0)) {
      void handleCollectorMessage(raw as CollectorToWorkerMessage);
    }
  })();
});

// ---------------------------------------------------------------- heartbeat

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== HEARTBEAT_ALARM) return;
  void (async () => {
    const state = await loadState();
    if (!state.runId || !nativePort.isConnected) return;
    nativePort.send(buildPing(state.runId));
  })();
});

function ensureHeartbeat(): void {
  chrome.alarms.get(HEARTBEAT_ALARM, (existing) => {
    if (existing) return;
    chrome.alarms.create(HEARTBEAT_ALARM, { periodInMinutes: HEARTBEAT_PERIOD_MINUTES });
  });
}

// ---------------------------------------------------------------- startup

// start() is reached from three places for the same module evaluation: the
// unconditional call below (needed because idle-timeout restarts fire
// neither Chrome event), plus onInstalled and onStartup, which both fire
// for real on a genuine Chrome launch — i.e. every video, since a fresh
// Chrome process is launched each time. Without this guard, a single
// module evaluation called start() twice, and NativePort.connect() has no
// reentrancy guard of its own: the second call silently overwrote `port`
// without disconnecting the first, leaving two live bridge connections and
// two hello_ack-triggered navigateToVideo() calls racing each other. That
// produced exactly the erratic extra-reload behaviour observed live —
// not YouTube- or network-timing flakiness, a real bug in our own startup
// path. See DECISIONS.md, Phase 7.
let started = false;

async function start(): Promise<void> {
  if (started) return;
  started = true;
  log("service worker starting");
  ensureHeartbeat();
  nativePort.connect();
  await announceHello();
}

chrome.runtime.onInstalled.addListener(() => void start());
chrome.runtime.onStartup.addListener(() => void start());
// The service worker script itself re-runs its top level on every wake,
// including MV3's routine idle-timeout restarts that fire neither of the
// events above — so startup also happens unconditionally here.
void start();
