# backend/scripts/

## check_playability.py

Measures real-world video playability across a sample of the library. Gets
ground truth from two independent sources per video — Telegram's
self-reported `DocumentAttributeVideo.video_codec` (via a direct Telegram
lookup) and `ffprobe` pointed at this app's own `/stream/{id}` HTTP endpoint
(via Range requests, so it costs a few MB per video, not a full download) —
and derives a Chrome/Edge-on-Windows playability verdict. Also checks whether
`moov` is at the front of the file (faststart) in the first 64KB, which feeds
preview-latency questions separately from playability.

This is a **measurement tool only**. It does not change how videos are
served, and it does not fix anything.

### Prerequisites

- The backend dev server must already be running and reachable (default
  `http://localhost:1864`, from the repo's documented dev port):
  ```
  cd backend
  uv run python main.py
  ```
- `ffprobe` (and `ffmpeg`) must be on PATH.
- `backend/session.session` must already be an authenticated Telegram
  session (first `uv run python main.py` run does the interactive login).
- The site password. Pass it via `--password` or the `TG_BACKEND_PW` env var
  — it is never hardcoded in this script.

### Run

From `backend/`:

```
uv run python scripts/check_playability.py --password <site password>
```

Default behavior samples 100 videos in a 40/40/20 mix:
- 40% spread evenly across the DWP (DirtyWrestlingPit) category's id range
- 40% the newest videos in the channel
- 20% spread evenly across offsets in the rest of the library (deduplicated
  against the other two buckets)

### Flags

- `--limit N` — total videos to sample (default 100). The 40/40/20 split
  scales with it.
- `--category <key>` — sample `--limit` videos from one category only
  (spread evenly across its id range), instead of the default 3-bucket mix.
  Category keys match `GET /api/categories` (e.g. `dwp`, `braz`, `kink-sas`).
- `--rest-only` — sample `--limit` videos from the library-spread "rest"
  bucket only (skips dwp/newest), excluding every id already present in
  `--output`. Use this to top up library-wide coverage across separate runs
  without re-touching buckets you've already measured.
- `--newest-only` — sample `--limit` videos from the newest bucket only.
- `--base-url URL` — backend base URL (default derived from `BACKEND_PORT`
  in the repo `.env`).
- `--output PATH` — results JSON path (default
  `.claude/.tmp/playability-results.json` at the repo root).
- `--timeout N` — per-video ffprobe timeout in seconds (default 60). One bad
  file cannot stall the run.
- `--force` — re-check ids that already have a record. Without it, ids
  already present in the output file are skipped, so re-running is cheap and
  the tool is safe to build up a larger sample over multiple runs.

### Output

Each run prints one line per video as it's checked, then a summary: codec /
verdict / faststart / codec-agreement distributions, a per-bucket verdict
breakdown, ffprobe latency (min/median/p90/max), and every failure with its
reason.

The "Selected N videos: ..." line at the start of a run shows actual/intended
counts per bucket (e.g. `rest=0/20`), and any bucket that comes up short
prints a loud `WARNING: bucket '<name>' only produced X/Y videos` — this is
what would have caught the 2026-08-23 run where the "rest" bucket silently
produced 0 videos while the run still finished and printed a summary as if
nothing were wrong. That original bug was `/api/videos`'s offset pagination
returning an empty page for any nonzero `offset` under the (then-new)
`sort=asc` default — Telegram's video search only honors `add_offset`
correctly in the descending direction, so `find_video_at_offset` and
`select_newest_bucket` now pass `sort=desc` explicitly rather than relying
on the route's default.

The full per-video records (Telegram ground truth, ffprobe ground truth,
verdict, faststart, timing) are written incrementally to the JSON output
file, so a run can be interrupted (Ctrl-C) without losing prior progress.

### How it avoids disturbing the app

- Every request against `/stream/{id}` uses `?preview=1`, so probing 100
  videos never steals the "last streamed video" playhead pin from a video a
  real user has open (see `streaming.py`).
- It still pulls real bytes through the block cache like any other stream
  request, so a full run does write a modest amount of new data into
  `backend/cache/` — bounded by how much of each file ffprobe needs to read
  its headers, not the whole file.

### Notes on implementation

- `GET /api/videos` (the same data the frontend uses) does not expose
  `video_codec` — Telethon's `msg.file` wrapper has no such property. This
  script opens its own short-lived Telegram connection, using a **copy** of
  `backend/session.session` (`.probe_session.session*`, gitignored, re-copied
  fresh on every run), specifically so it never contends with the running
  backend server for the same SQLite session file. It then walks
  `msg.document.attributes` directly for the raw `DocumentAttributeVideo`.
- Video selection goes through the real `/api/videos` / `/api/categories`
  HTTP endpoints (the same ones the frontend calls), not a direct Mongo or
  Telegram query, so the sampling exercises the real category-resolution
  logic in `categories.py`.

## import_playability.py

One-shot importer that reads a `check_playability.py` results JSON file and
bulk-upserts one doc per record into the `playability` MongoDB collection, so
verdicts can be queried without re-running the probe. It is a seeding tool,
not part of the served app — it does not change how videos are served.

Each doc is `{_id: <msg id>, verdict, video_codec, audio_codec, faststart,
updated_ts}`, where `video_codec`/`audio_codec` come from the record's
`ffprobe.video/audio.codec_name` and `updated_ts` is the import time (unix
seconds). A record missing a usable id or `verdict` is skipped and counted,
not imported.

### Run

From `backend/`:

```
uv run python scripts/import_playability.py
```

### Flags

- `--file PATH` — results JSON path (default
  `.claude/.tmp/playability-results.json` at the repo root, matching
  `check_playability.py`'s default `--output`).

### Output

Prints one summary line: `Imported <n>, skipped <n>`. Connects to the same
`MONGO_URI`/`DB_NAME` the backend uses (via `backend/config.py`), using
`pymongo`'s `AsyncMongoClient` directly rather than reusing `db.py`, so the
script has no dependency on the running server's connection lifecycle.
