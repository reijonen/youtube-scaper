/**
 * MAIN-world capture script (SPEC-V3, "Capture script"). Runs before any
 * YouTube script, in the page's own JS world, so it can see and wrap the
 * page's own globals. It does three things and nothing else: snapshot the
 * initial payloads at assignment, wrap fetch/XHR to observe
 * /youtubei/v1/next responses, and forward everything to the collector
 * script over postMessage. No page-visible globals, no network requests of
 * its own, no state beyond an internal buffer.
 *
 * Ported from gates/probe-extension/capture.js, with window.__ytProbe and
 * the diagnostic helpers (shapes/verify/summary/save) removed.
 */

import type { CapturedPayload, YtsWindowMessage } from "./capture-protocol";

(() => {
  "use strict";

  const NEXT_ENDPOINT_PATTERN = /\/youtubei\/v1\/next(?:[/?]|$)/;

  let collectorReady = false;
  const buffer: CapturedPayload[] = [];

  // YouTube mutates ytInitialData in place as the page paginates (verified
  // in the gate captures, not theoretical) — a held reference ends up
  // holding the accumulated end state, not the value at assignment. Every
  // capture must be a deep copy taken at the moment of assignment.
  // structuredClone is preferred; JSON round-trip is the fallback for
  // objects carrying functions.
  function snapshot(value: unknown): unknown {
    try {
      return structuredClone(value);
    } catch {
      try {
        return JSON.parse(JSON.stringify(value));
      } catch {
        return null;
      }
    }
  }

  function emit(payload: CapturedPayload): void {
    if (!collectorReady) {
      buffer.push(payload);
      return;
    }
    send(payload);
  }

  function send(payload: CapturedPayload): void {
    const message: YtsWindowMessage = { __yts: "captured-payload", payload };
    window.postMessage(message, window.location.origin);
  }

  // ---------------------------------------------------------------- initial data
  // Install accessors before YouTube's inline script assigns the globals,
  // so each value is captured at the moment of assignment rather than
  // polled for. document_start in the MAIN world is what makes this land
  // before that inline script runs.
  const INITIAL_GLOBALS: Record<string, string> = {
    ytInitialData: "ytInitialData",
    ytInitialPlayerResponse: "ytInitialPlayerResponse",
  };

  for (const [globalName, endpoint] of Object.entries(INITIAL_GLOBALS)) {
    let held: unknown;
    let captured = false;
    try {
      Object.defineProperty(window, globalName, {
        configurable: true,
        enumerable: true,
        get: () => held,
        set: (value: unknown) => {
          held = value;
          if (!captured) {
            captured = true;
            emit({
              source: "initial",
              endpoint,
              capturedAt: new Date().toISOString(),
              body: snapshot(value),
            });
          }
        },
      });
    } catch {
      // If YouTube ever defines the property as non-configurable first,
      // there is nothing more to do here — silent, not a page-visible
      // failure, and the missing initial payload becomes the controller's
      // PAGE_READY_TIMEOUT / SCHEMA_UNRECOGNISED signal downstream.
    }
  }

  // ---------------------------------------------------------------- interception
  function recordNetworkResponse(url: string, bodyText: string): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(bodyText);
    } catch {
      return;
    }
    emit({
      source: "network",
      endpoint: "/youtubei/v1/next",
      capturedAt: new Date().toISOString(),
      body: parsed,
    });
    void url;
  }

  const originalFetch = window.fetch;
  window.fetch = function (this: typeof window, ...args: Parameters<typeof fetch>) {
    const result = originalFetch.apply(this, args);
    try {
      const input = args[0];
      const url = typeof input === "string" ? input : (input as Request)?.url ?? "";
      if (NEXT_ENDPOINT_PATTERN.test(url)) {
        // Always read from a clone; the original body stream is never
        // consumed, so page behaviour is unchanged.
        result
          .then((response) => response.clone().text())
          .then((text) => recordNetworkResponse(url, text))
          .catch(() => {});
      }
    } catch {
      // Never let capture-wrapper failures affect the page's own fetch call.
    }
    return result;
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  const urlSymbol = Symbol("ytsCaptureUrl");

  XMLHttpRequest.prototype.open = function (
    this: XMLHttpRequest & { [urlSymbol]?: string },
    method: string,
    url: string | URL,
    ...rest: unknown[]
  ) {
    this[urlSymbol] = String(url);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return (originalOpen as any).call(this, method, url, ...rest);
  };

  XMLHttpRequest.prototype.send = function (
    this: XMLHttpRequest & { [urlSymbol]?: string },
    ...args: unknown[]
  ) {
    try {
      const url = this[urlSymbol] ?? "";
      if (NEXT_ENDPOINT_PATTERN.test(url)) {
        this.addEventListener("load", () => {
          try {
            recordNetworkResponse(url, this.responseText);
          } catch {
            // Same policy as above: never surface a capture failure to the page.
          }
        });
      }
    } catch {
      // ditto
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return (originalSend as any).apply(this, args);
  };

  // ---------------------------------------------------------------- readiness
  // The collector script (isolated world) signals readiness once it has
  // completed its own handshake with the service worker. Nothing captured
  // before that point may be dropped — it is buffered and flushed here.
  window.addEventListener("message", (event: MessageEvent) => {
    if (event.source !== window) return;
    const data = event.data as YtsWindowMessage | undefined;
    if (data?.__yts !== "collector-ready" || collectorReady) return;

    collectorReady = true;
    for (const payload of buffer) send(payload);
    buffer.length = 0;
  });
})();
