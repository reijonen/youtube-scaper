/**
 * Durable state, per SPEC-V3 "Service-worker lifetime": "worker tab ID,
 * current video ID, current navigation epoch, pending acknowledgement
 * identifiers, and current navigation state." Kept in chrome.storage.session
 * rather than service-worker globals — the service worker itself is
 * expected to restart at any time (the ~30s MV3 idle timeout is easy to
 * hit during a page load or a scroll-wait), and on restart it must
 * rehydrate from here and re-announce before doing anything else. The
 * controller and SQLite remain the authoritative source of truth; this is
 * just enough to resume without re-navigating or losing an in-flight batch.
 */

import type { CapturedPayload } from "./capture-protocol";
import type { CollectionConfig } from "./protocol";

const STORAGE_KEY = "yts_session_state";

export interface PendingBatch {
  batchId: string;
  navEpoch: number;
  payload: CapturedPayload;
}

export interface SessionState {
  workerTabId: number | null;
  runId: string | null;
  videoId: string | null;
  videoUrl: string | null;
  navEpoch: number;
  config: CollectionConfig | null;
  /** True once this run has connected to the controller at least once —
   * the next `hello` after a restart announces restart:true. */
  everConnected: boolean;
  paused: boolean;
  pendingBatch: PendingBatch | null;
}

export const INITIAL_STATE: SessionState = {
  workerTabId: null,
  runId: null,
  videoId: null,
  videoUrl: null,
  navEpoch: 0,
  config: null,
  everConnected: false,
  paused: false,
  pendingBatch: null,
};

export async function loadState(): Promise<SessionState> {
  const stored = await chrome.storage.session.get(STORAGE_KEY);
  const state = stored[STORAGE_KEY] as SessionState | undefined;
  return state ?? { ...INITIAL_STATE };
}

export async function saveState(state: SessionState): Promise<void> {
  await chrome.storage.session.set({ [STORAGE_KEY]: state });
}

export async function updateState(
  patch: Partial<SessionState>,
): Promise<SessionState> {
  const current = await loadState();
  const next = { ...current, ...patch };
  await saveState(next);
  return next;
}
