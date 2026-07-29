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

// ---------------------------------------------------------------- native port

async function reportFailure(errorCode: ErrorCode, message: string): Promise<void> {
  const state = await loadState();
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
      const next = await updateState({
        runId: msg.runId,
        videoId: msg.videoId,
        videoUrl: msg.videoUrl,
        config: msg.config,
        everConnected: true,
      });
      await navigateToVideo(next);
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

let expectingNavigationToken: string | null = null;

async function ensureWorkerTab(): Promise<number> {
  const state = await loadState();
  if (state.workerTabId !== null) {
    try {
      await chrome.tabs.get(state.workerTabId);
      return state.workerTabId;
    } catch {
      // Tab no longer exists (closed externally) — fall through and create
      // a fresh one.
    }
  }
  const tab = await chrome.tabs.create({ active: false, url: "about:blank" });
  const tabId = tab.id;
  if (tabId === undefined) throw new Error("chrome.tabs.create returned no tab id");
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
  expectingNavigationToken = state.videoUrl;
  await chrome.tabs.update(tabId, { url: state.videoUrl });
}

chrome.webNavigation.onCommitted.addListener((details) => {
  void (async () => {
    const state = await loadState();
    if (details.frameId !== 0 || details.tabId !== state.workerTabId) return;

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

    const initiated = expectingNavigationToken !== null;
    expectingNavigationToken = null;
    if (!initiated || !isAllowedYouTubeUrl(details.url)) {
      await reportFailure(
        "UNEXPECTED_NAVIGATION",
        `top-level navigation the service worker did not initiate: ${details.url}`,
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

  void (async () => {
    const state = await loadState();
    if (port.sender?.tab?.id !== state.workerTabId) {
      // Not the dedicated worker tab — SPEC-V3 requires every collector
      // message to be verified as originating from it.
      port.disconnect();
      return;
    }

    collectorPort = port;
    port.onMessage.addListener((raw: unknown) => {
      void handleCollectorMessage(raw as CollectorToWorkerMessage);
    });
    port.onDisconnect.addListener(() => {
      if (collectorPort === port) collectorPort = null;
    });
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

async function start(): Promise<void> {
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
