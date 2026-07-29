# Gate tests

Throwaway harness to answer the questions that block SPEC-V3. Nothing here is part of
the scraper. Delete the whole `gates/` directory when it has served its purpose.

Everything lives under `gates/` so it cannot touch the real `data-dir` or
`data-dir-template`.

| Gate | Question | Blocks |
|---|---|---|
| A | Does a cookie-less profile reach a watch page, or land on a consent wall? | Whether the design works at all |
| B | Does an unpacked extension survive a profile copy with a stable ID? | Whether the golden-template model works |
| C | What is actually in the payloads, and how deep does the sidebar go? | The SQLite schema |

All three are answered in one sitting. Do them in order.

The probe extension's pinned ID is **`mpjlhipdjoibcphphiemmnbckofdoimb`**, derived from
the public key in `probe-extension/manifest.json`. The matching private key is in
`keys/probe.pem`; it is only needed if we later pack a `.crx`, not for unpacked loading.

---

## Setup — build the template (once)

```bash
cd /Users/sor/Dev/youtube/research/pipelines/extraction/tools/scraper
./gates/gate.sh template
```

Chrome opens on `chrome://extensions` against `gates/data-dir-template`. Then:

1. **Do not sign in. Do not enable Sync.**
2. Turn on **Developer mode** (top right).
3. **Load unpacked** → select `gates/probe-extension`.
4. Confirm the ID shown is exactly `mpjlhipdjoibcphphiemmnbckofdoimb`.
   If it differs, stop and tell me — the pinned-key mechanism isn't working.
5. Install **I still don't care about cookies** from the Chrome Web Store.
   (Deliberately **not** installing uBlock Origin Lite — we dropped it.)
6. `⌘⇧⌫` → **Clear browsing data** → *All time*, everything checked → Clear.
7. **Quit Chrome entirely** (`⌘Q`, not just closing the window).

---

## Gate B — does the extension survive the copy?

```bash
./gates/gate.sh run
```

This does the real reset sequence: delete runtime, `ditto` the template, strip
`Singleton*` locks, launch. Then in the fresh window:

1. Go to `chrome://extensions`.
2. **Record what you see**, this is the whole gate:
   - Is *YTS Probe* listed at all?
   - Is it **enabled**?
   - Is its ID still `mpjlhipdjoibcphphiemmnbckofdoimb`?
   - Is *I still don't care about cookies* present and enabled?
   - Did Chrome show a "Disable developer mode extensions" bubble?
3. Click **service worker** under YTS Probe to open its console. It should print
   `service worker up — id=… MATCHES pinned key`.

Also note the `ditto` time the script prints — that's the per-video fixed cost, and it
decides whether per-video profile resets are viable at your scale.

**Pass** = listed, enabled, same ID. **Fail** = missing, disabled, or a different ID.

---

## Gate A — the consent wall

Still in that same fresh runtime window (its cookie jar is genuinely empty):

```
https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

Record:

1. Did you land on the watch page, or get redirected to `consent.youtube.com` /
   `consent.google.com`?
2. If a consent interstitial appeared, did **I still don't care about cookies** dismiss
   it on its own? How long did that take?
3. If it dismissed it, did you end up on the watch page or somewhere else?
4. Did the video **start playing**? It should not — the launch flags include
   `--autoplay-policy=document-user-activation-required` and `--mute-audio`. If it plays
   anyway, that flag no longer works and we need a different approach.
5. Is there a cookie banner *inside* the page that's still showing?

Then **repeat the whole thing with the cookie extension disabled** (`chrome://extensions`
→ toggle off → reload the page) so we know whether the wall appears at all. If there's no
wall either way, we can drop that extension too and ship a zero-dependency template.

**This is the pass/fail gate that matters most.** If every cookie-less load hits a wall
that isn't auto-dismissed, the per-video reset design needs rethinking.

---

## Gate C — characterise the payloads

Once you're on a watch page, open DevTools (`⌥⌘I`) → **Console**. The probe logs as it
captures. Then:

```js
__ytProbe.summary()
```

Expect `gotInitialData: true` and at least one entry under `byEndpoint`.

Now **scroll the page slowly**, pausing a second or two each time content loads, until
you've triggered at least three or four rounds of loading. Watch the console — each
captured payload logs a line. Then:

```js
__ytProbe.verify()
```

This is the important one. It must show `snapshot` **smaller** than `liveNow`:

```
lockups:        { snapshot: 26, liveNow: 78, fromWire: 52 }
commentThreads: { snapshot: 0,  liveNow: 40, fromWire: 40 }
```

`snapshot` is what the page shipped, `liveNow` is what YouTube has since mutated the same
object into, and `fromWire` is what arrived over the network. `snapshot + fromWire` should
equal `liveNow`. **If `snapshot` and `liveNow` are equal after scrolling, the deep copy
did not take** and the capture is contaminated — tell me, don't just send the file.

Then:

```js
__ytProbe.summary()
__ytProbe.shapes()
```

`shapes()` lists every `*Renderer` / `*Continuation` key seen and how often. That tells
us the item types in the sidebar, whether comments really do arrive on
`/youtubei/v1/next`, and whether continuation tokens behave as expected.

Finally export the full capture:

```js
__ytProbe.save()
```

That downloads a JSON file. Send it to me and I can write the parser and the schema
against real data instead of guesses.

**Please do this for three different videos**, in three separate fresh runtime profiles
(re-run `./gates/gate.sh run` between each):

- a large mainstream video with many comments
- a small/niche video with few comments
- a video you'd expect to be odd — a live stream VOD, or something age-restricted

Three files, and the schema stops being guesswork.

### What I most want to learn from Gate C

- How many sidebar recommendations are reachable in total before the chain ends.
- Whether the sidebar keeps paginating via `/youtubei/v1/next` or stops after round one.
- Whether comments and recommendations arrive on the same endpoint and how they're
  distinguished.
- The largest single payload, which sets the message-size budget.
- Whether non-video sidebar entries (Mixes, playlists) are present and how they're typed.

---

## Cleanup

```bash
rm -rf gates/data-dir gates/data-dir-template
```
