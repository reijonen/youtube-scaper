/**
 * The chrome.runtime.Port protocol between collector.ts (isolated world,
 * one per navigation) and service-worker.ts (one per Chrome session). Not
 * part of SPEC-V3's wire protocol — that's protocol.ts, controller <->
 * extension. This is purely internal, so its shape is a free design choice
 * (see DECISIONS.md, Phase 6) rather than something SPEC-V3 pins down.
 *
 * A long-lived Port (chrome.runtime.connect), not one-off sendMessage
 * calls, because the service worker must be able to push to the collector
 * unprompted — a `stop` triggered by the controller reaching the
 * recommendation cap, or a `pause` on native-messaging disconnect — not
 * only reply to collector-initiated requests.
 *
 * Every collector-originated message after `assign_request` carries the
 * navEpoch handed back in `assign`, so the service worker can hard-drop
 * anything from a superseded navigation (SPEC-V3, "Navigation model").
 */

import type { CapturedPayload } from "./capture-protocol";
import type { CollectionConfig, CompletionReason, ErrorCode } from "./protocol";

export const PORT_NAME = "yts-collector";

// -- Collector -> service worker ------------------------------------------

export interface AssignRequestMessage {
  type: "assign_request";
}

export interface ForwardPayloadMessage {
  type: "forward_payload";
  navEpoch: number;
  batchId: string;
  payload: CapturedPayload;
}

export interface ReportDoneMessage {
  type: "report_done";
  navEpoch: number;
  reason: CompletionReason;
}

export interface ReportFailedMessage {
  type: "report_failed";
  navEpoch: number;
  errorCode: ErrorCode;
  message: string;
}

export type CollectorToWorkerMessage =
  | AssignRequestMessage
  | ForwardPayloadMessage
  | ReportDoneMessage
  | ReportFailedMessage;

// -- Service worker -> collector ------------------------------------------

export interface AssignMessage {
  type: "assign";
  runId: string;
  videoId: string;
  videoUrl: string;
  navEpoch: number;
  config: CollectionConfig;
}

export interface PayloadAckMessage {
  type: "payload_ack";
  batchId: string;
}

export interface PayloadNackMessage {
  type: "payload_nack";
  batchId: string;
  retryable: boolean;
}

export interface DoneAckMessage {
  type: "done_ack";
}

export interface FailedAckMessage {
  type: "failed_ack";
}

/** Cap reached mid-video (controller-authoritative — see DECISIONS.md), or
 * the run is otherwise concluding this video. Received before the
 * collector has itself reported completion, it means "stop scrolling and
 * call reportDone('max_recommendations_reached')"; received after, it's
 * just the ordinary end-of-session signal to stop listening. */
export interface StopMessage {
  type: "stop";
}

/** Native-messaging connection is down (bridge reconnecting, or the
 * controller is unreachable). SPEC-V3: "On native-messaging disconnect:
 * scrolling stops... collection resumes only after reconnecting." */
export interface PauseMessage {
  type: "pause";
}

export interface ResumeMessage {
  type: "resume";
}

export type WorkerToCollectorMessage =
  | AssignMessage
  | PayloadAckMessage
  | PayloadNackMessage
  | DoneAckMessage
  | FailedAckMessage
  | StopMessage
  | PauseMessage
  | ResumeMessage;
