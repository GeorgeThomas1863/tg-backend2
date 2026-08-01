> **Superseded:** Readahead (`READAHEAD_BLOCKS`) was replaced by the tiered background worker in `prefetch.py`, and `CACHE_MAX_GB` now defaults to 100. See `2026-08-01-preload-queue-design.md`.

# Video Streaming Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. (subagent-driven-development is disallowed by user config.)

**Goal:** Raise Telegram→backend streaming throughput ~4× via parallel MTProto connections, add a disk block cache with readahead so watched bytes never re-download, and paginate the 35k-video list.

**Architecture:** `main.py` stays HTTP-only. Two new single-purpose backend modules: `downloader.py` (parallel block fetch over a pool of extra MTProto senders) and `cache.py` (LRU-capped disk block cache + thumb cache), orchestrated by a third, `streaming.py` (cache-aware `stream_range` with readahead). `telegram.py` gains a TTL message cache. Frontend gains cursor pagination (`before_id`) with an IntersectionObserver sentinel.

**Tech Stack:** FastAPI, Telethon 1.44 (verified against installed source), pytest (`asyncio_mode=auto`, monkeypatch-style fakes), React 19 + Vitest.

**Spec:** `docs/superpowers/specs/2026-07-13-video-streaming-performance-design.md`

## Global Constraints

- /cc clean-code principles: verb-phrase names, orchestrators delegate, guard clauses, ≤2 nesting levels, `for` loops, queries return data-or-None, operations log context on external-call failure.
- TDD: every task writes its failing test first, sees it fail, implements, sees it pass.
- **No git commits by Claude — the user owns all commits.** Task boundaries are natural commit points for the user.
- Baseline (2026-07-13): 0.73 MB/s sustained, 0.26 s TTFB. Phase-1 acceptance: ≥ 3 MB/s sustained on msg 35911.
- Verified Telethon 1.44 facts (do not re-derive from memory):
  - `from telethon.network import MTProtoSender`; `from telethon.tl.alltlobjects import LAYER`
  - `utils.get_input_location(media)` → `(dc_id, InputDocumentFileLocation)` (file_reference inside)
  - `functions.upload.GetFileRequest(location, offset=, limit=)` via `await client._call(sender, request)` → `result.bytes`; limit ≤ 512 KiB (Telethon's `MAX_CHUNK_SIZE`), offset/limit multiples of 4096
  - Same-DC extra sender: `MTProtoSender(client.session.auth_key, loggers=client._log)` + `sender.connect(client._connection(dc.ip_address, dc.port, dc.id, loggers=client._log, proxy=client._proxy, local_addr=client._local_addr))` — no init needed (initConnection is per-auth-key)
  - Cross-DC: sender with `None` auth key, then `ExportAuthorizationRequest(dc_id)` on the main client, `client._init_request.query = ImportAuthorizationRequest(...)`, `sender.send(InvokeWithLayerRequest(LAYER, client._init_request))` (mirrors Telethon's `_create_exported_sender`)
  - `client.get_messages(CHANNEL, limit=, offset_id=, filter=)` — `offset_id` returns messages strictly older
  - Errors: `errors.FileReferenceExpiredError`, `errors.FilerefUpgradeNeededError`
- Backend commands run from `backend/`: `uv run pytest -q`. Frontend from `frontend/`: `npm run test`.
- Plan deviation from spec, agreed rationale: the spec's interim "Phase 1 drop-in `fetch_range_streaming`" wiring is skipped — tasks are executed consecutively, so `/stream` switches directly to the cache-aware `streaming.stream_range` (Task 7) and the throughput benchmark runs there. Avoids throwaway code.

---

### Task 1: Config additions + test bootstrap

**Files:**
- Modify: `backend/config.py` (after the `ALIGN`/`REQUEST_SIZE` block)
- Modify: `backend/tests/conftest.py` (env-var block)
- Modify: `.gitignore` (repo root)

**Interfaces:**
- Produces: `config.TG_CONNECTIONS: int` (default 4), `config.CACHE_DIR: Path`, `config.CACHE_MAX_GB: float` (default 20), `config.BLOCK_SIZE = 4*1024*1024`, `config.READAHEAD_BLOCKS = 8`, `config.MSG_CACHE_TTL = 300`. All later tasks import from `config`.

- [x] **Step 1:** Add to `backend/config.py` (config is import-time validated like the AUTH_* guards; no separate test file — the whole suite exercises it via conftest):

```python
# Parallel download + disk cache tuning.
TG_CONNECTIONS = int(os.environ.get("TG_CONNECTIONS", "4"))
CACHE_DIR = Path(os.environ.get("CACHE_DIR", str(Path(__file__).resolve().parent / "cache")))
CACHE_MAX_GB = float(os.environ.get("CACHE_MAX_GB", "20"))

if TG_CONNECTIONS < 0:
    raise ValueError("TG_CONNECTIONS must be >= 0 (0 disables the parallel pool)")
if CACHE_MAX_GB <= 0:
    raise ValueError("CACHE_MAX_GB must be greater than zero")

BLOCK_SIZE = 4 * 1024 * 1024   # cache unit; multiple of ALIGN and REQUEST_SIZE
READAHEAD_BLOCKS = 8           # blocks fetched ahead of the playhead (32 MiB)
MSG_CACHE_TTL = 300            # seconds a resolved message stays fresh
```

- [x] **Step 2:** In `backend/tests/conftest.py`, add to the env block (before the config-importing imports):

```python
os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="tg-backend-cache-")
os.environ["TG_CONNECTIONS"] = "2"
```

- [x] **Step 3:** Add `backend/cache/` to `.gitignore`.

- [x] **Step 4:** Run: `uv run pytest -q` → expected: 47 passed (no regressions; new constants unused yet).

---

### Task 2: Message TTL cache in `telegram.py`

**Files:**
- Modify: `backend/telegram.py`
- Test: `backend/tests/test_message_cache.py`

**Interfaces:**
- Produces: `telegram.get_message(msg_id)` (signature unchanged, now TTL-cached), `telegram.invalidate_message(msg_id) -> None`. Task 4's file-reference retry consumes `invalidate_message` + `get_message`.

- [x] **Step 1:** Write `backend/tests/test_message_cache.py`:

```python
"""
get_message TTL cache: repeated calls inside the TTL hit Telegram once;
expiry and invalidate_message force a re-fetch. client.get_messages is
monkeypatched; time is controlled via telegram.time.monotonic.
"""

from types import SimpleNamespace

import telegram


async def test_second_call_within_ttl_hits_telegram_once(monkeypatch):
    calls = install_fake_get_messages(monkeypatch)
    first = await telegram.get_message(1)
    second = await telegram.get_message(1)
    assert first is second
    assert calls["count"] == 1


async def test_expired_entry_is_refetched(monkeypatch):
    calls = install_fake_get_messages(monkeypatch)
    clock = install_fake_clock(monkeypatch, start=1000.0)
    await telegram.get_message(1)
    clock["now"] += 301
    await telegram.get_message(1)
    assert calls["count"] == 2


async def test_invalidate_forces_refetch(monkeypatch):
    calls = install_fake_get_messages(monkeypatch)
    await telegram.get_message(1)
    telegram.invalidate_message(1)
    await telegram.get_message(1)
    assert calls["count"] == 2


async def test_none_result_is_not_cached(monkeypatch):
    calls = install_fake_get_messages(monkeypatch, result=None)
    await telegram.get_message(1)
    await telegram.get_message(1)
    assert calls["count"] == 2


async def test_failure_returns_none(monkeypatch):
    async def exploding(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(telegram.client, "get_messages", exploding)
    telegram.invalidate_message(1)
    assert await telegram.get_message(1) is None


# --- helpers ---


def install_fake_get_messages(monkeypatch, result="default"):
    calls = {"count": 0}

    async def fake_get_messages(channel, ids):
        calls["count"] += 1
        if result == "default":
            return SimpleNamespace(id=ids, media=object())
        return result

    monkeypatch.setattr(telegram.client, "get_messages", fake_get_messages)
    telegram._msg_cache.clear()
    return calls


def install_fake_clock(monkeypatch, start):
    clock = {"now": start}
    monkeypatch.setattr(telegram.time, "monotonic", lambda: clock["now"])
    return clock
```

- [x] **Step 2:** Run: `uv run pytest tests/test_message_cache.py -q` → expected: FAIL (`AttributeError: _msg_cache`).

- [x] **Step 3:** In `backend/telegram.py`: add `import time` and `from config import ... MSG_CACHE_TTL`; add `_msg_cache: dict = {}` after `client`; replace `get_message` and add helpers (keep them adjacent, in call order):

```python
_msg_cache: dict[int, tuple[object, float]] = {}


async def get_message(msg_id: int):
    """Resolve a message, serving repeats from a short TTL cache."""
    cached = read_cached_message(msg_id)
    if cached is not None:
        return cached

    try:
        msg = await client.get_messages(CHANNEL, ids=msg_id)
    except Exception:
        report_error(f"fetching message {msg_id} from {CHANNEL!r}")
        return None

    if msg:
        store_cached_message(msg_id, msg)
    return msg


def read_cached_message(msg_id: int):
    entry = _msg_cache.get(msg_id)
    if not entry:
        return None
    msg, fetched_at = entry
    if time.monotonic() - fetched_at > MSG_CACHE_TTL:
        del _msg_cache[msg_id]
        return None
    return msg


def store_cached_message(msg_id: int, msg) -> None:
    # Blunt size bound: reset rather than track LRU — refill is one RTT.
    if len(_msg_cache) > 1024:
        _msg_cache.clear()
    _msg_cache[msg_id] = (msg, time.monotonic())


def invalidate_message(msg_id: int) -> None:
    """Drop a cached message (stale file_reference)."""
    _msg_cache.pop(msg_id, None)
```

- [x] **Step 4:** Run: `uv run pytest -q` → expected: 52 passed. *(User commit point.)*

---

### Task 3: Disk block cache (`cache.py`)

**Files:**
- Create: `backend/cache.py`
- Test: `backend/tests/test_block_cache.py`

**Interfaces:**
- Produces: `cache.read_block(msg_id, idx) -> bytes | None`, `cache.write_block(msg_id, idx, data) -> None`, `cache.has_block(msg_id, idx) -> bool`, `cache.read_thumb(msg_id) -> bytes | None`, `cache.write_thumb(msg_id, data) -> None`. Module attrs `CACHE_ROOT: Path`, `MAX_BYTES: int`, `_total_bytes` (tests monkeypatch all three).

- [x] **Step 1:** Write `backend/tests/test_block_cache.py`:

```python
"""
Disk block cache: round-trips, atomicity, mtime-LRU eviction under the byte
cap, and failure-degrades-to-miss. Each test points CACHE_ROOT at its own
tmp_path and resets the lazy byte counter.
"""

import os

import cache


def test_read_miss_returns_none(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch)
    assert cache.read_block(1, 0) is None


def test_write_then_read_round_trips(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch)
    cache.write_block(1, 0, b"hello")
    assert cache.read_block(1, 0) == b"hello"
    assert cache.has_block(1, 0) is True


def test_write_leaves_no_tmp_file(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch)
    cache.write_block(1, 0, b"data")
    leftovers = [p for p in (tmp_path / "blocks").rglob("*.tmp")]
    assert leftovers == []


def test_empty_data_is_not_written(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch)
    cache.write_block(1, 0, b"")
    assert cache.has_block(1, 0) is False


def test_eviction_removes_oldest_first(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch, max_bytes=250)
    cache.write_block(1, 0, b"x" * 100)
    age_block(tmp_path, 1, 0, seconds=100)
    cache.write_block(1, 1, b"y" * 100)
    age_block(tmp_path, 1, 1, seconds=50)
    cache.write_block(2, 0, b"z" * 100)  # 300 > 250: evict oldest → (1,0)
    assert cache.has_block(1, 0) is False
    assert cache.has_block(1, 1) is True
    assert cache.has_block(2, 0) is True


def test_read_touches_mtime_for_lru(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch, max_bytes=250)
    cache.write_block(1, 0, b"x" * 100)
    age_block(tmp_path, 1, 0, seconds=100)
    cache.write_block(1, 1, b"y" * 100)
    age_block(tmp_path, 1, 1, seconds=50)
    cache.read_block(1, 0)               # touch: now (1,1) is oldest
    cache.write_block(2, 0, b"z" * 100)
    assert cache.has_block(1, 0) is True
    assert cache.has_block(1, 1) is False


def test_total_is_rebuilt_by_scanning_existing_files(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch, max_bytes=150)
    cache.write_block(1, 0, b"x" * 100)
    monkeypatch.setattr(cache, "_total_bytes", None)  # simulate restart
    cache.write_block(1, 1, b"y" * 100)               # scan finds 100 → evict
    assert cache.has_block(1, 0) is False


def test_thumb_round_trip_and_miss(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch)
    assert cache.read_thumb(7) is None
    cache.write_thumb(7, b"\xff\xd8jpeg")
    assert cache.read_thumb(7) == b"\xff\xd8jpeg"


def test_write_failure_degrades_silently(tmp_path, monkeypatch):
    point_cache_at(tmp_path, monkeypatch)

    def exploding_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(cache.os, "replace", exploding_replace)
    cache.write_block(1, 0, b"data")     # must not raise
    assert cache.read_block(1, 0) is None


# --- helpers ---


def point_cache_at(tmp_path, monkeypatch, max_bytes=10**9):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(cache, "MAX_BYTES", max_bytes)
    monkeypatch.setattr(cache, "_total_bytes", None)


def age_block(tmp_path, msg_id, idx, seconds):
    path = tmp_path / "blocks" / str(msg_id) / f"{idx}.blk"
    old = path.stat().st_mtime - seconds
    os.utime(path, (old, old))
```

- [x] **Step 2:** Run: `uv run pytest tests/test_block_cache.py -q` → expected: FAIL (`ModuleNotFoundError: cache`).

- [x] **Step 3:** Create `backend/cache.py`:

```python
"""
Disk caches: video blocks and thumbnails.

Video bytes are cached as whole fixed-size blocks keyed by (msg_id,
block_idx); the final block of a file is naturally shorter. Reads touch the
file mtime; writes are atomic (temp file + os.replace) and trigger LRU
eviction once the total passes MAX_BYTES. Thumbs are tiny and uncapped.
Every failure degrades to a cache miss — the cache is an optimization,
never a correctness dependency.
"""

import os
import traceback
from pathlib import Path

from config import CACHE_DIR, CACHE_MAX_GB

CACHE_ROOT = Path(CACHE_DIR)
MAX_BYTES = int(CACHE_MAX_GB * 1024**3)

_total_bytes = None  # lazily initialised; rebuilt by scan after restart


# --- blocks ---


def read_block(msg_id: int, block_idx: int) -> bytes | None:
    """Return a cached block (touching its mtime for LRU), or None."""
    path = build_block_path(msg_id, block_idx)
    try:
        data = path.read_bytes()
        os.utime(path)
        return data
    except OSError:
        return None


def write_block(msg_id: int, block_idx: int, data: bytes) -> None:
    """Atomically store a block, then evict oldest blocks over the cap."""
    if not data:
        return
    path = build_block_path(msg_id, block_idx)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        report_error(f"writing block {msg_id}/{block_idx}")
        return
    grow_total(len(data))
    evict_until_under_cap()


def has_block(msg_id: int, block_idx: int) -> bool:
    return build_block_path(msg_id, block_idx).exists()


# --- thumbs ---


def read_thumb(msg_id: int) -> bytes | None:
    try:
        return build_thumb_path(msg_id).read_bytes()
    except OSError:
        return None


def write_thumb(msg_id: int, data: bytes) -> None:
    if not data:
        return
    path = build_thumb_path(msg_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        report_error(f"writing thumb {msg_id}")


# --- size accounting + eviction ---


def evict_until_under_cap() -> None:
    global _total_bytes
    if current_total() <= MAX_BYTES:
        return
    for path, size in list_blocks_oldest_first():
        if _total_bytes <= MAX_BYTES:
            return
        try:
            path.unlink()
            _total_bytes -= size
        except OSError:
            report_error(f"evicting {path}")


def current_total() -> int:
    global _total_bytes
    if _total_bytes is None:
        _total_bytes = scan_total()
    return _total_bytes


def grow_total(added: int) -> None:
    global _total_bytes
    _total_bytes = current_total() + added


def scan_total() -> int:
    total = 0
    for _, size in iter_block_files():
        total += size
    return total


def list_blocks_oldest_first() -> list:
    entries = []
    for path, size in iter_block_files():
        try:
            entries.append((path.stat().st_mtime, path, size))
        except OSError:
            continue
    entries.sort()
    return [(path, size) for _, path, size in entries]


def iter_block_files():
    root = CACHE_ROOT / "blocks"
    if not root.exists():
        return
    for path in root.rglob("*.blk"):
        try:
            yield path, path.stat().st_size
        except OSError:
            continue


# --- pure builders ---


def build_block_path(msg_id: int, block_idx: int) -> Path:
    return CACHE_ROOT / "blocks" / str(msg_id) / f"{block_idx}.blk"


def build_thumb_path(msg_id: int) -> Path:
    return CACHE_ROOT / "thumbs" / f"{msg_id}.jpg"


def report_error(context: str) -> None:
    print(f"CACHE ERROR {context}:\n{traceback.format_exc()}")
```

- [x] **Step 4:** Run: `uv run pytest -q` → expected: 62 passed. *(User commit point.)*

---

### Task 4: Parallel download engine (`downloader.py`)

**Files:**
- Create: `backend/downloader.py`
- Test: `backend/tests/test_downloader.py`

**Interfaces:**
- Consumes: `telegram.client`, `telegram.get_message`, `telegram.invalidate_message`; `config.BLOCK_SIZE`, `config.REQUEST_SIZE`, `config.TG_CONNECTIONS`.
- Produces: `downloader.download_block(msg, block_idx) -> bytes | None`, `downloader.disconnect_all() -> None`, `downloader.resolve_location(media) -> (dc_id, location)` (test seam), `downloader.ensure_pool(dc_id) -> list`, `downloader.create_sender(dc_id) -> sender | None`.

- [x] **Step 1:** Write `backend/tests/test_downloader.py`:

```python
"""
Parallel block download with everything network-shaped faked: create_sender
returns dummy objects, resolve_location returns a sentinel, and
telegram.client._call serves slices of a deterministic buffer. Verifies
striping, reassembly order, round-robin sender use, the file-reference
retry, and pool-failure fallback (None).
"""

from types import SimpleNamespace

from telethon import errors

import downloader
import telegram
from config import BLOCK_SIZE, REQUEST_SIZE

FILE_SIZE = BLOCK_SIZE + 100_000          # two blocks; second one short
BUFFER = bytes(range(256)) * (FILE_SIZE // 256 + 1)
LOCATION = object()


async def test_downloads_a_full_block_byte_exact(monkeypatch):
    install_fakes(monkeypatch)
    data = await downloader.download_block(make_msg(), 0)
    assert data == BUFFER[:BLOCK_SIZE]


async def test_downloads_short_final_block(monkeypatch):
    install_fakes(monkeypatch)
    data = await downloader.download_block(make_msg(), 1)
    assert data == BUFFER[BLOCK_SIZE:FILE_SIZE]


async def test_requests_are_striped_at_request_size_and_aligned(monkeypatch):
    seen = install_fakes(monkeypatch)
    await downloader.download_block(make_msg(), 0)
    offsets = sorted(call["offset"] for call in seen)
    assert offsets == list(range(0, BLOCK_SIZE, REQUEST_SIZE))
    for call in seen:
        assert call["offset"] % 4096 == 0
        assert call["limit"] == REQUEST_SIZE


async def test_stripes_rotate_across_pool_senders(monkeypatch):
    seen = install_fakes(monkeypatch, pool_size=2)
    await downloader.download_block(make_msg(), 0)
    senders = {call["sender"] for call in seen}
    assert len(senders) == 2


async def test_empty_pool_returns_none(monkeypatch):
    install_fakes(monkeypatch, pool_size=0)
    assert await downloader.download_block(make_msg(), 0) is None


async def test_out_of_range_block_returns_none(monkeypatch):
    install_fakes(monkeypatch)
    assert await downloader.download_block(make_msg(), 99) is None


async def test_file_reference_expiry_refreshes_and_retries(monkeypatch):
    state = {"failed_once": False, "refreshed": False}

    async def flaky_call(sender, request):
        if not state["failed_once"]:
            state["failed_once"] = True
            raise errors.FileReferenceExpiredError(request=request)
        return SimpleNamespace(bytes=BUFFER[request.offset:request.offset + request.limit])

    async def fake_get_message(msg_id):
        state["refreshed"] = True
        return make_msg()

    install_fakes(monkeypatch)
    monkeypatch.setattr(telegram.client, "_call", flaky_call)
    monkeypatch.setattr(telegram, "get_message", fake_get_message)

    data = await downloader.download_block(make_msg(), 0)

    assert state["refreshed"] is True
    assert data == BUFFER[:BLOCK_SIZE]


async def test_persistent_failure_returns_none(monkeypatch):
    async def always_failing_call(sender, request):
        raise RuntimeError("connection reset")

    install_fakes(monkeypatch)
    monkeypatch.setattr(telegram.client, "_call", always_failing_call)
    assert await downloader.download_block(make_msg(), 0) is None


# --- helpers ---


def make_msg():
    return SimpleNamespace(id=1, file=SimpleNamespace(size=FILE_SIZE), media=object())


def install_fakes(monkeypatch, pool_size=2):
    """Fake pool, location resolution, and _call. Returns the call log."""
    seen = []

    async def fake_call(sender, request):
        seen.append({"sender": sender, "offset": request.offset, "limit": request.limit})
        return SimpleNamespace(bytes=BUFFER[request.offset:request.offset + request.limit])

    async def fake_ensure_pool(dc_id):
        return [object() for _ in range(pool_size)]

    monkeypatch.setattr(downloader, "resolve_location", lambda media: (2, LOCATION))
    monkeypatch.setattr(downloader, "ensure_pool", fake_ensure_pool)
    monkeypatch.setattr(telegram.client, "_call", fake_call)
    return seen
```

- [x] **Step 2:** Run: `uv run pytest tests/test_downloader.py -q` → expected: FAIL (`ModuleNotFoundError: downloader`).

- [x] **Step 3:** Create `backend/downloader.py`:

```python
"""
Parallel MTProto download engine.

Telegram throttles per connection (~0.7 MB/s measured), so a block is
fetched as REQUEST_SIZE stripes spread over a small pool of extra senders
created from the existing session. Same-DC senders reuse the session auth
key on a fresh connection; other DCs get an exported authorization —
mirroring Telethon's own _create_exported_sender (verified in 1.44).
"""

import asyncio
import traceback

from telethon import errors, utils
from telethon.network import MTProtoSender
from telethon.tl import functions
from telethon.tl.alltlobjects import LAYER

import telegram
from config import BLOCK_SIZE, REQUEST_SIZE, TG_CONNECTIONS

_pools: dict[int, list] = {}   # dc_id -> connected senders
_pool_lock = asyncio.Lock()


async def download_block(msg, block_idx: int) -> bytes | None:
    """Download one whole block of the message's media, or None on failure."""
    if not msg or not msg.file:
        return None
    offset = block_idx * BLOCK_SIZE
    length = min(BLOCK_SIZE, msg.file.size - offset)
    if length <= 0:
        return None

    dc_id, location = resolve_location(msg.media)
    pool = await ensure_pool(dc_id)
    if not pool:
        return None

    parts = await fetch_stripes(pool, location, offset, length, msg)
    if parts is None:
        return None

    data = b"".join(parts)
    if len(data) != length:
        print(f"DOWNLOADER ERROR block {msg.id}/{block_idx}: "
              f"got {len(data)} bytes, wanted {length}")
        return None
    return data


async def disconnect_all() -> None:
    """Disconnect every pooled sender (app shutdown)."""
    async with _pool_lock:
        for pool in _pools.values():
            for sender in pool:
                try:
                    await sender.disconnect()
                except Exception:
                    report_error("disconnecting pooled sender")
        _pools.clear()


# --- striping ---


async def fetch_stripes(pool, location, offset, length, msg) -> list | None:
    tasks = []
    stripe_offsets = range(offset, offset + length, REQUEST_SIZE)
    for i, stripe_offset in enumerate(stripe_offsets):
        sender = pool[i % len(pool)]
        tasks.append(fetch_stripe(sender, location, stripe_offset, msg))
    try:
        return await asyncio.gather(*tasks)
    except Exception:
        report_error(f"striped download at offset {offset} for message {msg.id}")
        return None


async def fetch_stripe(sender, location, stripe_offset, msg) -> bytes:
    request = functions.upload.GetFileRequest(location, offset=stripe_offset, limit=REQUEST_SIZE)
    try:
        result = await telegram.client._call(sender, request)
    except (errors.FileReferenceExpiredError, errors.FilerefUpgradeNeededError):
        fresh_location = await refresh_location(msg)
        if fresh_location is None:
            raise
        request = functions.upload.GetFileRequest(fresh_location, offset=stripe_offset, limit=REQUEST_SIZE)
        result = await telegram.client._call(sender, request)
    return result.bytes


async def refresh_location(msg):
    """Re-resolve the message for a fresh file_reference; None if gone."""
    telegram.invalidate_message(msg.id)
    fresh = await telegram.get_message(msg.id)
    if not fresh or not fresh.media:
        return None
    return resolve_location(fresh.media)[1]


# --- sender pool ---


async def ensure_pool(dc_id: int) -> list:
    """Return the connected sender pool for a DC, building it on first use."""
    async with _pool_lock:
        if dc_id in _pools:
            return _pools[dc_id]
        pool = []
        for _ in range(TG_CONNECTIONS):
            sender = await create_sender(dc_id)
            if sender:
                pool.append(sender)
        _pools[dc_id] = pool
        return pool


async def create_sender(dc_id: int):
    """Connect one extra MTProto sender for a DC, or None on failure."""
    client = telegram.client
    try:
        dc = await client._get_dc(dc_id)
        is_same_dc = dc_id == client.session.dc_id
        auth_key = client.session.auth_key if is_same_dc else None
        sender = MTProtoSender(auth_key, loggers=client._log)
        await sender.connect(client._connection(
            dc.ip_address, dc.port, dc.id,
            loggers=client._log, proxy=client._proxy, local_addr=client._local_addr,
        ))
        if not is_same_dc:
            auth = await client(functions.auth.ExportAuthorizationRequest(dc_id))
            client._init_request.query = functions.auth.ImportAuthorizationRequest(
                id=auth.id, bytes=auth.bytes)
            await sender.send(functions.InvokeWithLayerRequest(LAYER, client._init_request))
        return sender
    except Exception:
        report_error(f"creating sender for DC {dc_id}")
        return None


# --- pure builders ---


def resolve_location(media):
    """(dc_id, input_location) for a message's media. Seam for tests."""
    return utils.get_input_location(media)


def report_error(context: str) -> None:
    print(f"DOWNLOADER ERROR {context}:\n{traceback.format_exc()}")
```

- [x] **Step 4:** Run: `uv run pytest -q` → expected: 70 passed. *(User commit point.)*

---

### Task 5: Block math (`streaming.py` — pure part)

**Files:**
- Create: `backend/streaming.py` (builders only in this task)
- Test: `backend/tests/test_plan_blocks.py`

**Interfaces:**
- Produces: `streaming.BlockSlice(idx, start, end)` (NamedTuple; `start`/`end` are within-block, end exclusive) and `streaming.plan_blocks(start, end, file_size) -> list[BlockSlice]`. Task 6 consumes both.

- [x] **Step 1:** Write `backend/tests/test_plan_blocks.py`:

```python
"""plan_blocks: inclusive byte range → per-block slices (end exclusive)."""

from streaming import BlockSlice, plan_blocks
from config import BLOCK_SIZE

FILE_SIZE = 3 * BLOCK_SIZE + 1000  # 4 blocks; last is 1000 bytes


def test_range_inside_one_block():
    assert plan_blocks(100, 199, FILE_SIZE) == [BlockSlice(0, 100, 200)]


def test_range_spanning_two_blocks():
    plans = plan_blocks(BLOCK_SIZE - 10, BLOCK_SIZE + 9, FILE_SIZE)
    assert plans == [
        BlockSlice(0, BLOCK_SIZE - 10, BLOCK_SIZE),
        BlockSlice(1, 0, 10),
    ]


def test_exact_block_boundaries():
    plans = plan_blocks(BLOCK_SIZE, 2 * BLOCK_SIZE - 1, FILE_SIZE)
    assert plans == [BlockSlice(1, 0, BLOCK_SIZE)]


def test_final_short_block_to_eof():
    plans = plan_blocks(3 * BLOCK_SIZE, FILE_SIZE - 1, FILE_SIZE)
    assert plans == [BlockSlice(3, 0, 1000)]


def test_end_clamped_to_file_size():
    plans = plan_blocks(3 * BLOCK_SIZE, 99 * BLOCK_SIZE, FILE_SIZE)
    assert plans == [BlockSlice(3, 0, 1000)]


def test_single_byte():
    assert plan_blocks(5, 5, FILE_SIZE) == [BlockSlice(0, 5, 6)]


def test_invalid_ranges_return_empty():
    assert plan_blocks(-1, 10, FILE_SIZE) == []
    assert plan_blocks(10, 9, FILE_SIZE) == []
    assert plan_blocks(FILE_SIZE, FILE_SIZE + 10, FILE_SIZE) == []
```

- [x] **Step 2:** Run: `uv run pytest tests/test_plan_blocks.py -q` → expected: FAIL (`ModuleNotFoundError: streaming`).

- [x] **Step 3:** Create `backend/streaming.py` (docstring + builders; orchestrator lands in Task 6):

```python
"""
Cache-aware streaming orchestration.

stream_range yields an HTTP byte range by walking whole cache blocks:
hit → disk, miss → parallel download → cache → yield, while readahead
keeps the next blocks downloading behind the playhead. Any download
failure falls back to the original single-connection telegram.stream_range
for the remainder — the cache layer must never make playback worse.
"""

from typing import NamedTuple

from config import BLOCK_SIZE


class BlockSlice(NamedTuple):
    idx: int
    start: int  # within-block slice start
    end: int    # within-block slice end, exclusive


def plan_blocks(start: int, end: int, file_size: int) -> list[BlockSlice]:
    """Map an inclusive byte range onto block indices with in-block slices."""
    if start < 0 or end < start or start >= file_size:
        return []
    end = min(end, file_size - 1)

    plans = []
    for idx in range(start // BLOCK_SIZE, end // BLOCK_SIZE + 1):
        block_offset = idx * BLOCK_SIZE
        slice_start = max(start - block_offset, 0)
        slice_end = min(end - block_offset + 1, BLOCK_SIZE)
        plans.append(BlockSlice(idx, slice_start, slice_end))
    return plans
```

- [x] **Step 4:** Run: `uv run pytest -q` → expected: 77 passed. *(User commit point.)*

---

### Task 6: Cache-aware stream + readahead (`streaming.py` — orchestrator)

**Files:**
- Modify: `backend/streaming.py`
- Test: `backend/tests/test_streaming.py`

**Interfaces:**
- Consumes: `cache.read_block/write_block/has_block`, `downloader.download_block`, `telegram.stream_range` (fallback), `config.READAHEAD_BLOCKS`.
- Produces: `streaming.stream_range(msg, start, end) -> AsyncGenerator[bytes]` (Task 7 wires it into `/stream`), `streaming.get_block(msg, idx)`, `streaming.schedule_readahead(msg, current_idx)`.

- [x] **Step 1:** Write `backend/tests/test_streaming.py`:

```python
"""
stream_range orchestration over fake cache + downloader: byte-exact output
across hits/misses, single download per block under concurrency, readahead
scheduling, and fallback to telegram.stream_range when a download fails.
"""

import asyncio
from types import SimpleNamespace

import cache
import downloader
import streaming
import telegram
from config import BLOCK_SIZE, READAHEAD_BLOCKS

FILE_SIZE = 3 * BLOCK_SIZE + 1000
BUFFER = bytes(range(256)) * (FILE_SIZE // 256 + 1)


async def test_streams_exact_bytes_across_blocks(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    out = await drain(streaming.stream_range(make_msg(), 100, BLOCK_SIZE + 50))
    assert out == BUFFER[100:BLOCK_SIZE + 51]


async def test_second_read_serves_from_cache(tmp_path, monkeypatch):
    downloads = install_world(tmp_path, monkeypatch)
    await drain(streaming.stream_range(make_msg(), 0, 100))
    first_count = len(downloads)
    await drain(streaming.stream_range(make_msg(), 0, 100))
    assert len(downloads) == first_count  # no new downloads


async def test_concurrent_same_block_downloads_once(tmp_path, monkeypatch):
    downloads = install_world(tmp_path, monkeypatch, readahead=0)
    await asyncio.gather(
        drain(streaming.stream_range(make_msg(), 0, 100)),
        drain(streaming.stream_range(make_msg(), 0, 100)),
    )
    assert downloads.count(0) == 1


async def test_readahead_caches_upcoming_blocks(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch)
    await drain(streaming.stream_range(make_msg(), 0, 100))  # block 0 + readahead
    await settle_readahead()
    for idx in range(1, min(READAHEAD_BLOCKS, 3) + 1):
        assert cache.has_block(1, idx) is True


async def test_download_failure_falls_back_to_direct_stream(tmp_path, monkeypatch):
    install_world(tmp_path, monkeypatch, failing=True)
    fallback_calls = install_fake_direct_stream(monkeypatch)

    out = await drain(streaming.stream_range(make_msg(), 0, 100))

    assert out == BUFFER[0:101]
    assert fallback_calls == [(0, 100)]


async def test_fallback_resumes_at_current_position(tmp_path, monkeypatch):
    downloads = install_world(tmp_path, monkeypatch, fail_from_block=1, readahead=0)
    fallback_calls = install_fake_direct_stream(monkeypatch)

    out = await drain(streaming.stream_range(make_msg(), 100, BLOCK_SIZE + 50))

    assert out == BUFFER[100:BLOCK_SIZE + 51]
    assert fallback_calls == [(BLOCK_SIZE, BLOCK_SIZE + 50)]


# --- helpers ---


async def drain(agen) -> bytes:
    parts = []
    async for chunk in agen:
        parts.append(chunk)
    return b"".join(parts)


async def settle_readahead():
    while streaming._readahead_tasks:
        await asyncio.sleep(0)


def make_msg():
    return SimpleNamespace(id=1, file=SimpleNamespace(size=FILE_SIZE), media=object())


def install_world(tmp_path, monkeypatch, failing=False, fail_from_block=None, readahead=None):
    """Point cache at tmp_path, fake downloader.download_block, reset state."""
    downloads = []

    async def fake_download_block(msg, idx):
        downloads.append(idx)
        if failing:
            return None
        if fail_from_block is not None and idx >= fail_from_block:
            return None
        offset = idx * BLOCK_SIZE
        return BUFFER[offset:min(offset + BLOCK_SIZE, FILE_SIZE)]

    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(cache, "MAX_BYTES", 10**12)
    monkeypatch.setattr(cache, "_total_bytes", None)
    monkeypatch.setattr(downloader, "download_block", fake_download_block)
    if readahead is not None:
        monkeypatch.setattr(streaming, "READAHEAD_BLOCKS", readahead)
    streaming._block_locks.clear()
    streaming._inflight.clear()
    return downloads


def install_fake_direct_stream(monkeypatch):
    calls = []

    async def fake_direct(msg, start, end):
        calls.append((start, end))
        yield BUFFER[start:end + 1]

    monkeypatch.setattr(telegram, "stream_range", fake_direct)
    return calls
```

- [x] **Step 2:** Run: `uv run pytest tests/test_streaming.py -q` → expected: FAIL (`AttributeError: stream_range`).

- [x] **Step 3:** Extend `backend/streaming.py` — imports become:

```python
import asyncio
from typing import AsyncGenerator, NamedTuple

import cache
import downloader
import telegram
from config import BLOCK_SIZE, READAHEAD_BLOCKS
```

Add above the builders (main orchestrator first, helpers in call order per /cc):

```python
_block_locks: dict = {}          # (msg_id, idx) -> asyncio.Lock
_inflight: set = set()           # (msg_id, idx) readahead keys
_readahead_tasks: set = set()    # strong refs so tasks aren't GC'd
_readahead_limit = asyncio.Semaphore(2)  # never starve the live stream


async def stream_range(msg, start: int, end: int) -> AsyncGenerator[bytes, None]:
    """Yield bytes [start, end] inclusive: cache-first, else download+cache."""
    position = start
    for plan in plan_blocks(start, end, msg.file.size):
        data = await get_block(msg, plan.idx)
        if data is None:
            async for chunk in telegram.stream_range(msg, position, end):
                yield chunk
            return
        schedule_readahead(msg, plan.idx)
        piece = data[plan.start:plan.end]
        position += len(piece)
        yield piece


async def get_block(msg, idx: int) -> bytes | None:
    """One block from cache or network; concurrent callers share one fetch."""
    key = (msg.id, idx)
    lock = _block_locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = cache.read_block(msg.id, idx)
        if cached is not None:
            return cached
        data = await downloader.download_block(msg, idx)
        if data is None:
            return None
        cache.write_block(msg.id, idx, data)
        return data


def schedule_readahead(msg, current_idx: int) -> None:
    """Kick off background fetches for the next READAHEAD_BLOCKS blocks."""
    last_idx = (msg.file.size - 1) // BLOCK_SIZE
    stop = min(current_idx + READAHEAD_BLOCKS, last_idx)
    for idx in range(current_idx + 1, stop + 1):
        key = (msg.id, idx)
        if key in _inflight or cache.has_block(msg.id, idx):
            continue
        _inflight.add(key)
        task = asyncio.create_task(fetch_ahead(msg, idx))
        _readahead_tasks.add(task)
        task.add_done_callback(_readahead_tasks.discard)
    prune_locks()


async def fetch_ahead(msg, idx: int) -> None:
    try:
        async with _readahead_limit:
            await get_block(msg, idx)
    finally:
        _inflight.discard((msg.id, idx))


def prune_locks() -> None:
    # Unlocked entries can be dropped; a rare double-download after a drop
    # is harmless (block writes are atomic) — correctness never depends on it.
    if len(_block_locks) < 8192:
        return
    for key in list(_block_locks):
        if not _block_locks[key].locked():
            del _block_locks[key]
```

Note: `from config import READAHEAD_BLOCKS` binds a module-level global in `streaming`; `schedule_readahead` reads it at call time, so `monkeypatch.setattr(streaming, "READAHEAD_BLOCKS", 0)` in tests works with no extra code.

- [x] **Step 4:** Run: `uv run pytest -q` → expected: 84 passed. *(User commit point.)*

---

### Task 7: Wire `/stream` to the new path + shutdown + benchmark

**Files:**
- Modify: `backend/main.py` (import, `/stream` body, lifespan)
- Modify: `backend/tests/test_routes.py` (stream fakes now patch `streaming`)
- Test: existing `test_routes.py` suite

**Interfaces:**
- Consumes: `streaming.stream_range`, `downloader.disconnect_all`.

- [x] **Step 1:** In `backend/tests/test_routes.py`: add `import streaming` and repoint the stream fake:

```python
def install_fake_stream_range(monkeypatch):
    async def fake_stream_range(msg, start, end):
        yield DATA[start:end + 1]

    monkeypatch.setattr(streaming, "stream_range", fake_stream_range)
```

- [x] **Step 2:** Run: `uv run pytest tests/test_routes.py -q` → expected: 2 failures (stream tests still served by the old `telegram.stream_range` path).

- [x] **Step 3:** In `backend/main.py`: add `import downloader` and `import streaming`; change the `StreamingResponse` call to `streaming.stream_range(msg, start, end)`; extend lifespan:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram.connect()
    yield
    await downloader.disconnect_all()
    await telegram.disconnect()
```

- [x] **Step 4:** Run: `uv run pytest -q` → expected: 84 passed.

- [x] **Step 5 (acceptance benchmark, manual):** with the dev server running (uvicorn auto-reloads):

```bash
curl -s -c /tmp/ck.txt -X POST http://localhost:1864/api/auth -H "Content-Type: application/json" -d '{"pw":"<PW>"}'
curl -s -b /tmp/ck.txt -o /dev/null -H "Range: bytes=0-31457279" -w "cold: %{speed_download} B/s\n" http://localhost:1864/stream/35911
curl -s -b /tmp/ck.txt -o /dev/null -H "Range: bytes=0-31457279" -w "warm: %{speed_download} B/s\n" http://localhost:1864/stream/35911
```

Expected: cold ≥ 3,000,000 B/s (vs 725,592 baseline); warm ≥ 10× cold (disk-served). Record both numbers in this file once measured. *(User commit point.)*

  **Measured 2026-08-01:** cold 720,581 B/s; warm (fully cached) 143,754,764 B/s. The warm
  target passes; the cold target is unachievable: per-stripe timing showed Telegram answers
  concurrent upload.getFile requests in quantized server-side delays, capping this account at
  ~0.7 MB/s **aggregate** regardless of connection count (4 and 8 senders and two concurrent
  files all total ~0.73 MB/s; account is premium). Parallel striping cannot beat a per-account
  server cap, so cold ≈ baseline by design of Telegram's limiter, not by a defect here. Two
  real defects were found and fixed while measuring: same-DC pool senders shared the session
  auth key **without ever sending initConnection**, which made Telegram force-close pool
  connections ("Server closed the connection: 0 bytes read") into reconnect storms — each new
  sender now sends InvokeWithLayer(initConnection) first; and auth.exportAuthorization is
  rejected for the session's own DC (DC_ID_INVALID), so same-DC senders must reuse the session
  auth key (cross-DC senders still get exported authorizations).

---

### Task 8: Thumbnail cache + Cache-Control

**Files:**
- Modify: `backend/main.py` (`/thumb` route)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `cache.read_thumb`, `cache.write_thumb`.

- [x] **Step 1:** Add to `backend/tests/test_routes.py` (thumb section; also add `import cache` and a fixture-style helper):

```python
def test_thumb_is_cached_on_disk_and_served_without_telegram(authed_client, monkeypatch, tmp_path):
    point_thumb_cache_at(tmp_path, monkeypatch)
    fake_jpeg = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
    install_get_message(monkeypatch, make_video_msg())
    install_get_thumbnail(monkeypatch, fake_jpeg)

    first = authed_client.get("/thumb/1")
    install_get_thumbnail(monkeypatch, None)      # Telegram would now fail
    second = authed_client.get("/thumb/1")        # must come from disk

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.content == fake_jpeg


def test_thumb_sends_cache_control_header(authed_client, monkeypatch, tmp_path):
    point_thumb_cache_at(tmp_path, monkeypatch)
    install_get_message(monkeypatch, make_video_msg())
    install_get_thumbnail(monkeypatch, b"\xff\xd8jpeg")

    resp = authed_client.get("/thumb/1")

    assert resp.headers["Cache-Control"] == "private, max-age=86400"


def point_thumb_cache_at(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_ROOT", tmp_path)
```

- [x] **Step 2:** Run: `uv run pytest tests/test_routes.py -q` → expected: 2 new failures.

- [x] **Step 3:** In `backend/main.py`: add `import cache`; replace the `/thumb` route body and add the builder at the bottom of the file:

```python
@app.get("/thumb/{msg_id}", dependencies=[Depends(require_auth)])
async def thumb(msg_id: int):
    cached = cache.read_thumb(msg_id)
    if cached:
        return build_thumb_response(cached)

    msg = await telegram.get_message(msg_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    data = await telegram.get_thumbnail(msg)
    if not data:
        raise HTTPException(status_code=404, detail="No thumbnail available")

    cache.write_thumb(msg_id, data)
    return build_thumb_response(data)


def build_thumb_response(data: bytes) -> Response:
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )
```

- [x] **Step 4:** Run: `uv run pytest -q` → expected: 86 passed. *(User commit point.)*

---

### Task 9: `/api/videos` pagination (backend)

**Files:**
- Modify: `backend/telegram.py` (`list_videos`), `backend/main.py` (`videos` route)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Produces: `GET /api/videos?limit=50&before_id=<msg_id>` — returns videos strictly older than `before_id`; bare-array response unchanged. `telegram.list_videos(limit=50, before_id=None)`.

- [x] **Step 1:** Add to `backend/tests/test_routes.py` (videos section):

```python
def test_videos_passes_before_id_through(authed_client, monkeypatch):
    seen = {}

    async def fake_list_videos(limit=50, before_id=None):
        seen["limit"] = limit
        seen["before_id"] = before_id
        return []

    monkeypatch.setattr(telegram, "list_videos", fake_list_videos)
    resp = authed_client.get("/api/videos?limit=25&before_id=1234")

    assert resp.status_code == 200
    assert seen == {"limit": 25, "before_id": 1234}
```

- [x] **Step 2:** Run → expected: FAIL (`before_id` unexpected).

- [x] **Step 3:** `backend/telegram.py`:

```python
async def list_videos(limit: int = 50, before_id: int | None = None) -> Optional[list[dict]]:
    try:
        msgs = await client.get_messages(
            CHANNEL, limit=limit, offset_id=before_id or 0,
            filter=InputMessagesFilterVideo,
        )
    except Exception:
        report_error(f"listing videos from {CHANNEL!r}")
        return None
    return [media_to_dict(m) for m in msgs if m.file]
```

`backend/main.py`:

```python
@app.get("/api/videos", dependencies=[Depends(require_auth)])
async def videos(limit: int = 50, before_id: int | None = None):
    result = await telegram.list_videos(limit, before_id)
    if result is None:
        raise HTTPException(status_code=502, detail="Telegram request failed")
    return result
```

- [x] **Step 4:** Run: `uv run pytest -q` → expected: 87 passed. *(User commit point.)*

---

### Task 10: `fetchVideos(limit, beforeId)` (frontend API)

**Files:**
- Modify: `frontend/src/api/client.js`
- Test: `frontend/src/api/client.test.js`

**Interfaces:**
- Produces: `fetchVideos(limit = 50, beforeId = null)` — appends `&before_id=` only when set. Task 11 consumes it.

- [x] **Step 1:** Add to the `fetchVideos` describe block in `client.test.js`:

```js
test("appends before_id only when provided", async () => {
  fetchMock.mockResolvedValue({ ok: true, json: async () => [] });

  await fetchVideos(50, 999);
  await fetchVideos(50);

  expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/api/videos?limit=50&before_id=999`);
  expect(fetchMock.mock.calls[1][0]).toBe(`${BASE}/api/videos?limit=50`);
});
```

- [x] **Step 2:** Run: `npm run test` → expected: 1 failure.

- [x] **Step 3:** In `client.js`:

```js
export async function fetchVideos(limit = 50, beforeId = null) {
  const beforeParam = beforeId ? `&before_id=${beforeId}` : "";
  const res = await fetch(`${BASE}/api/videos?limit=${limit}${beforeParam}`, { credentials: "include" });
  if (!res.ok) {
    const error = new Error(`HTTP ${res.status}`);
    error.status = res.status;
    throw error;
  }
  return res.json();
}
```

- [x] **Step 4:** Run: `npm run test` → expected: 31 passed. *(User commit point.)*

---

### Task 11: `useVideos` loadMore/hasMore

**Files:**
- Modify: `frontend/src/hooks/useVideos.js`
- Test: `frontend/src/hooks/useVideos.test.jsx`

**Interfaces:**
- Produces: `useVideos(limit)` additionally returns `{ loadMore, hasMore, loadingMore }`. `loadMore()` fetches `fetchVideos(limit, lastId)` and appends; `hasMore` is `lastPage.length === limit`; re-entrant calls are ignored while a page is in flight. Task 12 consumes.

- [x] **Step 1:** Add tests to `useVideos.test.jsx`:

```js
function buildPage(startId, count) {
  const page = [];
  for (let i = 0; i < count; i++) page.push({ id: startId - i });
  return page;
}

describe("useVideos pagination", () => {
  test("hasMore is true after a full first page, false after a short one", async () => {
    fetchVideos.mockResolvedValue(buildPage(100, 50));
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.hasMore).toBe(true);

    fetchVideos.mockResolvedValue(buildPage(100, 10));
    const short = renderHook(() => useVideos(50));
    await waitFor(() => expect(short.result.current.loading).toBe(false));
    expect(short.result.current.hasMore).toBe(false);
  });

  test("loadMore fetches with the last id as beforeId and appends", async () => {
    fetchVideos.mockResolvedValueOnce(buildPage(100, 50)).mockResolvedValueOnce(buildPage(50, 50));
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.loadMore());

    expect(fetchVideos).toHaveBeenLastCalledWith(50, 51);
    expect(result.current.videos).toHaveLength(100);
    expect(result.current.hasMore).toBe(true);
  });

  test("a short page flips hasMore to false and further loadMore calls do not fetch", async () => {
    fetchVideos.mockResolvedValueOnce(buildPage(100, 50)).mockResolvedValueOnce(buildPage(50, 3));
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.loadMore());
    expect(result.current.hasMore).toBe(false);

    await act(() => result.current.loadMore());
    expect(fetchVideos).toHaveBeenCalledTimes(2);
  });

  test("overlapping loadMore calls fetch only once", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    fetchVideos.mockResolvedValueOnce(buildPage(100, 50)).mockImplementationOnce(async () => {
      await gate;
      return buildPage(50, 50);
    });
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let first;
    act(() => { first = result.current.loadMore(); result.current.loadMore(); });
    release();
    await act(() => first);

    expect(fetchVideos).toHaveBeenCalledTimes(2);
  });

  test("loadMore failure with 401 sets unauthorized", async () => {
    fetchVideos.mockResolvedValueOnce(buildPage(100, 50)).mockRejectedValueOnce(buildHttpError(401));
    const { result } = renderHook(() => useVideos(50));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(() => result.current.loadMore());

    expect(result.current.unauthorized).toBe(true);
  });
});
```

- [x] **Step 2:** Run: `npm run test` → expected: pagination tests fail.

- [x] **Step 3:** Rewrite `useVideos.js`:

```js
import { useEffect, useRef, useState } from "react";
import { fetchVideos } from "../api/client";

// Fetches the video list with cursor pagination and exposes
// loading/error/auth state. A 401 surfaces as `unauthorized` (the password
// gate), not as an error. `refetch` restarts from the first page (called
// after login). `loadMore` appends the next page; `hasMore` is false once a
// page comes back short.
export function useVideos(limit = 50) {
  const [videos, setVideos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState(null);
  const [unauthorized, setUnauthorized] = useState(false);
  const [fetchCount, setFetchCount] = useState(0);
  const loadingMoreRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setUnauthorized(false);

    fetchVideos(limit)
      .then((data) => {
        if (cancelled) return;
        setVideos(data);
        setHasMore(data.length === limit);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err.status === 401) setUnauthorized(true);
        else setError(err.message);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [limit, fetchCount]);

  const refetch = () => setFetchCount((count) => count + 1);

  const loadMore = async () => {
    if (loading || loadingMoreRef.current || !hasMore || videos.length === 0) return;

    loadingMoreRef.current = true;
    setLoadingMore(true);
    const lastId = videos[videos.length - 1].id;

    try {
      const page = await fetchVideos(limit, lastId);
      setVideos((current) => [...current, ...page]);
      setHasMore(page.length === limit);
    } catch (err) {
      if (err.status === 401) setUnauthorized(true);
      else setError(err.message);
    }

    loadingMoreRef.current = false;
    setLoadingMore(false);
  };

  return { videos, loading, loadingMore, hasMore, error, unauthorized, refetch, loadMore };
}
```

- [x] **Step 4:** Run: `npm run test` → expected: 36 passed. *(User commit point.)*

---

### Task 12: Infinite-scroll sentinel in `App`

**Files:**
- Create: `frontend/src/hooks/useSentinel.js`
- Modify: `frontend/src/App.jsx`, `frontend/src/index.css`
- Test: `frontend/src/hooks/useSentinel.test.jsx`

**Interfaces:**
- Consumes: `useVideos().loadMore / hasMore / loadingMore`.
- Produces: `useSentinel(onVisible) -> ref` — calls the latest `onVisible` whenever the ref'd element intersects the viewport.

- [x] **Step 1:** Write `frontend/src/hooks/useSentinel.test.jsx`:

```jsx
import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";
import { useSentinel } from "./useSentinel";

let observed;
let trigger;

class FakeObserver {
  constructor(callback) {
    trigger = (isIntersecting) => callback([{ isIntersecting }]);
  }
  observe(node) {
    observed = node;
  }
  disconnect() {
    observed = null;
  }
}

beforeEach(() => {
  observed = null;
  trigger = null;
  vi.stubGlobal("IntersectionObserver", FakeObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function Probe({ onVisible }) {
  const ref = useSentinel(onVisible);
  return <div data-testid="sentinel" ref={ref} />;
}

describe("useSentinel", () => {
  test("observes the ref'd element and fires onVisible on intersection", () => {
    const onVisible = vi.fn();
    render(<Probe onVisible={onVisible} />);

    expect(observed).not.toBeNull();
    trigger(true);
    expect(onVisible).toHaveBeenCalledTimes(1);
  });

  test("does not fire when the entry is not intersecting", () => {
    const onVisible = vi.fn();
    render(<Probe onVisible={onVisible} />);

    trigger(false);
    expect(onVisible).not.toHaveBeenCalled();
  });

  test("always calls the LATEST callback (no stale closure)", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(<Probe onVisible={first} />);

    rerender(<Probe onVisible={second} />);
    trigger(true);

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  test("disconnects on unmount", () => {
    const { unmount } = render(<Probe onVisible={vi.fn()} />);
    unmount();
    expect(observed).toBeNull();
  });
});
```

- [x] **Step 2:** Run: `npm run test` → expected: FAIL (module missing).

- [x] **Step 3:** Create `frontend/src/hooks/useSentinel.js`:

```js
import { useEffect, useRef } from "react";

// Returns a ref; whenever the ref'd element scrolls into view the latest
// onVisible is called. Used as the infinite-scroll trigger.
export function useSentinel(onVisible) {
  const nodeRef = useRef(null);
  const callbackRef = useRef(onVisible);
  callbackRef.current = onVisible;

  useEffect(() => {
    const node = nodeRef.current;
    if (!node) return;

    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) callbackRef.current();
      }
    });
    observer.observe(node);

    return () => observer.disconnect();
  }, []);

  return nodeRef;
}
```

In `App.jsx`: pull the new fields and render the sentinel after `<main>` (always rendered while the list shows — `loadMore` self-guards on `hasMore`):

```jsx
import { useSentinel } from "./hooks/useSentinel";
// ...
const { videos, loading, loadingMore, hasMore, error, unauthorized, refetch, loadMore } = useVideos();
const sentinelRef = useSentinel(loadMore);
// ... in the returned JSX, after <main>:
<div ref={sentinelRef} className="scroll-sentinel" aria-hidden="true" />
{loadingMore && <div className="page-status">Loading more…</div>}
```

In `index.css` (page section):

```css
.scroll-sentinel {
  height: 1px;
}
```

- [x] **Step 4:** Run: `npm run test` → expected: 40 passed. *(User commit point.)*

---

### Task 13: Documentation + full-suite verification

**Files:**
- Modify: `CLAUDE.md` (architecture paragraph + env vars), `.gitignore` (verify Task 1 entry), memory `uv-missing-run-tests-via-venv.md` (test counts)

- [x] **Step 1:** CLAUDE.md: replace the "Video bytes never touch local disk" intro sentence with the block-cache design; document `TG_CONNECTIONS`, `CACHE_DIR`, `CACHE_MAX_GB` as optional env vars; describe `downloader.py`, `cache.py`, `streaming.py` in the architecture section; note `/api/videos` pagination + infinite scroll.

- [x] **Step 2:** Full suites: `uv run pytest -q` (expected 87) and `npm run test` (expected 40).

- [x] **Step 3:** Re-run the Task 7 benchmark and record final numbers here.

  **Final numbers 2026-08-01:** cold ~0.7 MB/s (Telegram per-account server cap — see Task 7
  Step 5 for the full finding), warm fully-cached 143.8 MB/s. Readahead sustains the ~0.7 MB/s
  (≈5.6 Mbps) ahead of the playhead, which covers 1080p bitrates; rewatches are disk-served.

---

## Self-Review (completed)

- **Spec coverage:** parallel engine (T4), block cache (T3), readahead + orchestration (T6), wiring + fallback (T7), message TTL cache (T2), thumb cache + header (T8), pagination backend (T9) / frontend (T10–12), config (T1), docs (T13). Spec's interim Phase-1 wiring intentionally folded into T7 (noted in Global Constraints).
- **Placeholder scan:** none.
- **Type consistency:** `download_block(msg, block_idx)` consumed identically in T6 fakes; `BlockSlice(idx, start, end)` matches T5/T6; `fetchVideos(limit, beforeId)` matches T10/T11; `list_videos(limit, before_id)` matches T9 route.
