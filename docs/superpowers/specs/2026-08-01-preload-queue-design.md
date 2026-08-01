# Preload Queue — Design

**Date:** 2026-08-01
**Status:** Approved pending user review of this document

## Problem

Telegram caps this account at ~0.7 MB/s aggregate. Parallelism cannot raise it, and
typical video bitrates sit near that cap, so live streaming can never build a buffer.
Today's readahead stops 32 MB past the playhead and only advances while the browser is
actively pulling bytes — pausing does not build buffer, and nothing downloads when the
app is idle.

The fix is to download **earlier**, not faster: fill the disk block cache during the
hours nothing is being watched, and download the whole open video through pauses.

## Goal

A single background download worker with three priority tiers:

- **Tier 0 — live miss (preempt):** a viewer waiting on an uncached block always gets
  the full bandwidth.
- **Tier 1 — open video ("pin"):** the last-streamed video downloads in full,
  playhead→end then wrap to 0→playhead, surviving pauses and closed connections.
- **Tier 2 — library prewarm:** newest-first, whole files, until the cache cap is
  reached; rescans periodically for new uploads.

Non-goals (out of scope): head-only prewarm for instant starts, per-row manual
download buttons, bandwidth shaping, multi-account pooling, persisting the pin across
backend restarts.

## Architecture

One new module, `backend/prefetch.py`. Import graph stays acyclic:

```
main → streaming → prefetch → {cache, downloader, telegram, config}
```

To achieve that, `get_block` and its per-key lock map **move from `streaming.py` into
`prefetch.py`** — block acquisition (cache check, dedupe lock, download, cache write)
lives beside the scheduler that shares it. `streaming.py` keeps only the HTTP-range
concerns: `stream_range` and `plan_blocks`.

The worker is **serial**: one block download at a time. The bandwidth cap is
account-wide, so concurrent block downloads only split it; each block already stripes
across the sender pool at full speed. Serial also makes preemption trivial.

## Components

### `prefetch.py` (new)

Public surface:

- `async start()` — create the worker task. Called from `main.py` lifespan startup.
- `async stop()` — cancel the worker and its in-flight download, await cleanup.
  Called from lifespan shutdown, before `downloader.disconnect_all()`.
- `note_playhead(msg_id: int, block_idx: int)` — called by `streaming.stream_range`
  once per block served. Sets/updates the pin. Replaces `schedule_readahead` entirely.
- `async get_block(msg, idx, urgent: bool) -> bytes | None` — moved from
  `streaming.py`. Cache-first, per-key `asyncio.Lock` dedupe, download on miss, cache
  write. `urgent=True` (streaming's path) additionally announces the miss for the
  duration of its download (tier-0 signal). `urgent=False` is the worker's own path.

Internal behavior:

- **Worker loop:** wait until no urgent download is announced → pick the next job →
  run it as a cancellable task → repeat. A failed block logs and is skipped; a video
  whose message no longer resolves is skipped. Failures never propagate; the worker
  never dies (top-level try/except per iteration, logged).
- **Job selection (pure function, unit-testable):**
  1. If a pin exists: the first uncached block index in the order
     `playhead..last_block, 0..playhead-1`. Selection re-reads the current playhead
     every pick, so seeks self-heal without iterator state. All blocks cached → pin
     is complete, fall through.
  2. Else, tier 2 (only if `PREWARM_ENABLED`): next uncached block of the current
     prewarm video, videos enumerated newest-first.
  3. Nothing to do → sleep `PREWARM_RESCAN_SECONDS`, then re-enumerate from newest.
- **Preemption protocol:** while the worker's download is in flight, if an urgent
  miss is announced for a **different** block, cancel the worker's task (partial
  bytes discarded — the block will be re-picked later) and wait until no urgent
  download remains. If the urgent miss is for the **same** block, do not cancel: the
  urgent caller blocks on the per-key lock and receives the finished block (shared
  fetch). Multiple simultaneous urgent misses (two viewers) are a set; the worker
  waits until the set is empty.
- **Prewarm enumeration:** page through `telegram.list_videos(limit=50,
  before_id=cursor)` newest-first until a short page. For each video: resolve via
  `telegram.get_message(id)` (skip if gone); build its uncached block list via
  `cache.has_block`; compute `remaining = video.size − cached bytes`. **Stop-at-cap:**
  if `cache.current_total() + remaining > cache.MAX_BYTES`, end the prewarm pass —
  downloading past the cap would LRU-evict the newest prewarmed blocks (churn).
  After a pass ends (cap or exhausted), sleep `PREWARM_RESCAN_SECONDS` and restart
  from newest, so new uploads are picked up.
- **Pin lifetime:** the pin is the last `note_playhead` video; a new video replaces
  it (accordion UI ⇒ one open video). It survives until replaced or the process
  exits. No expiry.
- **Oversized pin:** if the pinned video's file size exceeds `cache.MAX_BYTES`,
  tier 1 treats it as complete (log once, skip). Completing it is impossible — its
  own later writes would evict its earlier blocks in an endless churn loop. Tier 0
  still serves it, so it streams exactly like today.

### `streaming.py` (modified)

- `stream_range` calls `prefetch.get_block(msg, idx, urgent=True)` and
  `prefetch.note_playhead(msg.id, plan.idx)` per block. The fallback to
  `telegram.stream_range` on a failed block is unchanged — the cache layer still
  never makes playback worse.
- Delete: `get_block` (moved), `schedule_readahead`, `fetch_ahead`, `prune_locks`
  (moves with the lock map), `_block_locks`, `_inflight`, `_readahead_tasks`,
  `_readahead_limit`.

### `main.py` (modified)

Lifespan: `await prefetch.start()` after `telegram.connect()`; `await
prefetch.stop()` first in shutdown, before `downloader.disconnect_all()`. No route
changes. No frontend changes.

### `config.py` (modified)

- `CACHE_MAX_GB`: default changes `"20"` → `"100"`. Still overridable via repo-root
  `.env` (existing mechanism, no new code).
- `PREWARM_ENABLED`: new env var, default on. `"0"` disables **tier 2 only** (dev
  kill-switch); tiers 0/1 always run. Parse: `os.environ.get("PREWARM_ENABLED",
  "1") != "0"`.
- `PREWARM_RESCAN_SECONDS = 600`: new constant (not env) — idle sleep between
  prewarm passes.
- `READAHEAD_BLOCKS`: deleted with the readahead machinery.

### `CLAUDE.md` (modified)

The Architecture section describes `streaming.py`'s readahead and `READAHEAD_BLOCKS`;
update it to describe `prefetch.py` and the tiered worker.

### Unchanged

`cache.py`, `downloader.py`, `telegram.py`, `rate_limit.py`, all frontend code.
Eviction stays pure mtime-LRU with no pin protection. The protection this gives the
active video is per-block, not whole-video (reads touch only the blocks actually
read), but combined with stop-at-cap prewarm and the oversized-pin rule it is
sufficient: past the cap, the only new writes come from the watched video itself,
and those evict the oldest prewarmed blocks first. Known accepted quirks:
`cache._total_bytes` can drift slightly high when a rare double-download rewrites an
existing block (conservative direction — evicts early, never late), and `has_block`
trusts the atomic-complete-block write invariant. `file_reference` expiry during long
prewarms is already handled inside `downloader.fetch_stripe` (refresh + retry once).

## Data flow (steady state)

1. Browser sends a Range request → `stream_range` walks blocks → `get_block(urgent=True)`
   serves each from disk or downloads it (announcing the miss, preempting the worker).
2. Each served block updates the pin via `note_playhead`.
3. The worker, whenever no urgent miss is announced, downloads the pinned video's
   remaining blocks; when the pin is fully cached, it prewarms the library newest-first
   until the cap; then it idles on a rescan timer.

## Error handling

- Worker: per-iteration try/except; log with block/video context (house style:
  `print` with a module-tag prefix); skip and continue. The worker task must never
  terminate on an error.
- `get_block` failure → `None` → streaming falls back to single-connection
  `telegram.stream_range` for the remainder (existing behavior). This includes
  downloader *exceptions*, not just `None` returns — `resolve_location` and pool
  setup can raise outside `download_block`'s own handling — so `get_block` wraps
  the download in try/except and returns `None`, letting only `CancelledError`
  propagate (preemption/shutdown).
- Prewarm treats `telegram.list_videos` returning `None` (Telegram failure) as a
  failed pass — log and retry after `PREWARM_RESCAN_SECONDS` — distinct from a
  short page, which is genuine end-of-list.
- Cancellation (preemption and shutdown) is not an error; partial downloads are
  discarded, locks release via `async with`.
- Cache failures already degrade to misses inside `cache.py`.

## Testing

Backend pytest (extends the existing suite; all 47 existing tests stay green,
readahead-specific tests are replaced):

- **Pure:** pin job selection — playhead-first order, wraparound, skip-cached,
  complete-pin fallthrough; stop-at-cap arithmetic including partially cached videos.
- **Async (fake downloader/telegram):** urgent miss pauses the worker; urgent miss
  cancels a different-key in-flight job; same-key urgent miss shares the fetch via
  the lock instead of cancelling; prewarm enumerates newest-first and stops at cap;
  `PREWARM_ENABLED=0` skips tier 2 but tier 1 still runs; `stop()` cancels cleanly.
- **Streaming:** `stream_range` calls `note_playhead` per block and still falls back
  to `telegram.stream_range` when `get_block` returns `None`.

## Risks / open items

- Sustained 24/7 downloading at the cap: unverified whether Telegram applies extra
  throttling beyond the existing ~0.7 MB/s. Telethon surfaces FloodWait if it comes;
  watch logs after shipping.
- Preemption discards up to ~4 MB (one block) per cold seek. Accepted: seek latency
  beats wasted bytes.
