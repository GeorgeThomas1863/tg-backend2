# Video Streaming Performance — Design

**Date:** 2026-07-13
**Goal:** Fast playback start, no mid-play buffering, responsive seeking — for a 35k+ video Telegram channel, streamed through the FastAPI backend to the React frontend.

## Measured baseline (2026-07-13, local dev)

| Metric | Value |
|---|---|
| Time to first byte on `/stream` | 0.26 s |
| Sustained Telegram → backend throughput | 0.73 MB/s (single MTProto connection) |
| First MB after a 200 MB seek | 4.6 s |
| Typical video bitrate | 0.65–0.8 MB/s (5–6 Mbit/s, 720p) |

Root cause of all three pain points: throughput ≈ 1.0× consumption rate. Telegram
throttles per connection; the app uses one connection, sequentially.

## Constraints (confirmed with user)

- Runs locally now; VPS deployment later. Design must work for both.
- Disk caching of video bytes is allowed (the old "never touch disk" rule is dropped;
  CLAUDE.md must be updated as part of this work).
- Library is 35k+ videos — full mirror impossible; cache must be size-capped.
- 1–2 concurrent users; single backend process; single Telethon session stays.
- All code follows /cc clean-code principles; built test-first (TDD).
- No git commits by Claude; user owns commits.

## Architecture overview

Four phases, each independently shippable and measurable. Backend stays split by
concern: `main.py` HTTP only, `telegram.py` Telegram client ownership, plus two new
single-purpose modules.

```
Browser <video> (Range requests)
   │
main.py /stream ── parse_range (unchanged)
   │
streaming.py  stream_range(msg, start, end)      ← NEW orchestrator
   │   for each 4 MiB block in range:
   │     cache hit  → yield slice from disk
   │     cache miss → fetch block via downloader → write cache → yield slice
   │   readahead: keep next N blocks downloading in background
   │
cache.py      block cache on disk, LRU-capped     ← NEW
downloader.py parallel MTProto block fetch        ← NEW
telegram.py   client, message resolution (+TTL cache), thumbs
```

## Phase 1 — Parallel download engine (`downloader.py`)

The throughput fix. Telegram throttles per connection, so we download each block
over a pool of MTProto senders on the file's DC, striped in 1 MiB requests, and
reassemble in order.

- Pool of `TG_CONNECTIONS` (env, default 4) senders created lazily from the existing
  session via exported authorization (the FastTelethon technique). One shared pool
  for the whole process; block fetches queue onto it.
- Public API: `download_block(msg, block_idx) -> bytes | None` (query shape: data or
  null) plus `fetch_range_streaming(msg, start, end)` used by Phase 1 as a drop-in
  replacement for today's `iter_download` loop before the cache exists.
- Telegram API constraints honored: offsets 4096-aligned, request limit ≤ 1 MiB and
  a multiple of 4096. Both verified against the installed Telethon (≥1.44) during
  implementation — the exact sender-export API is internal to Telethon and must be
  pinned down from its source, not memory.
- `FileReferenceExpiredError` mid-download: invalidate the message cache entry,
  re-resolve once, retry the request; second failure logs and aborts the block.
- Acceptance: benchmark script (curl, same video as baseline) shows sustained
  ≥ 3 MB/s — 4× headroom over bitrate. Measured before/after and recorded in the doc.

## Phase 2 — Disk block cache + readahead (`cache.py`, `streaming.py`)

Stops paying Telegram twice and absorbs jitter.

**Block cache (`cache.py`)**
- Fixed 4 MiB blocks (constant, multiple of 4096 and of the 1 MiB stripe), keyed
  `(msg_id, block_idx)`. Layout: `CACHE_DIR/blocks/{msg_id}/{block_idx}.blk`.
- Final block of a file is naturally short; blocks are only ever written whole
  (atomic: write temp file, rename).
- LRU by file mtime: reads `touch` the block; eviction deletes oldest-mtime blocks
  until total ≤ `CACHE_MAX_GB` (env, default 20). Total size tracked with a running
  counter initialized by a startup scan.
- Cache failures (disk full, IO error) log and degrade to direct streaming — the
  cache is an optimization, never a correctness dependency.

**Streaming orchestrator (`streaming.py`)**
- `stream_range(msg, start, end)`: async generator. Maps the byte range to blocks;
  per block: cache read, else `download_block` + cache write; yields exactly the
  requested slice (the existing align/trim math generalizes to block boundaries —
  current tests carry over).
- Per-block asyncio locks so concurrent requests (or readahead) never download the
  same block twice.
- **Readahead:** after yielding block *i*, ensures blocks *i+1 … i+R*
  (`READAHEAD_BLOCKS` constant, default 8 → 32 MiB) are cached or being fetched, as
  background tasks deduplicated by the same per-block locks. Stream teardown stops
  scheduling new readahead; in-flight blocks finish into the cache. A global cap
  bounds concurrent block fetches so readahead never starves the live stream.
- `main.py` switches its `telegram.stream_range` call to `streaming.stream_range`;
  Range parsing and headers unchanged.

## Phase 3 — Small caches (`telegram.py`)

- **Message resolution cache:** `{msg_id: (message, fetched_at)}` with 5-minute TTL.
  Removes the per-request `get_messages` round trip (0.26 s locally, more on VPS —
  and browsers fire several Range requests per playback start). Invalidated on
  `FileReferenceExpiredError` (Phase 1 hook).
- **Thumbnail disk cache:** `CACHE_DIR/thumbs/{msg_id}.jpg`, written on first fetch,
  served from disk after. No eviction (KB-scale files, bounded by library size).
  `/thumb` responses gain `Cache-Control: private, max-age=86400` so the browser
  stops re-requesting them too.

## Phase 4 — Pagination (backend param + frontend infinite scroll)

- `/api/videos?limit=50&before_id=<msg_id>`: `before_id` maps to Telethon's
  `offset_id` (messages strictly older than that id). Response stays a bare array;
  the client treats `length < limit` as end-of-list.
- `useVideos` gains `loadMore()`, `hasMore`, `loadingMore`, appending pages to the
  existing list state. Initial fetch unchanged.
- `App.jsx` renders a sentinel `<div>` after the rows; an `IntersectionObserver`
  fires `loadMore` when it scrolls into view. Accordion behavior unchanged.

## Configuration additions (`config.py` / `.env`)

| Var | Default | Meaning |
|---|---|---|
| `TG_CONNECTIONS` | 4 | parallel MTProto senders in the pool |
| `CACHE_DIR` | `backend/cache` | root for block + thumb caches (gitignored) |
| `CACHE_MAX_GB` | 20 | block-cache size cap, LRU eviction |

Constants (not env): `BLOCK_SIZE = 4 MiB`, `READAHEAD_BLOCKS = 8`,
`MSG_CACHE_TTL = 300 s`. No speculative knobs.

## Error handling

- All Telegram/disk calls wrapped in try/except with contextual logging (existing
  `report_error` pattern).
- Mid-stream failures: log and stop the generator (headers already sent — same as
  today).
- Downloader pool connection failures: log, drop the sender, continue on the
  remaining pool; a pool of zero falls back to the existing single-connection
  `iter_download` path.

## Testing (TDD — tests written first per component)

Backend (`pytest`, extends existing 47-test suite; Telethon fully mocked):
- Block math: byte range → block indices + slice offsets (property-style edge cases:
  range inside one block, spanning blocks, exact boundaries, final short block).
- Cache: write/read round-trip, atomicity (no partial blocks visible), touch-on-read,
  LRU eviction to cap, corrupted/missing file → miss, IO error → degrade not crash.
- Downloader: striping/reassembly order with mocked senders, file-reference retry
  path, pool-exhaustion fallback.
- Streaming orchestrator: serves mixed hit/miss ranges byte-exactly, dedupes
  concurrent block fetches, schedules readahead, teardown stops new scheduling.
- Routes: pagination params pass through; `/thumb` cache header present.

Frontend (`vitest`, extends existing 30-test suite):
- `useVideos`: loadMore appends, hasMore flips false on short page, no duplicate
  fetches while loading.
- Sentinel/observer wiring in `App` (observer mocked).

Acceptance (manual, scripted curl — same methodology as baseline):
- Sustained ≥ 3 MB/s on the baseline video (Phase 1).
- Re-play of a watched region: served from cache, no Telegram traffic (Phase 2).
- Seek into cached region resumes near-instantly (Phase 2).
- List scrolls past 50 items smoothly (Phase 4).

## Out of scope (deliberately)

- HLS/adaptive bitrate, transcoding (revisit only if VPS-era networks demand it).
- Hover-prefetch of unwatched videos (readahead + parallel speed cover start time).
- Search/filtering over the 35k library (separate feature).
- Multi-process scaling (single process stays; 1–2 users).

## Documentation updates

- CLAUDE.md: replace the "video bytes never touch local disk" architecture note with
  the block-cache design; document new env vars and the cache directory; add
  `backend/cache/` to `.gitignore`.
