# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A React frontend that browses and plays videos stored in a Telegram channel, streamed on-demand through a FastAPI backend. The backend converts HTTP Range requests into Telegram's chunked download protocol so the browser's native `<video>` seeking works: urgent cache misses download over a pool of parallel MTProto connections, while a tiered background worker completes the open video through pauses and prewarms the newest library videos into an LRU-capped disk block cache (`backend/cache/`, gitignored) when idle.

## Commands

Backend (from `backend/`, using `uv`):
- Install deps: `uv sync`
- Run dev server: `uv run python main.py` (reload enabled; listens on `BACKEND_PORT`. `config.py` loads the repo-root `.env` itself via `python-dotenv`, so no `--env-file` flag is needed)
- First run opens an interactive Telethon login prompt (phone + code) in the terminal; after that it reuses the `backend/session` file (gitignored — never commit it)

Required env vars (repo-root `.env`): `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_CHANNEL` (username or numeric ID), `PW_HASH` (bcrypt hash of the site password — single-quote it so the `$` signs stay literal; generate with `uv run python -c "import bcrypt; print(bcrypt.hashpw(b'yourpassword', bcrypt.gensalt()).decode())"`), `SESSION_SECRET` (signs the session cookie). Optional: `BACKEND_PORT` (default 8000), `FRONTEND_PORT` (default 5173), `FRONTEND_ORIGIN` (defaults to `http://localhost:<FRONTEND_PORT>`, used for CORS), `TG_CONNECTIONS` (parallel download senders, default 4; 0 disables the pool), `CACHE_DIR` (disk cache location, default `backend/cache/`), `CACHE_MAX_GB` (block-cache size cap, default 100), `PREWARM_ENABLED` (default on; `0` disables idle library prewarm only), `PREWARM_RESCAN_SECONDS` (idle delay between prewarm passes, default 600).

Frontend (from `frontend/`, using `npm`):
- Install deps: `npm install`
- Run dev server: `npm run dev` (Vite, serves on `FRONTEND_PORT` from the repo-root `.env`, default 5173; `strictPort` is on, so it fails rather than drifting off the CORS-pinned port)
- `vite.config.js` reads the repo-root `.env` and injects `VITE_API_BASE` (an explicit `VITE_API_BASE` wins, else `http://localhost:<BACKEND_PORT>`).

Tests: backend `uv run pytest -q` (from `backend/`), frontend `npm run test` (Vitest, from `frontend/`). No linter is configured on either side.

## Architecture

**Backend** is split by concern, not by feature:
- `main.py` — HTTP only: routes, Range-header parsing, status codes, and the site auth (bcrypt password check via `POST /api/auth` behind `rate_limit.py`'s per-IP limiter, signed session cookie, `require_auth` dependency gating every data route). Delegates streaming to `streaming.py` and everything else Telegram-related to `telegram.py`.
- `streaming.py` — HTTP-range block planner: maps the requested byte range onto fixed-size blocks (`plan_blocks`), delegates block acquisition to `prefetch.py`, updates the playhead pin, and falls back to `telegram.stream_range` (single connection) if a block download fails — the cache layer must never make playback worse.
- `prefetch.py` — serial background download worker with three tiers: urgent cache misses preempt its in-flight block unless they share that block's lock; the last-streamed video is pinned for full playhead-to-end-then-start download through pauses and closed connections (skipped when the file exceeds the cache cap); idle time prewarms newest library videos that fit under the cache cap.
- `downloader.py` — parallel MTProto engine: fetches one block as `REQUEST_SIZE` stripes spread across a pool of extra senders created from the existing session (mirrors Telethon's `_create_exported_sender`; verified against Telethon 1.44). Handles `file_reference` expiry by re-resolving the message and retrying once.
- `cache.py` — disk caches: video blocks keyed by `(msg_id, block_idx)` with atomic writes and mtime-LRU eviction under `CACHE_MAX_GB`, plus uncapped thumbnails. Every failure degrades to a cache miss.
- `telegram.py` — owns the single shared `TelegramClient` and the direct fallback streaming path, including the Telegram quirk that download offsets must be 4096-byte aligned. `get_message` serves repeats from a short TTL cache (`invalidate_message` drops a stale `file_reference`).
- `config.py` — env vars plus the tuning constants (`ALIGN`, `REQUEST_SIZE`, `BLOCK_SIZE`, `MSG_CACHE_TTL`, `TG_CONNECTIONS`, `PREWARM_ENABLED`, `PREWARM_RESCAN_SECONDS`, cache settings).

The core mechanic worth understanding before touching streaming code: `GET /stream/{msg_id}` in `main.py` parses the browser's `Range` header into an inclusive byte range, and `streaming.stream_range` walks it block by block — the range math in `main.parse_range`, `streaming.plan_blocks`, and the `telegram.stream_range` fallback has to stay consistent across those file boundaries.

`GET /api/videos` supports cursor pagination (`?limit=50&before_id=<msg_id>` returns videos strictly older than `before_id`). `GET /thumb/{msg_id}` serves from the disk thumb cache first and sends `Cache-Control: private, max-age=86400`.

**Frontend** follows one direction of data flow: `hooks/useVideos.js` fetches and holds state (cursor pagination: `loadMore` appends the next page, `hasMore` flips false on a short page) → `App.jsx` composes state with presentation — a ledger-style list where each `components/VideoRow.jsx` expands into an inline `components/VideoPlayer.jsx` (accordion: one open row, so one open stream) — with an infinite-scroll sentinel (`hooks/useSentinel.js`, IntersectionObserver) after the list that calls `loadMore`. `api/client.js` is the single place that knows the backend base URL (`VITE_API_BASE`, injected by `vite.config.js` from the repo-root `.env`) and builds stream/thumb URLs — components never construct backend URLs themselves.
