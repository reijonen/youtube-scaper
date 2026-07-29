/**
 * The postMessage channel between capture.ts (MAIN world) and collector.ts
 * (isolated world). They are two separate JS execution contexts that
 * happen to share the same `window` object — this is the only channel
 * between them, so it's type-only shared state, not a runtime import.
 *
 * SPEC-V3: "The postMessage channel is inside the page and is therefore
 * not trustworthy against the page itself. No integrity is claimed for
 * it." collector.ts's only defence is `event.source === window`; anything
 * beyond that (schema validation, size caps) is the controller's job on
 * the payloads that ultimately reach it, same as any other untrusted input.
 */

export interface CapturedPayload {
  source: "initial" | "network";
  endpoint: string;
  capturedAt: string;
  body: unknown;
}

export type YtsWindowMessage =
  | { __yts: "collector-ready" }
  | { __yts: "captured-payload"; payload: CapturedPayload };
