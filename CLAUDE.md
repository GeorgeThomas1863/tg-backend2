# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A React frontend that browses and plays videos stored in a Telegram channel, streamed on-demand through a FastAPI backend. The backend converts HTTP Range requests into Telegram's chunked download protocol so the browser's native `<video>` seeking works: urgent cache misses download over a pool of parallel MTProto connections, while a tiered background worker completes the open video through pauses, downloads the videos currently on the user's screen in display order, and prewarms the newest library videos into an LRU-capped disk block cache (`backend/cache/`, gitignored) when idle.

## Commands

Backend (from `backend/`, using `uv`):
- Install deps: `uv sync`
- Run dev server: `uv run python main.py` (reload enabled; listens on `BACKEND_PORT`. `config.py` loads the repo-root `.env` itself via `python-dotenv`, so no `--env-file` flag is needed)
- First run opens an interactive Telethon login prompt (phone + code) in the terminal; after that it reuses the `backend/session` file (gitignored — never commit it)

Required env vars (repo-root `.env`): `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `MONGO_URI` (MongoDB connection string), `DB_NAME` (MongoDB database name), `PW_HASH` (bcrypt hash of the site password — single-quote it so the `$` signs stay literal; generate with `uv run python -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"`), `SESSION_SECRET` (signs the session cookie). Optional: `TELEGRAM_CHANNEL` (username or numeric ID used only to seed an empty channel registry on first run), `BACKEND_PORT` (default 8000), `FRONTEND_PORT` (default 5173), `FRONTEND_ORIGIN` (defaults to `http://localhost:<FRONTEND_PORT>`, used for CORS), `TG_CONNECTIONS` (parallel download senders, default 4; 0 disables the pool), `CACHE_DIR` (disk cache location, default `backend/cache/`; startup default only, overridden across restarts by a value saved from the frontend cache drawer in MongoDB's `settings` collection), `CACHE_MAX_GB` (block-cache size cap, default 100; startup default only, overridden across restarts by a value saved from the frontend cache drawer in MongoDB's `settings` collection), `PREWARM_ENABLED` (default on; `0` disables idle library prewarm only), `PREWARM_RESCAN_SECONDS` (idle delay between prewarm passes, default 600).

Frontend (from `frontend/`, using `npm`):
- Install deps: `npm install`
- Run dev server: `npm run dev` (Vite, serves on `FRONTEND_PORT` from the repo-root `.env`, default 5173; `strictPort` is on, so it fails rather than drifting off the CORS-pinned port)
- `vite.config.js` reads the repo-root `.env` and injects `VITE_API_BASE` (an explicit `VITE_API_BASE` wins, else `http://localhost:<BACKEND_PORT>`).

Tests: backend `uv run pytest -q` (from `backend/`), frontend `npm run test` (Vitest, from `frontend/`). No linter is configured on either side.

## Architecture

**Backend** is split by concern, not by feature:
- `main.py` — HTTP only: routes, Range-header parsing, status codes, and the site auth (bcrypt password check via `POST /api/auth` behind `rate_limit.py`'s per-IP limiter, signed session cookie, `require_auth` dependency gating every data route). Delegates streaming to `streaming.py` and everything else Telegram-related to `telegram.py`.
- `streaming.py` — HTTP-range block planner: maps the requested byte range onto fixed-size blocks (`plan_blocks`), delegates block acquisition to `prefetch.py`, updates the playhead pin, and falls back to `telegram.stream_range` (single connection) if a block download fails — the cache layer must never make playback worse. A `?preview=1` query flag on `GET /stream/{msg_id}` skips the playhead pin update, so hover previews never steal the pin from the video actually open in the player.
- `prefetch.py` — serial background download worker with five tiers: urgent cache misses preempt its in-flight block unless they share that block's lock; the last-streamed video is pinned for full playhead-to-end-then-start download through pauses and closed connections (skipped when the file exceeds the cache cap); a one-slot priority job — set via `POST /api/prefetch/priority` (frontend "cache now" button) — jumps the queue front-to-back ahead of the visible tier; the videos currently on the user's screen (reported by the frontend via `POST /api/prefetch/visible`, which also cancels an in-flight prewarm block) download in display order, each budgeted by full file size so the visible set never evicts itself; idle time prewarms newest library videos that fit under the cache cap. The visible tier is independent of `PREWARM_ENABLED`, which gates only the library prewarm.
- `downloader.py` — parallel MTProto engine: fetches one block as `REQUEST_SIZE` stripes spread across a pool of extra senders created from the existing session (mirrors Telethon's `_create_exported_sender`; verified against Telethon 1.44). Handles `file_reference` expiry by re-resolving the message and retrying once.
- `cache.py` — disk caches: video blocks keyed by `(msg_id, block_idx)` with atomic writes and mtime-LRU eviction under `CACHE_MAX_GB`, plus uncapped thumbnails. Every failure degrades to a cache miss.
- `settings.py` — Mongo-backed runtime overrides for the cache directory and size cap, loaded at startup and applied live via `cache.configure()`; `POST /api/cache/settings` changes them at runtime (size changes evict immediately; location changes restart the prefetch worker, start with whatever cache blocks/thumbs already exist in the new location—normally none, while re-pointing at a previously used cache directory reuses its still-valid contents—and delete the old location's `blocks/`/`thumbs/` in the background). A `.tg-cache` marker file claims each cache root: locations already containing foreign `blocks/`/`thumbs/` are rejected, deletion refuses unowned roots, and the old-root cleanup skips a root that has become active again.
- `telegram.py` — owns the single shared `TelegramClient` and the direct fallback streaming path, including the Telegram quirk that download offsets must be 4096-byte aligned. `get_message` serves repeats from a short TTL cache (`invalidate_message` drops a stale `file_reference`).
- `video_metadata.py` — enriches `/api/videos` pages with `caption`/`posted_ts` from `postData1`, and answers the caption search query behind `search=`.
- `config.py` — env vars plus the tuning constants (`ALIGN`, `REQUEST_SIZE`, `BLOCK_SIZE`, `MSG_CACHE_TTL`, `TG_CONNECTIONS`, `PREWARM_ENABLED`, `PREWARM_RESCAN_SECONDS`, cache settings).

The channel registry is stored in MongoDB and managed by `channels.py`; its active-channel switch endpoint coordinates cache cleanup and worker restart before serving the newly selected channel.

The core mechanic worth understanding before touching streaming code: `GET /stream/{msg_id}` in `main.py` parses the browser's `Range` header into an inclusive byte range, and `streaming.stream_range` walks it block by block — the range math in `main.parse_range`, `streaming.plan_blocks`, and the `telegram.stream_range` fallback has to stay consistent across those file boundaries.

`GET /api/videos` supports cursor pagination (`?limit=50&before_id=<msg_id>` returns videos strictly older than `before_id`), plus an optional `search=<text>` param that caption-searches `postData1` instead (Mongo `$text`, relevance-ordered, `offset`/`limit` paginated; `before_id`/`category` are ignored in search mode). `GET /thumb/{msg_id}` serves from the disk thumb cache first and sends `Cache-Control: private, max-age=86400`. `GET /api/cache/status` also reports `cache_dir` and `max_gb`.

**Frontend** follows one direction of data flow: `hooks/useVideos.js` fetches and holds state (cursor pagination: `loadMore` appends the next page, `hasMore` flips false on a short page; each video item also carries `caption` (string|null) and `posted_ts` (unix seconds|null)) → `App.jsx` composes state with presentation — a ledger-style list where each `components/VideoRow.jsx` expands into an inline `components/VideoPlayer.jsx` (accordion: one open row, so one open stream; opens at 50% volume with 5s-skip overlay buttons) — with an infinite-scroll sentinel (`hooks/useSentinel.js`, IntersectionObserver) after the list that calls `loadMore`. `api/client.js` is the single place that knows the backend base URL (`VITE_API_BASE`, injected by `vite.config.js` from the repo-root `.env`) and builds stream/thumb URLs — components never construct backend URLs themselves.

`hooks/useChannels.js` owns channel-registry state; `App.jsx` wires it to `components/ChannelDrawer.jsx` and remounts the video library when the active channel changes.

`hooks/useVisibleVideos.js` tracks which rows are on screen (one IntersectionObserver; each `VideoRow` root registers via its `rowRef` prop) and reports the visible ids, in display order and debounced, to `POST /api/prefetch/visible` — this is what makes the cache worker prioritize whatever the user is currently looking at after scrolling or changing the category filter.

`hooks/useHoverPreview.js` debounces pointer hover on a row by 300ms before flipping `previewing` true (and cancels immediately on leave), which `VideoRow` uses to render a floating ~360px preview popup (portaled to `document.body`, positioned beside the row; the thumb stays visible) holding a muted, looping `<video>` that streams from `previewStreamUrl(id, startSeconds)` starting at 25% of the video's duration.
