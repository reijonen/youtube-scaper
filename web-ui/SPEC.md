# web-ui — Specification

A local, read-only viewer for the scraper's SQLite database. Its purpose is to make the
collected data legible as structure, not to reproduce it as tables.

This document describes what the web-ui **is**. Open work lives in the repository-root
`BACKLOG.md`; non-obvious choices already made live in `DECISIONS.md` next to this file.

## Scope

Three graph views over the same data, each answering a different question:

| View | Question | Status |
|---|---|---|
| **Seed → channel** | who is recommended how much, and in whose videos | built |
| **Directed channel → channel** | where does YouTube send a channel's viewers | `BL-007` |
| **Co-recommendation** | which channels does YouTube treat as substitutes | `BL-008` |

They share the query layer, the avatar cache, and the view registry. They do **not** share
a weighting model — see *Weighting models*, which is the part of this document most likely
to be got wrong.

## Preconditions

None. No view specified here depends on a normalized column — weights derive from
`raw_position` and row counts, both already stored as integers, and labels from text
columns consumed as-is.

Several columns hold numbers rendered as display text (`view_count_text` of `"274K"`,
`published_text` of `"1 year ago"`). Normalizing those is the scraper's responsibility, not
the web-ui's, and is deferred until a view requires it (`BL-013`). The web-ui never parses,
coerces, or repairs values it reads — it consumes columns as stored.

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

There is no production build target and no deployment. The application is run locally.

Sigma is chosen deliberately over an SVG approach rather than as a concession to current
data size. The corpus is intended to grow; building the layout on SVG now would mean
rebuilding it later. graphology also carries the analytical satellite libraries (metrics,
community detection) that the co-recommendation view will want, so the graph model is not a
dead end.

Two known limits shape the design:

- **Sigma has no built-in dashed edge type.** Verified against the installed `sigma` 3.0.3
  source, its changelog, and the npm registry. Achieving a real dash pattern would mean a
  hand-written WebGL edge shader. Edges that need to read as secondary are distinguished by
  **color and reduced opacity only**, using the default solid edge program.
- **Sigma degrades when every node carries an image**, well before it degrades on node
  count alone. Avatar-image nodes render only while the visible node count is small enough
  to afford them, falling back to plain colored circles beyond a threshold via a node
  reducer. The fallback is a rendering concern only — it changes no data and no weighting.

Queries return renderer-agnostic `{nodes, edges}` structures, so the query layer is never
coupled to Sigma. No charting library is added until a view requires one.

## Data access

The database path comes from `SCRAPER_DB_PATH`, defaulting to a path resolved relative to
the module's own location (not `process.cwd()`), so the web-ui makes no assumption about
where it sits in the tree or where `npm run dev` was invoked from.

The connection is opened **read-only**, with `fileMustExist: true`. The web-ui never
writes to the database, never migrates it, and never creates it. The database is in WAL
mode, so read-only access is safe while the scraper is running. Do not take locks and do
not assume exclusive access.

`fileMustExist` is not optional: without it `better-sqlite3` silently creates an empty
database at a mistyped path, and every view then renders an empty graph that looks like a
data problem rather than a configuration one.

Read-only refers to the database. The web-ui writes exactly one thing to disk: the avatar
cache described below.

### Table boundary

The web-ui reads only content tables:

- `recommendations`
- `recommendation_channels`
- `comments`

It never reads pipeline tables — `runs`, `raw_payloads`, `received_batches` — and from
`videos` reads only `video_id` and `status`, which identify which videos are seeds.

This boundary is why the database is not split into separate files: separation is enforced
at the query layer instead. Splitting was rejected on two grounds. The database is in WAL
mode, under which SQLite's cross-database transactions are atomic per file but not across
the set, weakening the scraper's crash-safety guarantees. And the scraper enforces foreign
keys (`PRAGMA foreign_keys = ON`), which SQLite cannot apply across attached databases.

`data/raw/` is not read. Raw payloads are cold recovery, not a data source.

### run_id

`run_id` is ignored entirely. Recommendations reconcile on
`UNIQUE (video_id, recommended_video_id)`, so a row's `run_id` records whichever run
happened to write it first and carries no meaning for display. No view filters, groups, or
labels by run.

## Repository boundary

The web-ui is a self-contained project inside a larger repository.

**Owned by this project — create and modify freely:** everything under `web-ui/`, plus
`web-ui/` entries in the repository `.gitignore`.

**Shared with the repository:** the root `CLAUDE.md` (conventions and standing
instructions) and `BACKLOG.md` (open work for both subjects). Read and write both.

**Read, never modify:** `src/scraper/storage/schema.sql` — the authoritative schema — and
`data/db.sqlite3`, opened read-only.

**Not guidance for this project:** the root `SPEC.md`, `PROTOCOL.md`, and `DECISIONS.md`
describe the scraper and its extension. They are readable, but they do not govern the
web-ui and applying their rules here would be a mistake. Likewise leave alone
`src/scraper/` (apart from the schema), `extension/`, `tests/`, `bin/`, `keys/`,
`data-dir-template/`, `data/raw/`, `run/`, and `.venv/`.

## Architecture

Each visualization is a self-contained folder under `src/lib/views/`, exporting a manifest:

```
{ id, title, question, load, component }
```

Views are discovered automatically via `import.meta.glob` and populate the navigation.
Adding a visualization means adding one folder — no routing changes, no registry edits. A
single generic `src/routes/[view]/` route matches on the manifest `id`.

Query functions live in `src/lib/server/queries/` and return renderer-agnostic data
structures. Rendering decisions belong to components, not queries.

`load` must wrap its query import in a **dynamic** `import()`. The registry is eagerly
globbed from `+layout.svelte`, which is client code, and SvelteKit's server-only-import
guard fires on a static import of anything under `$lib/server`. Type-only imports are
erased and are safe.

### Testing

Query functions are tested; components are not. The weighting arithmetic, the position
filtering, and the per-seed grouping are the parts that can be silently wrong while looking
plausible, and they all live in the query layer. Rendering errors are visible on screen.

Tests run against a small fixture database built in test setup — never against
`data/db.sqlite3`. The fixture builder applies `src/scraper/storage/schema.sql` verbatim to
a fresh `:memory:` database, so the test schema cannot drift from the scraper's.

Query functions take their connection as a required parameter rather than defaulting to the
app singleton: importing `db.ts` opens the real database as an import side effect, which
would make even fixture-based tests touch it.

### Source resolution

The "source" side of a graph edge is resolved through a single function mapping a seed
video to its graph node. Today it resolves a seed video to itself. The directed view
replaces this one function using a video-to-channel mapping held outside this database
(`BL-009`). No schema column is added for this.

## Avatar cache

Channel avatars are remote YouTube CDN URLs in
`recommendation_channels.avatar_sources_json`. Rendering directly against those URLs would
issue one request to Google per channel on every render, so avatars are fetched once and
cached on disk.

A server route serves an avatar by `channel_id`. On a hit it streams the stored file; on a
miss it fetches the recorded URL, writes it to the cache, and streams it. The client
references the route and never touches a CDN URL, so outbound requests are bounded by the
number of channels, once.

The cache lives under `web-ui/`, excluded from version control, and is disposable —
deleting it costs one refetch.

- **A `channel_id` arriving at the route is validated against a strict identifier pattern
  before it is used to build a filesystem path.** It comes from the URL; unvalidated, it is
  a path traversal.
- **Failures are never cached.** A transient network error must not become a permanently
  broken avatar that only a manual cache wipe fixes.
- Every channel has exactly one avatar source, at 68×68, but **36 of 378 source records
  carry no `width` field**. Read the URL; do not require the dimensions.
- The fallback, used when a channel has no usable avatar or the fetch fails, is a colored
  circle bearing the channel's first initial, its hue derived deterministically from the
  display label.

## Weighting models

The three views measure different things and **must not share a weighting scheme**. A
directed edge weights an observed event; a co-recommendation edge weights a statistical
association. One is a sum over observations, the other a ratio against a null model.

### Position weight (DCG)

Wherever position matters, the discount is:

```
1 / log₂(1 + raw_position)
```

Slot 1 scores 1.00, slot 2 scores 0.63, slot 10 scores 0.29, slot 100 scores 0.15 — steep
enough to express position bias, gentle enough that entries below the fold stay visible.

Weight uses `raw_position`, not `normalised_position`: the playlist and mix blocks filtered
out of the normalised value occupied real screen space and pushed later entries down, so
the raw value is the faithful attention proxy. `raw_position` is 1-based, so `log₂(1 + p)`
is safe — but it is one bad row away from dividing by zero. **Assert `raw_position >= 1`
rather than trusting it.**

### Channel credit within a recommendation

A recommendation credits its channels in `recommendation_channels.position` order. Position
0 is the main channel; positions above 0 are collaborators.

**Only the position-0 channel carries weight.** This matches where views and watch time
actually accrue on a collaboration upload, keeps total credits equal to the number of real
sidebar slots, and prevents a channel network from multiplying its apparent reach by
crediting many channels on a single slot. Collaborators are rendered but score zero.

**The unweighted credit count is always displayed alongside the weighted score, never
instead of it.** A score whose derivation is invisible is not interpretable.

### Directed edges (A → B)

An edge asserts *YouTube routes A's viewers toward B*. Asymmetric by nature.

The DCG weight transfers directly, but raw sums introduce a sampling artifact the
seed→channel view never had: **A's out-weight scales with how many of A's videos were
scraped.** Ten Numberphile seeds and one Veritasium seed gives Numberphile ten times the
raw out-weight — a fact about the scraping, not about YouTube. So normalize per source:

```
w(A→B) = (1 / |V_A|) · Σ         Σ                 1 / log₂(1 + raw_position(r))
                        v ∈ V_A   r ∈ sidebar(v)
                                  owner(r) = B
```

where `V_A` is the set of A's scraped videos. The edge then reads as *"on a typical A
video, B captures this much discounted sidebar attention"* — comparable across channels
sampled at different depths.

**Two normalizations, both available, switchable:**

- **Absolute** (the formula above) — preserves magnitude. Answers "how much".
- **Row-stochastic** — A's out-edges sum to 1, making the graph a transition matrix and
  legitimizing PageRank and random-walk analysis. Answers "where does a walker end up".
  Destroys magnitude, so it does not replace absolute.

Out-degree is a fact about the scraping; in-degree is a fact about YouTube. Channels seen
only as recommendations are structural leaves with zero out-degree because they were never
scraped, not because they recommend nobody. This asymmetry recedes as the frontier closes
but never fully disappears — say so in the UI rather than letting it read as a finding.

### Co-recommendation edges (X — Y)

An edge asserts *YouTube treats X and Y as interchangeable for the same viewer*. Symmetric
by nature: sharing a sidebar has no direction.

**Do not weight these by DCG.** Position measures attention, not similarity; weighting
co-occurrence by position would assert that channels near the top of a sidebar are more
alike than channels near the bottom, which is a category error. If anything the reverse
holds — deep slots are where YouTube has exhausted the obvious picks and is reaching into
the genuine topical neighbourhood.

The real problems here are different, and neither is solved by position weighting:

- **Combinatorial inflation.** A sidebar of *n* channels manufactures `C(n,2)` edges. Raw
  counts let the fattest sidebars write the graph.
- **Popularity conflation.** A channel in every sidebar co-occurs with everything. Raw
  co-occurrence cannot separate "similar to X" from "popular".

Both are addressed by measuring association over **seed-membership sets**. Let `S_X` be the
set of seeds in which X is recommended and `N` the seed count:

| Measure | Formula | Character |
|---|---|---|
| Jaccard | `\|S_X ∩ S_Y\| / \|S_X ∪ S_Y\|` | simple; harsh on rare channels |
| Cosine (Ochiai) | `\|S_X ∩ S_Y\| / √(\|S_X\|·\|S_Y\|)` | collaborative-filtering standard; stable |
| NPMI | `PMI / -log p(X,Y)`, `PMI = log( p(X,Y) / (p(X)·p(Y)) )` | discounts the null model directly; noisy at low N; bounded to [-1, 1] |

**Implement all three and make them switchable.** NPMI is the measure that actually answers
the clustering question, because it explicitly asks whether a pair co-occurs more than
popularity alone explains — but it needs N in the hundreds to behave, and is meaningless at
today's N. Cosine is the sane default until the corpus is deep. Picking one now and
deferring the others would mean rebuilding this when the data arrives.

All three are bounded, which is what makes a linear filter slider usable — see `BL-003`.

### Self-loops

A self-loop is A recommending its own other videos. It is real signal — YouTube's
in-channel retention behaviour — and it is already known to occur: three of the current
seed videos are identifiable as Numberphile *only* because they appear in each other's
sidebars.

**Model it as a node attribute, not an edge:**

```
selfShare(A) = w(A→A) / Σ  w(A→B)      over all B including A
```

The fraction of A's discounted sidebar attention that stays inside A. Excluded from the
edge set, encoded as a visual attribute (node ring, border weight, or fill saturation). It
is one number per node, so it is naturally a node property; drawn as a loop it would be
illegible, inflate PageRank, and distort random walks for no gain.

**The denominator rule, which is easy to get backwards and looks correct when wrong:** when
row-normalizing, the self-loop stays **in** the denominator even though its edge is not
drawn. Outward edges then sum to less than 1, and the deficit *is* the self-share — "40%
stays home, 60% leaves". Dropping the self-loop before normalizing inflates the outward
edges to sum to 1 and annihilates the retention signal, making a channel that retains 60%
of its attention indistinguishable from one that retains none.

What it licenses:

| Reading | Interpretation |
|---|---|
| High self-share | walled garden — YouTube treats the catalog as self-sufficient |
| Low self-share | gateway — viewers get dispersed outward |
| High in-degree + high self-share | dominant hub that also hoards |
| High in-degree + low self-share | distributor — receives attention and passes it on |

Self-share also predicts whether chain-scraping from a seed will explore or stall, which is
an independent read on `BL-011`.

Co-recommendation has no self-loop — X—X is meaningless. The analogous quantity is a
channel appearing several times in one sidebar, which is a multiplicity already captured as
the credit count.

## Views

### Seed → channel (built)

Route `/channels`. A force-directed bipartite graph rendered by Sigma with ForceAtlas2
layout in a web worker. ForceAtlas2 must run in its worker — on the main thread the UI
freezes during layout.

- **Nodes:** seed videos, plus every channel appearing in any seed's sidebar.
- **Seed nodes:** every video with `status = 'completed'`, including any that produced no
  recommendations. Such a video renders as an isolated node, visibly distinct from a video
  never collected — deliberately, since YouTube does not realistically serve a watch page
  with zero recommendations, so an isolated seed signals the video should be collected
  again.
- **Node size:** a channel's total weighted score summed across all seeds. Zero-weight
  channels render at a fixed minimum size so they stay visible and hoverable.
- **Node fill:** the channel's avatar, subject to the image-node threshold.
- **Edges:** **one edge per `(seed, channel)` pair**, never one per recommendation. The
  edge's weight is the DCG sum across every recommendation in that seed whose position-0
  channel is that channel; its credit count is the number of contributing recommendations.
- **Collaborator edges:** pairs credited only at `position > 0`, weight zero, rendered in a
  distinct color at reduced opacity.
- **A pair with any position-0 credit is a weighted edge, never a collaborator edge.** A
  channel can be the main channel on one recommendation and a collaborator on another
  within the same seed. Compute the weighted pair set first, then emit collaborator edges
  only for pairs not already in it. Getting this wrong yields parallel solid-and-faint edges
  between the same two nodes.
- **Channel label:** `handle`, falling back to `name`. Identity is `channel_id`. Graph node
  keys are prefixed (`seed:` / `channel:`) so a video id and a channel id can never collide
  into one node.
- **Interaction:** hovering a channel shows its per-seed breakdown — weighted score and
  unweighted credit count for each seed it appears in.

**This view is not a clustering instrument.** It is bipartite: channels have no adjacency
to each other, so a force layout has nothing to cluster them by. What it does show is that
channels recommended across multiple seeds are pulled toward the centre while single-seed
channels settle on the fringe. Questions about who clusters with whom belong to the
co-recommendation view.

### Directed channel → channel (`BL-007`)

Nodes are channels only; no video nodes. A→B when a video owned by A recommends a video
owned by B. Weighting and self-loop handling as specified above. Requires the seed→owner
mapping (`BL-009`).

Note before building: at today's four seeds this view is **topologically identical to the
seed→channel view** — four hubs and their leaves, with the hubs relabelled from video ids to
channel names. It becomes a network only once channels seen as recommendations are
themselves scraped and the loop closes. That is expected, not a failure of the
implementation.

### Co-recommendation (`BL-008`)

Nodes are channels only. X—Y when both appear in the same seed's sidebar. Buildable from
the database today with no external data. Community detection over this graph is the direct
answer to which channels cluster together.

Note before building: at four seeds this is **a union of four cliques**, and 90% of its
edges are single-seed co-occurrences carrying no information. Filtering to pairs sharing
two or more seeds leaves 789 edges over 44 channels at 83% density — smaller, still a
hairball. Meaningful clustering needs a substantially deeper corpus. Build it correctly now
and it becomes useful as the corpus grows; do not tune it against today's shape.

## Measured data

Measured 2026-07-29. These are checks on a correct implementation, not targets — the
database is expected to grow, and these numbers with it. Re-measure rather than assuming.

| Quantity | Value |
|---|---|
| Seed videos (`status = 'completed'`) | 4 |
| Distinct channel nodes | 212 |
| Channels carrying weight | 197 |
| Collaborator-only channels (zero weight) | 15 |
| Recommendations, each with exactly one position-0 channel | 355 |
| **Weighted edges** — distinct `(seed, channel)` pairs | **263** |
| …credited by more than one recommendation | 27 |
| Collaborator credits (`position > 0`) | 23 |
| …falling on pairs that already have a position-0 credit | 5 |
| **Collaborator edges** — pairs, overlap excluded | **18** |
| Total edges of any kind | 281 |
| `raw_position` range | 1–146 |

355 is the number of weighted **credits**; 263 is the number of **edges** those credits
collapse into. Both are displayed; only 263 are drawn.

Sidebar sizes per seed: 73 (`1cvKGqgOx_8`), 80 (`JEPqrqNqkHw`), 66 (`cOTf_YEmSOU`), 44
(`dQw4w9WgXcQ`).

Channel reach: 153 channels appear in exactly one seed, 22 in two, 22 in three, none in all
four.

Co-recommendation shape: 7,859 distinct undirected pairs, of which 7,070 share exactly one
seed, 558 share two, and 231 share three.

Heaviest edges, for spot-checking: Numberphile in `JEPqrqNqkHw` (23 credits, ≈ 4.724),
Numberphile in `1cvKGqgOx_8` (20, ≈ 4.669), Numberphile in `cOTf_YEmSOU` (18, ≈ 3.945),
Classic 80s Mix in `dQw4w9WgXcQ` (7, ≈ 1.559).

Three multi-channel recommendations exist and are recognisable as channel farms: a
nine-channel cluster ("No Fluff …"), a three-channel music cluster, and a four-channel
shorts cluster. Two collaborator-only channels — Dr. Becky and Philosophical Instrumentals
— are legitimate rather than network padding and must stay visible.

## Conventions

TypeScript settings match the repository's existing ones, taken from `extension/`: ES2022
target, ESM, `strict: true`, `noUncheckedIndexedAccess: true`, npm with a committed
`package-lock.json`. `tsconfig.json` extends SvelteKit's generated base, which sets
`target: "esnext"` and omits `noUncheckedIndexedAccess` — both are overridden explicitly.

Server-only code lives under `src/lib/server/` so SvelteKit refuses to bundle it into the
client. `better-sqlite3` is a native module and must compile against the local Node.

Environment as verified 2026-07-29: Node 25.8.2, npm 11.11.1.

## Out of scope

- Any write to the database, including triggering collection
- Any disk write other than the avatar cache
- Charts and charting dependencies
- Access to `data/raw/` payloads
- Views other than the three above
- Production build or deployment
- Normalization of display-text columns (`BL-013`)
- The per-seed ranked mode — dropped 2026-07-29, removal tracked as `BL-006`
