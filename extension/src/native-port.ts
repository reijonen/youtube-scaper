/**
 * Owns the single persistent connectNative() connection to the bridge
 * (SPEC-V3, "Connection topology": "one persistent extension <-> native-
 * host connection... A new native host is never opened per message.
 * Reconnection uses bounded exponential backoff.").
 */

import { PROTOCOL_VERSION, type ControllerMessage, type ExtensionMessage } from "./protocol";

const NATIVE_HOST_NAME = "com.sor.yts";
const BASE_DELAY_MS = 250;
const MAX_DELAY_MS = 10_000;

export class NativePort {
  private port: chrome.runtime.Port | null = null;
  private attempt = 0;
  private stopped = false;

  constructor(
    private readonly onMessage: (msg: ControllerMessage) => void,
    private readonly onDisconnected: () => void,
  ) {}

  connect(): void {
    this.stopped = false;
    this.attempt = 0;
    this.tryConnect();
  }

  stop(): void {
    this.stopped = true;
    this.port?.disconnect();
    this.port = null;
  }

  get isConnected(): boolean {
    return this.port !== null;
  }

  send(msg: ExtensionMessage): void {
    if (!this.port) {
      throw new Error("native port is not connected");
    }
    this.port.postMessage(msg);
  }

  private tryConnect(): void {
    if (this.stopped) return;
    try {
      this.port = chrome.runtime.connectNative(NATIVE_HOST_NAME);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.port.onMessage.addListener((raw: unknown) => {
      const msg = raw as ControllerMessage;
      if (msg.protocolVersion !== PROTOCOL_VERSION) return;
      this.attempt = 0;
      this.onMessage(msg);
    });

    this.port.onDisconnect.addListener(() => {
      this.port = null;
      this.onDisconnected();
      this.scheduleReconnect();
    });
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    const delay = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** this.attempt);
    this.attempt += 1;
    setTimeout(() => this.tryConnect(), delay);
  }
}
