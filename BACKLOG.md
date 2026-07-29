# Backlog

Open work across both subjects. The only place open work lives — `SPEC.md` describes what
is, `DECISIONS.md` records why, and neither carries todos.

Ids are stable and never reused. Reference them in commits and when handing work to
another session. Remove an item when it lands or is explicitly dropped; do not renumber.

Last reviewed 2026-07-29.

## web-ui — graph legibility

The graph is currently hard to read, and that blocks everything else: a hairball you can't
navigate makes weighting changes unfalsifiable, because you can't see whether they worked.
These five come before new views.

**BL-001 — Node labels and avatars overlap.** At 216 nodes the labels collide with each
other and with the avatar fills, so most of the graph is unreadable. Needs some combination
of label culling by zoom level, collision-aware label placement, and only labelling nodes
above a weight threshold. Sigma exposes label rendering through its node reducer.
→ `web-ui/src/lib/views/channels/TotalModeGraph.svelte`

**BL-002 — No indication of the graph's extent.** There is nothing showing where the graph
begins or ends, or that content exists outside the current viewport. Needs at minimum a
fit-to-view control and an off-screen-content indicator; a minimap would cover both.
→ `web-ui/src/lib/views/channels/TotalModeGraph.svelte`

**BL-003 — Weight filtering with a sensible default floor and a slider.** Nothing is
filtered today, so every low-weight edge renders. Needs a default floor that hides the long
tail on load, plus a slider to move it. Note the scale interacts with the weighting model:
DCG sums are unbounded and skewed (currently 0.1–4.7) so a linear slider over them is
unusable and needs log or percentile scaling, whereas cosine and NPMI are bounded and take
a linear slider directly. Settle BL-008's measure before designing the control.

**BL-004 — Page header takes roughly a third of the vertical space.** The title, question,
and counts block on `/channels` crowds out the graph. Compress to a minimal horizontal
header.
→ `web-ui/src/lib/views/channels/ChannelsView.svelte`, `web-ui/src/routes/[view]/+page.svelte`

**BL-005 — Click a node to see what it connects to.** Currently the only way to trace a
node's edges is to follow them by eye through the tangle. Clicking should isolate the
node's neighbourhood — dim everything else, keep its edges and neighbours at full opacity.
Cheap in Sigma via node and edge reducers, and it substitutes for a lot of aggressive
filtering.

## web-ui — views

**BL-006 — Remove the per-seed ranked mode.** Decided 2026-07-29: only one view was wanted
initially, and per-seed was not it. Its question is better answered by BL-005 once that
lands. Delete `PerSeedRanked.svelte`, the mode toggle, and the `selectedSeedId` state;
keep `breakdown.ts`, which the hover tooltip still needs.
→ `web-ui/src/lib/views/channels/ChannelsView.svelte`, `PerSeedRanked.svelte`

**BL-007 — Directed channel→channel view.** New view. A→B when a video owned by A
recommends a video owned by B. Weighting and self-loop handling are specified in
`web-ui/SPEC.md`. Blocked on BL-009.

**BL-008 — Co-recommendation view.** New view. X—Y when both are recommended in the same
seed's sidebar. Buildable from the database today, no external data needed. Three
similarity measures are specified — implement all three and make them switchable rather
than picking one. See `web-ui/SPEC.md`.

**BL-009 — Ingest the seed video → owner channel mapping.** BL-007 cannot exist without
it; the `videos` table has no channel column and only three of six seeds are inferable
from cross-recommendation. The mapping is supplied by the repository owner from outside
this database. **Open question: where it lives.** An earlier decision was that no
seed-channel column would be added to the scraper schema; if that still holds it needs a
separate file or table that the web-ui reads. Confirm before building.

## scraper

**BL-010 — Recommendations are geographically wrong.** Finnish channels appear in
recommendations; results should be US-based. Chrome is launched against a fresh
cookie-less profile every video, so YouTube has no profile to personalise against and
falls back to something location-derived. Investigate pinning locale and region —
`--lang`, an explicit `hl`/`gl`, or Accept-Language on the profile template.
→ `src/scraper/chrome/process.py`, `data-dir-template/`

**BL-011 — Test whether deep recommendation scraping is worth doing.** Hypothesis from
BL-010: because every run starts cookie-less, YouTube has nothing to personalise with and
its recommendations degrade into generic filler past some depth. If true, collecting deep
into the sidebar buys noise, and `--max-recommendations` should default lower. Needs a
real measurement, not a guess — compare recommendation quality by depth across several
videos. The self-share metric from BL-007 is a second, independent read on the same
question: a high-self-share seed will walk chain-scraping around inside one catalog.

**BL-012 — `duration_text` and `animated_preview_sources_json` are empty on every
recommendation.** As of 2026-07-29 all 355 stored recommendations have an empty
`duration_text` and an empty `animated_preview_sources_json` (`[]`), across all four seed
videos that produced recommendations. Every other field populates normally — `title` is
355/355, `view_count_text` is 338/355 — so this is specific to these two fields, not a
general extraction failure. Both are parsed in the parser
(`RawRecommendation.duration_text`, `RawRecommendation.animated_preview_sources`) and both
columns are written, so the fault is in extraction rather than storage. Two candidates:
the lockup shapes these are read from have moved in YouTube's payload, or they only appear
under conditions the current run never produces (hover state, for animated previews). No
consumer today — revisit before anything depends on duration or preview data.

**BL-013 — Normalize display-text columns.** `view_count_text` holds `"274K"`,
`published_text` holds `"1 year ago"`. Normalizing is the scraper's job, not the web-ui's,
and is deliberately deferred until a view needs a real number. Keep the raw text columns
when this lands — they are what makes the normalizer auditable, and 17 rows already have an
empty `view_count_text`, which a numeric column alone could not distinguish from a parser
failure.

**BL-014 — Four of twelve error codes have no detection.** `CONSENT_WALL`,
`AGE_RESTRICTED`, `LOGIN_REQUIRED`, and `VIDEO_UNAVAILABLE` are defined in `PROTOCOL.md`
but never emitted. Detecting them correctly needs a verified `playabilityStatus` value from
a real capture of each state, and `tests/captures/` has only an ordinary video, a finished
live stream, and a premiere. These states still fail loudly today, just under the less
specific `PAGE_READY_TIMEOUT` or `SCHEMA_UNRECOGNISED`. Revisit if a capture of one is
obtained.
→ `extension/src/service-worker.ts`

**BL-015 — The bridge does not notice Chrome closing stdin during reconnect backoff.** It
only checks for stdin EOF while actively forwarding; in the disconnected retry loop it
sleeps and retries. There is no portable non-consuming peek for pipes, so detecting this
needs OS-specific work. Low priority: Chrome signals the native host process directly
rather than relying on stdin EOF alone.
→ `src/scraper/native_host.py`, `Bridge.run`

**BL-016 — `CommentCollectionFailure` is reported as `SCHEMA_UNRECOGNISED`.** No error code
covers "comment header present with an implausible zero-thread count", and
`SCHEMA_UNRECOGNISED` is the closest fit. Add a specific code if this case turns out to
matter.
→ `src/scraper/controller/video_session.py`, `handle_video_done`

## Documentation

**BL-017 — Update stale document references in code comments.** 85 docstrings and comments
across `src/` and `extension/src/` refer to "SPEC-V3", which is now `SPEC.md`; those
pointing at framing, message types, protocol invariants, or error codes are doubly stale
because that content moved to `PROTOCOL.md`. A further 16 reference `PLAN.md`, which no
longer exists — those should point at `SPEC.md`, `DECISIONS.md`, or this file depending on
what they were citing. None are path references so nothing is broken at runtime; this is
navigation rot for future sessions. Mechanical but needs judgement per site, so not a blind
sed.
