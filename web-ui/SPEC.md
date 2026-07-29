# web-ui — Specification

A local, read-only viewer for the scraper's SQLite database. Its purpose is to make the
collected data legible as structure, not to reproduce it as tables.

## Scope

This document covers `web-ui/` only. Everything in it is decided; there are no open
items. Work not described here is out of scope for this spec.

## Preconditions

None. The view specified here depends on no normalized column — its weights derive from
`raw_position` and row counts, both already stored as integers, and its labels from text
columns consumed as-is.

Several columns in the database hold numbers rendered as display text (`view_count_text`
of `"274K"`, `published_text` of `"1 year ago"`). Normalizing those is the scraper's
responsibility, not the web-ui's, and it is deferred until a view actually requires it.
The web-ui never parses, coerces, or repairs values it reads — it consumes columns as
stored. A view that needs a real number waits for the scraper to provide one.

## Stack

| Concern | Decision |
|---|---|
| Framework | SvelteKit 2, Svelte 5 |
| Database access | `better-sqlite3`, opened read-only |
| Graph model | `graphology` 0.26 |
| Graph rendering | `sigma` 3.0 (WebGL), with `@sigma/node-image` 3.0 |
| Graph layout | `graphology-layout-forceatlas2` 0.10, run in a web worker |
| Charting library | None |
| Runtime | Dev server only — `npm run dev` |

There is no production build target and no deployment. The application is run locally by
its author.

Sigma is a WebGL renderer built on graphology, and it is chosen deliberately over an
SVG-based approach rather than as a concession to current data size. The corpus is
intended to grow; building the layout on SVG now would mean rebuilding it later, and the
renderer is not the part of this application worth doing twice. graphology also carries the
analytical satellite libraries (metrics, community detection) that later views will want,
so the graph model is not a dead end.

Two capabilities the specified view needs are native to this stack rather than custom
work: dashed edges are a built-in Sigma edge type, and image-filled nodes are provided by
`@sigma/node-image`. ForceAtlas2 runs in a web worker, so layout iteration never blocks
the UI.

One known limit shapes the design: Sigma renders very large graphs comfortably with default
styles but degrades well before that when every node carries an image. The view therefore
renders avatar-image nodes only while the visible node count is small enough to afford
them, and falls back to plain colored circles beyond that threshold, via a Sigma node
reducer. The fallback is a rendering concern only — it changes no data and no weighting.

Queries return renderer-agnostic `{nodes, edges}` structures regardless, so the query layer
is never coupled to Sigma.

No charting library is added until a view requires one. The channel graph does not.

## Data access

The database path is read from an environment variable with a relative default, so the
web-ui makes no assumption about its own position in the directory tree.

The connection is opened **read-only**. The web-ui never writes to the database, never
migrates it, and never creates it. The database is in WAL mode, so read-only access is
safe while the scraper is running.

Read-only refers to the database. The web-ui does write one thing to disk: a local avatar
image cache, described under *Avatar cache* below. It writes nothing else.

### Table boundary

The web-ui reads only content tables:

- `recommendations`
- `recommendation_channels`
- `comments`

It never reads pipeline tables — `runs`, `videos`, `raw_payloads`, `received_batches` —
except for `videos.video_id` and `videos.status`, which identify which videos are seeds.
This boundary is the reason the database is not split into separate files: separation is
enforced at the query layer instead. Splitting was rejected on two grounds. The database
is in WAL mode, under which SQLite's cross-database transactions are atomic per file but
not across the set, which would weaken the scraper's crash-safety guarantees. And the
scraper enforces foreign keys (`PRAGMA foreign_keys = ON`, a documented Phase 2
deliverable), which SQLite cannot apply across attached databases.

`data/raw/` is not read. Raw payloads are cold recovery, not a data source for this
application.

### run_id

`run_id` is ignored entirely. Recommendations reconcile on
`UNIQUE (video_id, recommended_video_id)`, so a row's `run_id` records whichever run
happened to write it first and carries no meaning for display. No view filters, groups, or
labels by run.

## Repository boundary

The web-ui is a self-contained project inside an existing repository. The boundary is
strict in both directions.

**Owned by this project — created and modified freely:**

- Everything under `web-ui/`, including its `SPEC.md`, `PLAN.md`, and `DECISIONS.md`
- `web-ui/`-related entries appended to the repository `.gitignore`

**Readable, never modified:**

- `src/scraper/storage/schema.sql` — the authoritative schema
- `data/db.sqlite3` — opened read-only

**Out of bounds — neither read as guidance nor modified:**

- The repository-root `SPEC.md`-equivalent documents: `SPEC-V3.md`, `PLAN.md`,
  `DECISIONS.md`, `README.md`. These describe the scraper's design and implementation
  history. They do not govern the web-ui, and following them here would be a mistake.
- `src/scraper/` (other than `schema.sql`), `extension/`, `tests/`, `bin/`, `keys/`,
  `data-dir-template/`, `data/raw/`, `run/`, `.venv/`

`web-ui/SPEC.md` and `web-ui/PLAN.md` are the governing documents for this work.
`web-ui/DECISIONS.md` records non-obvious choices and gotchas found during implementation,
mirroring the convention the repository root uses for the scraper.

Every convention the web-ui needs is stated in its own documents. No file outside
`web-ui/` needs to be consulted to build it, apart from reading the schema.

## Architecture

Each visualization is a self-contained folder under `src/lib/views/`, exporting a manifest:

```
{ id, title, question, load, component }
```

Views are discovered automatically and populate the navigation. Adding a visualization
means adding one folder — no routing changes, no registry edits.

Query functions live in `src/lib/server/queries/` and return renderer-agnostic data
structures. Rendering decisions belong to components, not queries.

### Testing

Query functions are tested; components are not. The weighting arithmetic, the
position-0 filtering, and the per-seed grouping are the parts that can be silently wrong
while looking plausible, and they all live in the query layer. Rendering errors are
visible on screen and need no test to catch.

Tests run against a small fixture database built in the test setup, never against
`data/db.sqlite3`.

### Source resolution

The "source" side of a graph edge is resolved through a single function that maps a seed
video to its graph node. Today it resolves a seed video to itself. A future
channel-to-channel view replaces this one function using a video-to-channel mapping held
outside this database. No schema column is added for this, and no other code changes when
it lands.

## Avatar cache

Channel avatars are remote YouTube CDN URLs stored in
`recommendation_channels.avatar_sources_json`. Every channel has exactly one source, at
68×68. Rendering the graph directly against those URLs would issue one request to Google
per channel on every render, so avatars are fetched once and cached on disk.

A server route serves an avatar by `channel_id`. On a cache hit it streams the stored
file. On a miss it fetches the URL recorded for that channel, writes it to the cache, and
streams it. The client references the route and never touches a CDN URL, so the number of
outbound requests is bounded by the number of channels, once.

The cache lives in a directory under `web-ui/`, excluded from version control. It is
disposable: deleting it costs one refetch and nothing else. It is the only thing the
web-ui writes.

A `channel_id` arriving at the route is validated against a strict identifier pattern
before it is used to build a filesystem path. A fetch failure is not cached, and renders
as the same fallback used when a channel has no usable avatar: a colored circle bearing
the channel's first initial.

## View: Channel graph

Route: `/channels`. One route, two modes, one toggle.

The question it answers: **who is recommended how much, and in whose videos.**

### Weighting model

A recommendation credits its channels in `recommendation_channels.position` order.
Position 0 is the main channel; positions above 0 are collaborators.

**Only the position-0 channel carries weight.** Collaborators are rendered but score zero.
This matches where views and watch time actually accrue on a collaboration upload, keeps
total credits equal to the number of real sidebar slots, and prevents a channel network
from multiplying its apparent reach by crediting many channels on a single slot.

Weight uses `raw_position` — true on-screen placement, including the playlist and mix
blocks that were filtered out of `normalised_position`. Those blocks occupied real screen
space and pushed later entries down, so `raw_position` is the faithful attention proxy.

For a channel `c` and seed video `s`:

```
weight(c, s) = Σ  1 / log₂(1 + raw_position(r))
```

over every recommendation `r` in seed `s` whose position-0 channel is `c`.

This is the standard DCG discount: slot 1 scores 1.00, slot 2 scores 0.63, slot 10 scores
0.29, slot 100 scores 0.15. It is steep enough to express position bias and gentle enough
that entries below the fold remain visible.

**The unweighted credit count is always displayed alongside the weighted score, never
instead of it.** A score whose derivation is invisible is not interpretable.

### Total mode

A force-directed bipartite graph, rendered by Sigma with ForceAtlas2 layout.

- **Nodes:** seed videos, plus every channel appearing in any seed's sidebar. Against
  current data that is 212 channel nodes — 197 carrying weight, 15 appearing only as
  collaborators.
- **Seed nodes:** every video with `status = 'completed'`, including any that produced no
  recommendations. Such a video renders as an isolated node, visibly distinct from a video
  that was never collected. This is deliberate: YouTube does not realistically serve a
  watch page with zero recommendations, so an isolated seed is a signal that the video
  should be collected again. Against current data no video is in this state — the two
  videos with no recommendations both have `status = 'failed'` and are therefore not seeds.
- **Node size:** a channel's total weighted score summed across all seeds. Channels with
  zero weight render at a fixed minimum size so they remain visible and hoverable.
- **Node fill:** the channel's avatar, served from the avatar cache, subject to the
  image-node threshold described under *Stack*.
- **Edges:** seed video → channel, weighted by `weight(c, s)`. Against current data, 355
  weighted edges.

The layout carries the finding: channels recommended across multiple seeds are pulled
toward the centre, channels appearing in a single seed settle on the fringe.

### Per-seed mode

A ranked layout, not a force layout. A single seed's graph is a star — one hub with every
edge topologically identical — and a force layout of a star communicates nothing.

Channels are ordered and sized by their weighted score within that one seed, with avatars
and labels visible, answering which channels dominate that specific sidebar.

This mode does not use Sigma. A ranked list is a layout problem, not a graph-rendering
one, and is drawn directly in the component. Both modes read the same query output.

### Collaborator rendering

Channels at position above 0 appear as nodes connected by **dashed edges at reduced
opacity**, carrying zero weight. Against current data that is 23 dashed edges.

This keeps two distinct realities visible without conflating them: a channel network
inflating one slot with many avatars reads as a recognisable dashed cluster, while a
legitimate collaborator that never uploads under its own name still appears in the graph
rather than vanishing from it.

### Labels and identity

Channel identity is `channel_id`. The display label is `handle`, falling back to `name`
when `handle` is absent. No `channel_id` in the current data carries conflicting names or
handles across rows, so no reconciliation is required.

### Interaction

Hovering a channel shows its per-seed breakdown: weighted score and unweighted credit
count for each seed it appears in.

## Out of scope

Not built in this specification:

- Any write to the database, including triggering collection
- Any disk write other than the avatar cache
- Charts and charting dependencies
- Access to `data/raw/` payloads
- The channel-to-channel graph mode
- Views other than the channel graph
- Production build or deployment
- Normalization of display-text columns — deferred until a view requires it
