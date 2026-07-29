// MAIN-world capture probe.
//
// Throwaway diagnostic, not the real collector. It deliberately exposes a global
// (window.__ytProbe) so a human can inspect and export results from DevTools.
// The real extension must not do that.

(() => {
  "use strict";

  const store = {
    startedAt: Date.now(),
    pageUrl: location.href,
    initialData: null, // deep snapshot taken at assignment
    initialDataFinal: null, // filled at export from the live object, for comparison
    initialDataCapturedAt: null,
    responses: [],
    errors: [],
  };

  let seq = 0;
  const live = {}; // live references, kept only so export can diff against the snapshot

  const note = (label, extra) =>
    console.log(`%c[ytProbe] ${label}`, "color:#0a0;font-weight:bold", extra ?? "");

  // YouTube mutates ytInitialData in place as the page paginates, so a held reference
  // ends up holding the accumulated end state. Everything must be deep-copied at the
  // moment of assignment. structuredClone is preferred; JSON round-trip is the fallback
  // for objects carrying functions (ytcfg does).
  const snapshot = (v) => {
    try {
      return structuredClone(v);
    } catch {
      try {
        return JSON.parse(JSON.stringify(v));
      } catch (e) {
        store.errors.push({ what: "snapshot", error: String(e) });
        return null;
      }
    }
  };

  // ---------------------------------------------------------------- initial data
  // Install an accessor before YouTube's inline script assigns the global, so the
  // value is captured at the moment of assignment rather than polled for.
  for (const name of ["ytInitialData", "ytInitialPlayerResponse", "ytcfg"]) {
    let held;
    try {
      Object.defineProperty(window, name, {
        configurable: true,
        enumerable: true,
        get: () => held,
        set: (v) => {
          held = v;
          live[name] = v;
          if (name === "ytInitialData" && store.initialData === null) {
            const t0 = performance.now();
            store.initialData = snapshot(v);
            store.initialDataCapturedAt = Date.now();
            note(
              "snapshotted ytInitialData",
              `${(performance.now() - t0).toFixed(1)} ms`
            );
          }
          if (name === "ytInitialPlayerResponse" && !store.playerResponse) {
            store.playerResponse = snapshot(v);
          }
          if (name === "ytcfg" && !store.ytcfg) store.ytcfg = snapshot(v);
        },
      });
    } catch (e) {
      store.errors.push({ what: `defineProperty ${name}`, error: String(e) });
    }
  }

  // ---------------------------------------------------------------- interception
  const INTERESTING = /\/youtubei\/v1\//;

  const record = (url, method, bodyText, via) => {
    let parsed = null;
    try {
      parsed = JSON.parse(bodyText);
    } catch (e) {
      store.errors.push({ what: `parse ${url}`, error: String(e) });
      return;
    }
    const entry = {
      seq: seq++,
      at: Date.now(),
      via,
      method,
      url,
      endpoint: (() => {
        try {
          return new URL(url, location.origin).pathname;
        } catch {
          return url;
        }
      })(),
      bytes: bodyText.length,
      body: parsed,
    };
    store.responses.push(entry);
    note(`${via} ${entry.endpoint} #${entry.seq}`, `${(entry.bytes / 1024) | 0} KiB`);
  };

  const origFetch = window.fetch;
  window.fetch = function (...args) {
    const p = origFetch.apply(this, args);
    try {
      const url = typeof args[0] === "string" ? args[0] : args[0]?.url ?? "";
      if (INTERESTING.test(url)) {
        const method =
          (typeof args[0] === "object" && args[0]?.method) || args[1]?.method || "GET";
        // Always read from a clone; never consume the body the page needs.
        p.then((res) => res.clone().text())
          .then((t) => record(url, method, t, "fetch"))
          .catch((e) => store.errors.push({ what: `fetch ${url}`, error: String(e) }));
      }
    } catch (e) {
      store.errors.push({ what: "fetch wrapper", error: String(e) });
    }
    return p;
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__ytProbeUrl = url;
    this.__ytProbeMethod = method;
    return origOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function (...args) {
    try {
      if (INTERESTING.test(this.__ytProbeUrl || "")) {
        this.addEventListener("load", () => {
          try {
            record(this.__ytProbeUrl, this.__ytProbeMethod, this.responseText, "xhr");
          } catch (e) {
            store.errors.push({ what: "xhr load", error: String(e) });
          }
        });
      }
    } catch (e) {
      store.errors.push({ what: "xhr wrapper", error: String(e) });
    }
    return origSend.apply(this, args);
  };

  // ---------------------------------------------------------------- inspection API
  const walk = (node, depth, out, path) => {
    if (depth > 12 || node === null || typeof node !== "object") return;
    if (Array.isArray(node)) {
      node.slice(0, 3).forEach((v) => walk(v, depth + 1, out, `${path}[]`));
      return;
    }
    for (const k of Object.keys(node)) {
      if (k.endsWith("Renderer") || k.endsWith("Continuation")) {
        out[k] = (out[k] || 0) + 1;
      }
      walk(node[k], depth + 1, out, `${path}.${k}`);
    }
  };

  window.__ytProbe = {
    get raw() {
      return store;
    },
    /** Count every *Renderer / *Continuation key seen, across all captures. */
    shapes() {
      const out = {};
      walk(store.initialData, 0, out, "initialData");
      store.responses.forEach((r) => walk(r.body, 0, out, `resp${r.seq}`));
      return Object.fromEntries(Object.entries(out).sort((a, b) => b[1] - a[1]));
    },
    /**
     * Proof that the snapshot worked. `snapshot` must stay at the page's initial item
     * count while `liveNow` grows as continuations arrive. If they match after
     * scrolling, the snapshot did not take and the capture is contaminated.
     */
    verify() {
      const count = (node, key, depth = 0) => {
        if (depth > 30 || node === null || typeof node !== "object") return 0;
        if (Array.isArray(node))
          return node.reduce((a, v) => a + count(v, key, depth + 1), 0);
        return Object.entries(node).reduce(
          (a, [k, v]) => a + (k === key ? 1 : 0) + count(v, key, depth + 1),
          0
        );
      };
      const wire = store.responses.reduce(
        (a, r) => a + count(r.body, "lockupViewModel"),
        0
      );
      return {
        lockups: {
          snapshot: count(store.initialData, "lockupViewModel"),
          liveNow: count(live.ytInitialData, "lockupViewModel"),
          fromWire: wire,
        },
        commentThreads: {
          snapshot: count(store.initialData, "commentThreadRenderer"),
          liveNow: count(live.ytInitialData, "commentThreadRenderer"),
          fromWire: store.responses.reduce(
            (a, r) => a + count(r.body, "commentThreadRenderer"),
            0
          ),
        },
      };
    },
    summary() {
      return {
        pageUrl: store.pageUrl,
        elapsedMs: Date.now() - store.startedAt,
        gotInitialData: store.initialData !== null,
        responseCount: store.responses.length,
        byEndpoint: store.responses.reduce((a, r) => {
          a[r.endpoint] = (a[r.endpoint] || 0) + 1;
          return a;
        }, {}),
        totalBytes: store.responses.reduce((a, r) => a + r.bytes, 0),
        largestBytes: Math.max(0, ...store.responses.map((r) => r.bytes)),
        errors: store.errors,
      };
    },
    /** Download everything captured so far as one JSON file. */
    save(filename) {
      // Serialise the live object too, so the file itself carries the proof that the
      // snapshot diverged from the mutated original.
      store.initialDataFinal = snapshot(live.ytInitialData);
      store.verification = window.__ytProbe.verify();
      const blob = new Blob([JSON.stringify(store)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download =
        filename ||
        `ytprobe-${new URLSearchParams(location.search).get("v") || "page"}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
      return "saved";
    },
  };

  note("capture installed at document_start", location.href);
})();
