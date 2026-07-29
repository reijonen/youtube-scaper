# YouTube Scraper

Collects YouTube sidebar recommendations and comment counts/threads for a list of video
IDs, storing results in a local SQLite database.

## Requirements

- macOS, with Google Chrome installed.
- Python 3.14+ and [uv](https://docs.astral.sh/uv/).
- Node.js (only needed once, to build the browser extension).

## One-time setup

1. Install dependencies:

   ```bash
   uv sync
   ```

2. Build the browser extension:

   ```bash
   cd extension && npm install && npm run build && cd ..
   ```

3. Create the golden Chrome profile template. Launch Chrome manually against
   `data-dir-template`, then in that window:
   - Do not sign in. Do not enable Chrome Sync.
   - Install [I still don't care about cookies](https://chromewebstore.google.com/detail/i-still-dont-care-about-c/edibdbjcniadpccecjdfdjjppcpchdlm)
     from the Chrome Web Store.
   - Enable Developer mode at `chrome://extensions`, then Load unpacked → select the
     `extension/` directory.
   - Confirm the loaded extension's ID matches the one pinned in
     `extension/manifest.json`.
   - Clear all browsing data (all time).
   - Quit Chrome completely.

   This template is reused (copied fresh) for every video collected. You only redo this
   step if you want to reset the template or update the extension/cookie-consent add-on.

## Usage

```bash
uv run python -m scraper controller --video-ids <id1,id2,id3>
```

Video IDs are the `v=` parameter from a YouTube watch URL, comma-separated, no spaces.

Chrome will open automatically for each video in turn, run headed (not headless), and
close when that video is done. A video that was already collected successfully in a
previous run is skipped automatically — rerunning the same IDs is safe and cheap.

Results land in `data/db.sqlite3` (video metadata, recommendations, comments) and
`data/raw/` (raw captured payloads).

### Options

| Flag | Default | Meaning |
|---|---|---|
| `--max-recommendations` | 100 | Stored recommendations per video, after filtering to videos (playlists/mixes excluded). |
| `--wait-for-comments` | off | Keep collecting past the recommendation target until the comment count is resolved (a real count, or at least one thread seen). Slower but more complete. |
| `--max-scroll-rounds` | 20 | Cap on scroll attempts per video before giving up. The default is deliberately conservative; raise it only for videos you're confident will keep paginating normally. |
| `--inter-video-delay-ms` | 5000 | Idle pause between videos. |
| `--scroll-delay-ms` | 1000 | Dwell time between scroll actions within a page. |
| `--delay-jitter` | 0.25 | Fraction of each delay applied as random jitter, to avoid a mechanically regular pace. |

Run `uv run python -m scraper controller --help` for the full list.

### Example

```bash
uv run python -m scraper controller --video-ids dQw4w9WgXcQ --max-recommendations 50 --wait-for-comments
```

## Notes

- Only one collection run can proceed at a time (a second run against the same database
  fails fast with a clear error rather than corrupting data).
- Interrupting a run (Ctrl-C) is safe: the video in progress is marked failed, everything
  already recorded stays recorded, and a rerun picks up where it left off.
