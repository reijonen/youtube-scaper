# Implementation plan

Step-by-step build order for `web-ui/`, as specified in [SPEC.md](SPEC.md). The spec says
what is being built and why; this document says in what order and what "done" means at
each step.

Read SPEC.md first. Where this plan and the spec appear to disagree, the spec wins.

## Repository boundary

This is a self-contained project inside a larger repository. Respect the boundary.

**Create and modify freely:** everything under `web-ui/`, plus `web-ui/` entries appended
to the repository `.gitignore`.

**Read, never modify:** `src/scraper/storage/schema.sql` and `data/db.sqlite3`.

**Do not read as guidance, do not modify:** the repository-root `SPEC-V3.md`, `PLAN.md`,
`DECISIONS.md`, and `README.md` — those govern the scraper, not this project, and applying
them here would be a mistake. Likewise leave alone `src/scraper/` (apart from
`schema.sql`), `extension/`, `tests/`, `bin/`, `keys/`, `data-dir-template/`, `data/raw/`,
`run/`, and `.venv/`.

Everything needed to build this is in `web-ui/SPEC.md`, this file, and the schema. Nothing
outside `web-ui/` needs to be consulted.

## Ground rules

- **Never write to the database.** The connection is opened read-only and is never
  migrated, created, or written. The avatar cache is the only thing written to disk.
- **The scraper may be running.** The database is in WAL mode; read-only access is safe
  concurrently. Do not take locks and do not assume exclusive access.
- **Queries stay renderer-agnostic.** Query functions return plain `{nodes, edges}` data.
  They do not import Sigma, graphology, or any rendering type.
- **Match the repository's existing TypeScript conventions**, taken from `extension/`:
  ES2022 target, ESM, `strict: true`, `noUncheckedIndexedAccess: true`, npm with a
  committed `package-lock.json`. These are restated here so no other directory needs to be
  opened.
- **Record non-obvious findings in `web-ui/DECISIONS.md`** as you go, mirroring how the
  repository root documents the scraper. A choice that took thought, or a gotcha that cost
  time, belongs there.
- Environment as verified at planning time: Node 25.8.2, npm 11.11.1.

## Phase 0 — Scaffolding

### Deliverables

- A SvelteKit 2 / Svelte 5 project in `web-ui/`, TypeScript, npm.
- `tsconfig.json` matching the conventions in Ground rules.
- `.gitignore` entries for `web-ui/node_modules/`, the avatar cache directory, and
  SvelteKit build output.
- `npm run dev` and `npm run typecheck` scripts. No build or deploy script.

### Done when

`npm run dev` serves a page, and `npm run typecheck` passes clean.

## Phase 1 — Read-only data access

### Deliverables

- `src/lib/server/db.ts`: opens `better-sqlite3` with `readonly: true` and
  `fileMustExist: true`.
- Database path from an environment variable, defaulting to a path relative to the project
  so the web-ui makes no assumption about its location in the tree.
- A single shared connection for the server process, not one per request.

### Gotchas

- `fileMustExist: true` is not optional. Without it, `better-sqlite3` silently creates an
  empty database on a mistyped path, and every view then renders an empty graph that looks
  like a data problem rather than a configuration one.
- `better-sqlite3` is a native module and must compile against the local Node version.
- Server-only code must live under `src/lib/server/` so SvelteKit refuses to bundle it into
  the client. A database import that reaches the browser fails confusingly.

### Done when

A trivial server-side query returns a row count from the real database, and pointing the
environment variable at a nonexistent path fails loudly instead of returning zero rows.

## Phase 2 — Channel graph query

The core logic of the entire view. Everything that can be silently wrong lives here.

### Deliverables

- `src/lib/server/queries/channelGraph.ts`, returning `{nodes, edges}` for both modes.
- Seed nodes: every video with `status = 'completed'`, including any with no
  recommendations, which appear as isolated nodes.
- Channel nodes: every distinct `channel_id` in `recommendation_channels`, whether or not
  it carries weight.
- Weighted edges: **one edge per `(seed, channel)` pair**, never one per recommendation.
  For every recommendation in that seed whose `position = 0` channel is that channel,
  accumulate

  ```
  weight = 1 / log2(1 + raw_position)
  ```

  The edge's weight is that sum; the number of recommendations contributing to it is that
  edge's credit count. Carry both — the spec requires both to be displayable, never the
  score alone.
- Collaborator edges: pairs credited only at `position > 0`, weight zero, flagged so the
  renderer can style them (reduced opacity, distinct color). A pair with any position-0
  credit is a weighted edge and gets no collaborator edge — see Gotchas.
- Channel label: `handle`, falling back to `name`.
- A source-resolution function mapping a seed video to its graph node. It returns the seed
  video itself. It exists as a single named function because a future channel-to-channel
  mode replaces exactly this and nothing else.

### Gotchas

- `raw_position` is 1-based in the current data, so `log2(1 + p)` is safe. It is one row of
  bad data away from dividing by zero. Assert `raw_position >= 1` rather than trusting it.
- Use `raw_position`, not `normalised_position`. This is deliberate and explained in the
  spec: filtered-out playlist blocks occupied real screen space.
- `run_id` is ignored entirely. Do not filter, group, or label by it.
- Do not read `runs`, `raw_payloads`, or `received_batches`. From `videos`, read only
  `video_id` and `status`.
- **A `(seed, channel)` pair can be both.** A channel may be the main channel on one
  recommendation and a collaborator on another within the same seed — 5 pairs are like this
  in the current data. Resolve it as: any position-0 credit makes the pair a weighted edge,
  and its collaborator appearances then contribute nothing and produce no second edge.
  Compute weighted pairs first, then emit collaborator edges only for pairs not already in
  that set. Getting this wrong yields parallel weighted-and-collaborator edges between the
  same two nodes.

### Tests

Vitest, against a small fixture database created in test setup. Never against
`data/db.sqlite3`.

- The DCG weight is correct at known positions: 1 → 1.00, 3 → 0.50, 7 → 0.333.
- A recommendation with several channels contributes weight to the `position = 0` channel
  only, and the others appear as zero-weight collaborator edges.
- A channel appearing only as a collaborator is present as a node with total weight zero.
- A seed crediting the same channel on several recommendations yields **one** edge whose
  weight is the sum and whose credit count is the number of recommendations — not parallel
  edges.
- A `(seed, channel)` pair credited both at position 0 and as a collaborator yields exactly
  one weighted edge and no collaborator edge.
- Per-seed grouping isolates seeds: a channel in two seeds yields two distinct edges, not a
  merged one.
- A completed video with no recommendations yields an isolated seed node.
- Credit counts and weighted scores are carried independently and neither is derived from
  the other at render time.

### Done when

Tests pass, and running the query against the real database reproduces the figures in
*Verification against real data* at the end of this document. Treat a mismatch as a bug in
the query, not as stale documentation — but if a figure appears internally inconsistent,
say so rather than picking whichever reading matches your code.

## Phase 3 — View registry

### Deliverables

- `src/lib/views/`, one folder per visualization, each exporting
  `{ id, title, question, load, component }`.
- Automatic discovery populating the navigation, so a new view is one folder with no
  routing or registry edits.

### Done when

The channel graph view is discovered and navigable without any file outside its own folder
naming it.

## Phase 4 — Avatar cache

### Deliverables

- A server route serving an avatar by `channel_id`: cache hit streams the stored file, miss
  fetches the recorded URL, writes it to the cache, then streams it.
- Cache directory under `web-ui/`, gitignored, safe to delete at any time.
- Fallback response — a colored circle bearing the channel's first initial — when a channel
  has no usable avatar or the fetch fails.

### Gotchas

- **Validate `channel_id` against a strict identifier pattern before it touches a
  filesystem path.** It arrives from the URL. Unvalidated, it is a path traversal.
- Do not cache failures. A transient network error must not become a permanently broken
  avatar that only a manual cache wipe fixes.
- Every channel in the current data has exactly one avatar source at 68×68, but 36 of 378
  source records carry no `width` field. Read the URL; do not require the dimensions.

### Done when

A cold cache populates on first render, a warm cache issues no outbound requests, and
deleting the cache directory recovers cleanly.

## Phase 5 — Total mode

### Deliverables

- graphology graph built from the Phase 2 query output.
- Sigma renderer, ForceAtlas2 layout running in a web worker.
- Node size from total weighted score across all seeds; zero-weight channels render at a
  fixed minimum size so they stay visible and hoverable.
- Node fill from the avatar route via `@sigma/node-image`.
- Collaborator edges rendered in a distinct color, at reduced opacity.
- A node reducer dropping image fills for plain colored circles once the visible node count
  passes the threshold where image nodes stop performing.

### Gotchas

- Sigma renders large graphs comfortably with default styles but degrades well before that
  when every node carries an image. The reducer is what keeps this view usable as the
  corpus grows; it is not optional polish.
- ForceAtlas2 must run in its worker. Run on the main thread and the UI freezes during
  layout.
- The reducer is a rendering concern only. It must not alter weights, sizes-as-data, or
  anything the query produced.
- **Sigma has no built-in dashed edge type.** Checked directly against the installed
  `sigma` source, its changelog, and the npm registry — no such capability or companion
  package exists. Don't reach for a custom WebGL edge shader to get literal dashing; render
  collaborator edges with Sigma's default (solid) edge program, distinguished by color and
  reduced opacity only.

### Done when

The graph renders against the real database, multi-seed channels visibly settle toward the
centre, single-seed channels sit on the fringe, and the three collaborator clusters are
identifiable by their color and reduced opacity.

## Phase 6 — Per-seed mode and interaction

### Deliverables

- A mode toggle on the same route. Both modes consume the same query output.
- Per-seed ranked layout: channels ordered and sized by weighted score within one seed,
  avatars and labels visible. Drawn directly in the component — this mode does not use
  Sigma.
- Hover on a channel shows its per-seed breakdown: weighted score and unweighted credit
  count for every seed it appears in.

### Gotchas

- Do not reuse the force layout here. A single seed's graph is a star, and a force layout
  of a star communicates nothing — this is why the mode exists as a separate layout rather
  than a filter.

### Done when

Both modes render from one query, the toggle preserves selection sensibly, and hover
reports both numbers.

## Verification against real data

The figures below were measured at planning time and are what a correct implementation
reproduces against the current database. They are checks, not targets — if the database has
grown, the shape should still hold.

| Quantity | Value |
|---|---|
| Seed videos (`status = 'completed'`) | 4 |
| Distinct channel nodes | 212 |
| Channels carrying weight | 197 |
| Collaborator-only channels (zero weight) | 15 |
| Recommendations, each with exactly one position-0 channel | 355 |
| **Weighted edges** — distinct `(seed, channel)` pairs | **263** |
| …of which are credited by more than one recommendation | 27 |
| Collaborator credits (`position > 0`) | 23 |
| …falling on pairs that already have a position-0 credit | 5 |
| **Collaborator edges** (reduced opacity) — pairs, overlap excluded | **18** |
| Total edges of any kind (263 + 18) | 281 |
| `raw_position` range | 1–146 |

Note the distinction between 355 and 263: 355 is the number of weighted *credits*, and 263
is the number of *edges* those credits collapse into. Both matter — the credit count is
displayed alongside every weighted score — but only 263 edges are drawn.

Heaviest edges, for a spot check: Numberphile in `JEPqrqNqkHw` (23 credits, weight ≈ 4.724),
Numberphile in `1cvKGqgOx_8` (20 credits, ≈ 4.669), Numberphile in `cOTf_YEmSOU`
(18 credits, ≈ 3.945), Classic 80s Mix in `dQw4w9WgXcQ` (7 credits, ≈ 1.559).

The three multi-channel recommendations are recognisable: a nine-channel cluster
("No Fluff …"), a three-channel music cluster, and a four-channel shorts cluster. Two
collaborator-only channels — Dr. Becky and Philosophical Instrumentals — are legitimate
channels rather than network padding, and must remain visible in the graph.
