/**
 * Message shapes, mirroring src/scraper/controller/protocol.py exactly.
 * Nothing here adds a message type or field that isn't in SPEC-V3's
 * "Messages" table.
 */

export const PROTOCOL_VERSION = 1;

export type ErrorCode =
  | "PAGE_READY_TIMEOUT"
  | "CONSENT_WALL"
  | "VIDEO_UNAVAILABLE"
  | "AGE_RESTRICTED"
  | "LOGIN_REQUIRED"
  | "SHORTS_EXCLUDED"
  | "UNEXPECTED_NAVIGATION"
  | "SCHEMA_UNRECOGNISED"
  | "CHAIN_STALLED"
  | "CIRCUIT_BREAKER"
  | "PAYLOAD_TOO_LARGE"
  | "CONTROLLER_UNAVAILABLE";

export type CompletionReason = "max_recommendations_reached" | "chain_exhausted";
export type PayloadSource = "initial" | "network";

// -- Extension -> controller --------------------------------------------

export interface HelloMessage {
  type: "hello";
  protocolVersion: number;
  restart: boolean;
}

export interface PayloadMessage {
  type: "payload";
  protocolVersion: number;
  runId: string;
  videoId: string;
  navEpoch: number;
  batchId: string;
  source: PayloadSource;
  endpoint: string;
  capturedAt: string;
  body: unknown;
}

export interface VideoDoneMessage {
  type: "video_done";
  protocolVersion: number;
  runId: string;
  videoId: string;
  navEpoch: number;
  reason: CompletionReason;
}

export interface VideoFailedMessage {
  type: "video_failed";
  protocolVersion: number;
  runId: string;
  videoId: string;
  navEpoch: number;
  errorCode: ErrorCode;
  message: string;
}

export interface PingMessage {
  type: "ping";
  protocolVersion: number;
  runId: string;
}

export type ExtensionMessage =
  | HelloMessage
  | PayloadMessage
  | VideoDoneMessage
  | VideoFailedMessage
  | PingMessage;

// -- Controller -> extension ---------------------------------------------

export interface CollectionConfig {
  maxRecommendations: number;
  scrollDelayMs: number;
  delayJitter: number;
  pageReadyTimeoutMs: number;
  ackTimeoutMs: number;
  maxScrollRounds: number;
  maxPageDurationMs: number;
  // When true, neither chain-exhaustion nor the recommendation cap ends
  // collection until the comments header's count is resolved (known to be
  // 0, or at least one thread has actually been captured) — otherwise a
  // low --max-recommendations (or a short recommendation chain) can end
  // the page before comments ever get a chance to load, since nothing
  // about the collector's own stopping conditions is comment-aware.
  // maxScrollRounds/maxPageDurationMs still bound this either way.
  waitForComments: boolean;
}

export interface HelloAckMessage {
  type: "hello_ack";
  protocolVersion: number;
  runId: string;
  videoId: string;
  videoUrl: string;
  config: CollectionConfig;
}

export interface PayloadAckMessage {
  type: "payload_ack";
  protocolVersion: number;
  batchId: string;
}

export interface PayloadNackMessage {
  type: "payload_nack";
  protocolVersion: number;
  batchId: string;
  retryable: boolean;
}

export interface VideoDoneAckMessage {
  type: "video_done_ack";
  protocolVersion: number;
  videoId: string;
}

export interface VideoFailedAckMessage {
  type: "video_failed_ack";
  protocolVersion: number;
  videoId: string;
}

export interface PongMessage {
  type: "pong";
  protocolVersion: number;
}

export interface StopMessage {
  type: "stop";
  protocolVersion: number;
}

/**
 * Synthesized locally by the native-host bridge when the controller socket
 * is unreachable (src/scraper/native_host.py, CONTROLLER_UNAVAILABLE_MESSAGE_TYPE).
 * Never sent by the controller itself — it can't be, by definition, when
 * this fires. Documented here because the service worker must recognise it
 * on the same native port as every other controller-originated message.
 */
export interface ControllerUnavailableMessage {
  type: "controller_unavailable";
  protocolVersion: number;
}

export type ControllerMessage =
  | HelloAckMessage
  | PayloadAckMessage
  | PayloadNackMessage
  | VideoDoneAckMessage
  | VideoFailedAckMessage
  | PongMessage
  | StopMessage
  | ControllerUnavailableMessage;

export function buildHello(restart: boolean): HelloMessage {
  return { type: "hello", protocolVersion: PROTOCOL_VERSION, restart };
}

export function buildPayload(fields: Omit<PayloadMessage, "type" | "protocolVersion">): PayloadMessage {
  return { type: "payload", protocolVersion: PROTOCOL_VERSION, ...fields };
}

export function buildVideoDone(
  fields: Omit<VideoDoneMessage, "type" | "protocolVersion">,
): VideoDoneMessage {
  return { type: "video_done", protocolVersion: PROTOCOL_VERSION, ...fields };
}

export function buildVideoFailed(
  fields: Omit<VideoFailedMessage, "type" | "protocolVersion">,
): VideoFailedMessage {
  return { type: "video_failed", protocolVersion: PROTOCOL_VERSION, ...fields };
}

export function buildPing(runId: string): PingMessage {
  return { type: "ping", protocolVersion: PROTOCOL_VERSION, runId };
}
