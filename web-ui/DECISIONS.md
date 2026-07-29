# web-ui — Decisions

Non-obvious choices and gotchas found during implementation, mirroring the repository
root's `DECISIONS.md` convention. See [SPEC.md](SPEC.md) for what governs this project and
the root `BACKLOG.md` for open work.

**This file is append-only.** Add entries; never edit or delete existing ones. Supersede by
appending.

**Historical vocabulary.** Entries below were written during the original phased build.
`PLAN.md` burned down completely and was deleted — its durable content moved into
`SPEC.md`, this file, and `BACKLOG.md`. "Phase N" referred to that plan's build order;
sections here are keyed by subject instead. Where an entry cites a figure or rule from
`PLAN.md`, the current statement of it is in `SPEC.md`.

## Scaffolding

- Scaffolded with `npx sv create` (the current official SvelteKit CLI, successor to
  `create-svelte`) rather than hand-writing config. Template `minimal`, `--types ts`,
  `--add vitest="usages:unit"` (component testing add-on skipped — SPEC.md says
  components are never tested here, only the query layer).
- Versions resolved from the npm registry at scaffold time matched the spec's pins
  exactly: `@sveltejs/kit` 2.70.1, `svelte` 5.56.8, `better-sqlite3` 13.0.2, `graphology`
  0.26.0, `graphology-layout-forceatlas2` 0.10.1, `sigma` 3.0.3, `@sigma/node-image`
  3.0.0. No version pins needed adjusting.
- The current `sv create` output has no `svelte.config.js` — the SvelteKit Vite plugin now
  takes its options (adapter, compiler options) directly in `vite.config.ts`. This is
  correct for this SvelteKit version, not a missing file.
- `package.json` scripts were trimmed post-scaffold: dropped `build` and `preview` (PLAN.md
  is explicit — dev server only, no build or deploy script) and renamed `check` /
  `check:watch` to `typecheck` / `typecheck:watch` to match PLAN.md's stated script names.
  `adapter-auto` remains a dependency and is still wired in `vite.config.ts` since removing
  it isn't required by the spec and keeps `vite dev` itself unaffected; it's simply never
  invoked by any script.
- `tsconfig.json` extends the SvelteKit-generated `.svelte-kit/tsconfig.json`. Added
  `noUncheckedIndexedAccess: true` and `target: "ES2022"` explicitly, since the generated
  base sets `target: "esnext"` and omits `noUncheckedIndexedAccess` — both are required by
  the repo's stated TypeScript conventions (taken from `extension/`).
- `.gitignore`: rather than keep the `sv create`-generated `web-ui/.gitignore`, its entries
  were merged into the repository-root `.gitignore` under a `# web-ui` section (`SPEC.md`
  calls for "`web-ui/`-related entries appended to the repository `.gitignore`", not a
  second ignore file). Chose `web-ui/avatar-cache/` as the avatar cache directory name,
  ahead of Phase 4 actually creating it.
- Removed the scaffold's demo content (`src/lib/vitest-examples/`, the default welcome
  markup in `src/routes/+page.svelte`) since it's noise against the real view to be built
  in Phase 3 onward.

## Read-only data access

- `src/lib/server/db.ts` resolves its default DB path relative to its own file location
  (`import.meta.url`), not `process.cwd()` — four directories up
  (`src/lib/server` → `web-ui` → repo root) then `data/db.sqlite3`. This makes the default
  correct regardless of what directory `npm run dev` happens to be invoked from, matching
  SPEC.md's "makes no assumption about its own position in the directory tree."
- Env var name: `SCRAPER_DB_PATH`. Not specified by name in SPEC.md/PLAN.md, only that one
  must exist.
- Verified both `fileMustExist` failure modes manually against the real database (not as a
  committed test — PLAN.md reserves committed tests for the fixture DB only):
  - Real DB, default path: query returns `355` recommendations, matching the row count
    used throughout PLAN.md's verification table.
  - `SCRAPER_DB_PATH` pointed at a nonexistent file: request fails with
    `SqliteError: unable to open database file` (visible 500, not a silent empty result),
    and no file is created at the bad path.
  - Verification was done via a throwaway `+server.ts` route hit with `curl`, then deleted;
    it's not part of the committed source.

## Channel graph query

- **A spec/plan inconsistency was caught and fixed before implementing, not after.**
  PLAN.md's Phase 2 deliverable originally said weighted edges are formed "summed per
  (seed, channel) pair" (i.e. merged — one edge per pair, weight and credit count
  accumulated across every recommendation in that seed crediting that channel). Against
  the real database that merge produces 263 edges. But the verification table at the time
  said "Weighted edges | 355" — a number that only falls out if edges are *not* merged
  (355 is the total count of recommendations, and every recommendation has exactly one
  position-0 channel, no exceptions). Rather than pick whichever reading matched easier
  code, this was raised with the user before writing any query code. Resolution: **merged
  is correct (263 edges)**; 355 is the weighted *credit* count, a different, also-displayed
  number, not the edge count. Both SPEC.md and PLAN.md were corrected in place.
- **That question also surfaced a real, previously-unplanned bug.** 5 `(seed, channel)`
  pairs in the current data are credited *both* at position 0 (on one recommendation) and
  as a collaborator at position > 0 (on a different recommendation) within the same seed —
  e.g. a channel is the main channel on one sidebar entry and a listed collaborator on
  another, in the same video's sidebar. Naively building "weighted edges" and "collaborator
  edges" as two independent sets emits a duplicate solid-and-dashed edge pair between the
  same two nodes for these 5 cases. Fixed rule, now in both governing docs: **compute the
  weighted `(seed, channel)` pair set first; a collaborator credit only produces a dashed
  edge when that exact pair has no position-0 credit anywhere in that seed.** This takes
  dashed edges from a naive 23 down to 18 (23 collaborator credits, 5 of which fall on
  already-weighted pairs and are absorbed into the existing solid edge instead of drawn
  separately).
- Corrected figures used for Phase 2's "done when" check (see PLAN.md's *Verification
  against real data* table for the authoritative version): 355 recommendations/credits,
  263 weighted edges (27 of which are credited by >1 recommendation), 23 collaborator
  credits (5 overlapping weighted pairs), 18 dashed edges, 281 total edges. Verified via a
  throwaway `+server.ts` route against the real database (not committed) — every figure
  reproduced exactly.
- `getChannelGraph(db)` takes its `better-sqlite3` connection as a required parameter
  rather than defaulting to the app's singleton from `db.ts`. An earlier draft imported
  `db.ts` for a default-parameter fallback, but importing that module has a side effect —
  it opens the real database at import time — which would make even the fixture-based
  Vitest suite implicitly touch (or fail to find) `data/db.sqlite3` on import, contrary to
  "tests run against a small fixture database ... never against `data/db.sqlite3`."
  Callers (view `load` functions) import `db` from `$lib/server/db` themselves and pass it
  in explicitly.
- The fixture builder (`__fixtures__/testDb.ts`) applies the real
  `src/scraper/storage/schema.sql` verbatim (read, never copied by hand or modified) to a
  fresh `:memory:` database per test run, then inserts minimal rows. This keeps the test
  schema from silently drifting out of sync with the scraper's actual schema.

## View registry

- **Route strategy: one generic dynamic route, `src/routes/[view]/+page.server.ts` +
  `+page.svelte`, not one route folder per view.** SPEC.md says the channel graph's route
  is `/channels`, but PLAN.md's Phase 3 "done when" requires the view to be "discovered and
  navigable without any file outside its own folder naming it" — a hand-written
  `src/routes/channels/` folder would itself be a file outside `src/lib/views/channels/`
  that names the view. Resolved by giving the channel-graph manifest `id: 'channels'` and
  letting the single generic `[view]` route match on that id — the route matches
  SPEC.md's literal URL while staying generic: it never changes when a new view folder is
  added, satisfying the architecture's "no routing changes" promise.
- `src/lib/views/index.ts` uses `import.meta.glob<{ default: ViewManifest }>('./*/manifest.ts', { eager: true })`
  to discover views. `eager: true` was chosen over lazy imports because this app has no
  production build/deploy target (SPEC.md) and no meaningful number of views yet — code-split
  loading has no payoff here and would only add async-await ceremony to `+layout.svelte`'s
  nav rendering.
- **A worry that turned out to be unfounded: SvelteKit's "cannot import `$lib/server` into
  client code" guard does not fire here**, even though `+layout.svelte` (a client
  component) imports `$lib/views`, which eagerly imports every `manifest.ts`, which in turn
  imports `$lib/server/queries/channelGraph.ts`. Verified directly by running `npm run dev`
  and hitting both `/` and `/channels` — no build/runtime error, and the page renders.
  This works because `channelGraph.ts` only takes a `better-sqlite3` `Database` as a
  type-only import (`import type { Database } from 'better-sqlite3'`) and never imports the
  native module itself at the value level, so nothing server-only actually crosses into the
  client bundle. Loading real data still only happens in `+page.server.ts`, which passes the
  already-computed `ChannelGraph` object to the client component — the client never touches
  `db.ts` or the native module. Worth re-checking if a future view's query module ever needs
  a runtime (not type-only) import from `better-sqlite3` or `node:*` — that would very
  likely trip the guard and require moving `load` out of the eagerly-globbed manifest.
- `ChannelsView.svelte` in Phase 3 is a placeholder (a plain counts summary) — Phase 5/6
  replace it with the actual Sigma/ranked-list rendering. Its only job here is to prove data
  flows from `getChannelGraph` through the registry to a rendered component.

## Avatar cache

- Route: `src/routes/avatars/[channelId]/+server.ts` (`GET /avatars/:channelId`).
- **Cache encodes content-type in the file extension instead of a sidecar metadata file.**
  On a miss, the fetched response's `content-type` header picks an extension from a small
  known map (`image/jpeg` → `.jpg`, etc., defaulting to `.jpg` for anything unrecognized);
  the bytes are written to `<cacheDir>/<channelId>.<ext>`. On a lookup, the route checks
  for each known extension in turn and infers the content-type back from whichever one
  exists. This avoids a second file (or a JSON index) just to remember one MIME string per
  channel, at the cost of one `existsSync` call per known extension on every cache-hit
  request (4 extensions today — negligible).
- `CHANNEL_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/` validates `params.channelId` before it
  touches any path — an invalid id gets a plain 400, never a filesystem operation.
  Verified live: `..%2F..%2Fetc%2Fpasswd` (URL-encoded path traversal) is rejected with 400
  before any `existsSync`/`fetch` call.
- Failures are never cached, by construction: `writeFileSync` is reached only after
  `fetch` succeeds *and* `response.ok`. Every other branch (network error, non-OK status,
  channel not in the db, empty `avatar_sources_json`, no row at all) returns the fallback
  SVG directly without touching the cache directory, so a transient failure self-heals on
  the next request instead of needing a manual cache wipe.
- Fallback is a hand-built `<svg>` (colored circle + first-letter initial), color derived
  from a simple deterministic string hash of the display label (`handle ?? name ?? channel_id`)
  so a given channel always gets the same fallback color across requests, without needing
  to persist anything.
- Verified live against the real database end-to-end: cold fetch for a real channel_id
  writes `avatar-cache/<id>.jpg` (2892 bytes, matching a direct `curl` of the source CDN
  URL) and returns `content-type: image/jpeg`; a second request returns byte-identical
  content with the cache file's mtime unchanged (no re-fetch); deleting `avatar-cache/`
  entirely and re-requesting recovers the identical image with no manual steps; an unknown
  channel_id renders the fallback SVG.
- The cache directory resolves relative to the route file's own location
  (`import.meta.url`, four levels up to `web-ui/`), the same pattern as `db.ts` in Phase 1,
  for the same reason - no assumption about the process's working directory.

## Graph rendering

- **SPEC.md's claim that "dashed edges are a built-in Sigma edge type" is false**,
  discovered before writing any rendering code (grepped the installed `sigma@3.0.3`
  source for "dash" — zero matches; checked the CHANGELOG — never mentioned; checked the
  npm registry for an `@sigma/edge-*` dash package — none exists). Raised with the user;
  resolved as color + reduced opacity only, using Sigma's default solid edge program. Both
  SPEC.md and PLAN.md corrected in place (see their Stack/Collaborator rendering sections
  and Phase 5's gotchas). Full account in `feedback_verify_spec_numbers_before_coding`
  (session memory) alongside the earlier Phase 2 edge-count issue — same pattern: a
  spec's factual claim didn't survive being checked against the actual installed package.
- **Sigma, `@sigma/node-image`, and `graphology-layout-forceatlas2/worker` are all loaded
  via dynamic `import()` inside `onMount`, never as static top-level imports.** All three
  reference browser-only globals (`WebGL2RenderingContext`, `Worker`) at module scope.
  Statically importing any of them crashed SvelteKit's SSR pass with
  `ReferenceError: WebGL2RenderingContext is not defined` — verified live, then fixed by
  moving the imports inside `onMount`, which never runs during SSR. `graphology` and the
  synchronous `graphology-layout-forceatlas2` (used only for `inferSettings`) stayed as
  static imports; their module scope has no browser-global references (checked directly).
- **The view registry's server-only-import guard (flagged as a risk in Phase 3
  DECISIONS.md) did fire once real Sigma code existed**, with
  `Cannot import $lib/server/queries/channelGraph.ts into code that runs in the browser`
  from `+layout.svelte → $lib/views/index.ts → manifest.ts → channelGraph.ts`. (It hadn't
  fired during Phase 3's own testing — apparently order- or cache-dependent in dev — but
  reproduced reliably here across full server restarts, so it needed a real fix, not just
  a re-test.) Fixed the same way as the Sigma SSR crash: `manifest.ts`'s `load` field wraps
  the `getChannelGraph` import in a dynamic `import('$lib/server/queries/channelGraph')`
  instead of a static one. SvelteKit's guard evidently doesn't flag dynamic-import
  specifiers the same way it flags static ones, and since `load` is only ever *called* from
  server-side code (`+page.server.ts`), the dynamically-imported chunk is never actually
  evaluated in the browser either way. `manifest.ts`'s only remaining top-level import from
  `$lib/server` is `import type { ChannelGraph }`, which is type-only and erased entirely.
- **Sigma's canvases stayed stuck at the browser's default 300×150 `<canvas>` size** on an
  early run despite the container being correctly sized (confirmed via
  `container.offsetWidth/Height` logged immediately before `new Sigma(...)`) - a one-off
  dev-server/HMR glitch during iteration, not a real bug: a clean full restart (stopping
  the dev server, deleting `.svelte-kit/`, restarting) reproduced correct sizing every
  time after that, and the resize is driven by Sigma's own `resize()` call at construction
  plus a `window` `resize` listener, not by anything this code controls.
- Node/edge visual encoding, chosen and tuned by eye against the real graph:
  - Seed nodes: fixed size 9, flat gray (`#4b5563`), plain (no image/avatar - seeds are
    videos, not channels).
  - Channel nodes: size `4 + sqrt(totalWeight) * 6` (a fixed floor for zero-weight
    collaborator-only channels, per SPEC.md), filled with the channel's avatar via
    `@sigma/node-image`'s `NodeImageProgram`, `image: '/avatars/' + channelId` (Phase 4's
    route). Fallback background color (shown before the image loads or if a channel has no
    avatar) is the same hash-of-label-to-hue function used for the avatar route's own SVG
    fallback in Phase 4, duplicated rather than shared, since one is server code and the
    other is client code with no natural common module.
  - Weighted edges: solid, `rgba(100,100,220,0.55)`. Collaborator edges:
    `rgba(150,150,150,0.25)` - visibly fainter and a different hue, satisfying "distinct
    color, at reduced opacity" without any custom shader.
  - Graph node keys are prefixed (`seed:<id>` / `channel:<id>`) rather than using the raw
    `video_id`/`channel_id` directly, so a coincidental string collision between a video id
    and a channel id (not currently possible given YouTube's id formats, but not something
    to rely on) can never merge two unrelated nodes into one.
- **`IMAGE_NODE_THRESHOLD = 300`, not the more conservative 150 first tried.** The real
  dataset has 216 total nodes (212 channels + 4 seeds); at 150 every node would always
  fall back to plain circles today, which technically satisfies the spec's fallback
  requirement but defeats the point of having avatars at all against current data. 300
  keeps today's graph fully illustrated while still guarding against unbounded growth.
  Verified both states directly: at a temporarily-lowered threshold, confirmed the
  fallback (plain circles, matching the spot-check figures for cluster shape); at 300,
  confirmed real avatar images load through the Phase 4 route (`GET /avatars/<id>` → 200,
  visible in the network log) and render as the node fill.
- ForceAtlas2 runs via the worker-based `FA2LayoutSupervisor`
  (`graphology-layout-forceatlas2/worker`), started in `onMount` and stopped after a fixed
  4-second settle timer (`setTimeout`), rather than run indefinitely or for a fixed
  iteration count - the supervisor's start/stop API doesn't expose iteration counts, and a
  static dataset like this has no ongoing reason to keep recomputing layout after it
  settles. `layout.kill()` and `sigmaInstance.kill()` both run in `onDestroy`.
- Added `.claude/launch.json` (`web-ui-dev`, `npm run dev --prefix web-ui`, port 5173) to
  drive the in-app browser preview tool against this project during development/testing.

## Interaction and hover verification

- Mode/seed-selection state (`mode`, `selectedSeedId`) lives in `ChannelsView.svelte`,
  the parent of both `TotalModeGraph` and `PerSeedRanked`, rather than inside either mode
  component. Neither mode component unmounts-and-loses-state the other's context this way;
  toggling `Total → Per-seed → Total → Per-seed` keeps the same seed selected throughout -
  verified live by switching modes twice and confirming the `<select>` still showed the
  seed chosen before the first switch. This is what PLAN.md's "the toggle preserves
  selection sensibly" means in practice.
- `breakdown.ts`'s `channelBreakdown(data, channelId)` is the single shared function both
  modes use for the hover tooltip's per-seed data (weight + credit count per seed a
  channel appears in) - computed from `data.edges` alone, filtered by `target`, no new
  query needed since Phase 2's output already carries everything required.
- **Verifying hover interaction against Sigma's WebGL canvas was the hard part of this
  phase**, and is worth recording since it's non-obvious. A programmatically-dispatched
  `MouseEvent('mousemove', {clientX, clientY, bubbles: true})` on the container element
  reliably reached Sigma's `MouseCaptor` (which listens on `document` and checks
  `e.target === this.container`), but `sigma.getNodeAtPosition()` still returned `null`
  even when called directly with the exact viewport coordinates `graphToViewport()`
  reported for a known node - true for both image-typed and plain default-typed nodes, so
  it wasn't specific to `@sigma/node-image`. Root cause not fully chased down (likely the
  picking framebuffer needing a real paint/composite cycle that a synthetic, teleported
  event doesn't trigger the same way genuine OS-level pointer movement does). What
  actually worked: computing the node's target position via
  `sigma.graphToViewport(sigma.getNodeDisplayData(nodeId))` plus the container's
  `getBoundingClientRect()`, converting to the browser tool's screenshot-pixel space
  (`realPixel * (screenshotWidth / window.innerWidth)`), then issuing a **real** simulated
  hover through the browser automation tool (not a JS-dispatched event) at that
  coordinate - this reliably fired `enterNode` and rendered the tooltip with correct data
  (spot-checked against RobWords' known per-seed weights). Lesson for next time: verify
  Sigma hover/click interaction with the automation tool's real pointer actions, not
  synthetic `dispatchEvent` calls - the latter can silently fail Sigma's internal picking
  even when every event-listener-level check passes.
- Per-seed ranked list bar width uses `weight / maxWeight` within that seed (not a global
  max across all seeds), so the top channel in any given seed always fills the full bar
  width, matching "channels are ordered and sized by their weighted score **within that
  one seed**" (SPEC.md).
- Verified live against the real database: selecting seed `JEPqrqNqkHw` in per-seed mode
  reproduces the user's spot-check exactly - Numberphile, weight 4.72, ×23 credits.

## Graph model — reviewed 2026-07-29

Recorded after the first working build was reviewed against what it was meant to answer.

- **The seed→channel graph is bipartite and therefore cannot answer "who clusters around
  whom".** Every edge runs seed → channel; no code path emits a channel↔channel edge, and
  `GraphEdge` cannot express one. Channels have no adjacency to each other, so ForceAtlas2
  has nothing to cluster them by — what renders is four video hubs with 212 leaves. This
  was not a rendering bug: the clustering relation was never computed. The original brief
  ("who is recommended how much in whose videos") was encoded literally as seed→channel and
  the channel-to-channel graph deferred, which produced a correct implementation of the
  wrong instrument. Clustering questions now belong to the co-recommendation view
  (`BL-008`); the bipartite view keeps its own narrower question and is not being deleted.

- **A directed channel→channel graph would not have fixed it either, at today's data.**
  Swapping the four video nodes for their four owning channels relabels the hubs and
  changes nothing else — same four hubs, same 212 leaves, still zero adjacency among the
  leaves. Directed edges only form a network once channels seen as recommendations are
  themselves scraped and the loop closes. Worth stating explicitly in `SPEC.md` so a future
  implementer of `BL-007` does not read the expected shape as a failure of their own work.

- **DCG position weight must not be applied to co-recommendation edges.** A directed edge
  weights an observed event (B sat at slot *k* on A's page — an attention magnitude); a
  co-recommendation edge weights a statistical association (X and Y are treated as
  interchangeable). Weighting co-occurrence by position would assert that channels near the
  top of a sidebar are more *similar* to each other than channels near the bottom, which
  confuses attention with similarity. If anything the reverse holds: deep slots are where
  YouTube has exhausted the obvious picks and is reaching into the real topical
  neighbourhood. The correct instrument is a normalized set-overlap measure, which also
  fixes the two problems position weighting does not touch — combinatorial inflation
  (`C(n,2)` edges per sidebar) and popularity conflation.

- **All three similarity measures get built rather than one being chosen.** Jaccard, cosine
  (Ochiai), and NPMI. NPMI is the measure that actually answers the clustering question
  because it discounts the null model directly, but it needs N in the hundreds and is
  meaningless at four seeds; cosine is the stable default meanwhile. Choosing one now and
  deferring the rest would mean rebuilding when the corpus deepens. Explicit instruction
  from the repository owner: build the state of the art, don't settle.

- **Self-loops are modelled as a node attribute, not an edge.** `selfShare(A)` is one
  number per node, so it is naturally a node property; drawn as a loop it would be
  illegible, inflate PageRank, and distort random walks. The trap worth recording: when
  row-normalizing, the self-loop must stay **in the denominator** even though its edge is
  not drawn, so the outward edges sum to less than 1 and the deficit carries the retention
  signal. Removing the self-loop before normalizing inflates the remaining edges to sum to
  1 and destroys the signal silently — nothing about the output looks wrong.

- **The per-seed ranked mode is dropped** (`BL-006`). Only one view was wanted initially and
  per-seed was not it; its question is better served by click-to-trace on a node
  (`BL-005`). The code still exists at time of writing.

- **Measured co-recommendation shape, before building anything on it:** 7,859 distinct
  undirected pairs across the four seeds, of which 7,070 (90%) share exactly one seed and
  are pure within-sidebar filler. Filtering to pairs sharing two or more seeds leaves 789
  edges over 44 channels — 83% density, still a hairball. At four seeds any channels-only
  projection is a union of four cliques. This is a fact about corpus depth, not about the
  measure, and it is why the view is specified to be built correctly rather than tuned
  against today's shape.
