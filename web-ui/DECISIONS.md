# web-ui — Decisions

Non-obvious choices and gotchas found during implementation, mirroring the repository
root's `DECISIONS.md` convention. See [SPEC.md](SPEC.md) and [PLAN.md](PLAN.md) for what
governs this project.

## Phase 0 — Scaffolding

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

## Phase 1 — Read-only data access

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

## Phase 2 — Channel graph query

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

## Phase 3 — View registry

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
