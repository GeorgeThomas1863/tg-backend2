import { useEffect, useState } from "react";
import { useVideos } from "./hooks/useVideos";
import { useSentinel } from "./hooks/useSentinel";
import { useVisibleVideos } from "./hooks/useVisibleVideos";
import { isVideoDownloading, useCacheStatus } from "./hooks/useCacheStatus";
import { useChannels } from "./hooks/useChannels";
import { useTelegramAuth } from "./hooks/useTelegramAuth";
import { useCategories } from "./hooks/useCategories";
import { VideoRow } from "./components/VideoRow";
import { PasswordGate } from "./components/PasswordGate";
import { CacheDrawer } from "./components/CacheDrawer";
import { ChannelDrawer } from "./components/ChannelDrawer";
import { JumpControls } from "./components/JumpControls";
import { TelegramAuthDrawer } from "./components/TelegramAuthDrawer";
import { CategoryBar } from "./components/CategoryBar";
import { AlphaCategoryBar } from "./components/AlphaCategoryBar";
import { SortControl } from "./components/SortControl";
import { FloodAlert } from "./components/FloodAlert";
import { BulkCacheButton } from "./components/BulkCacheButton";
import { formatAgo, formatSize } from "./format";

const SEARCH_DEBOUNCE_MS = 300;

export default function App() {
  const registry = useChannels();
  const telegram = useTelegramAuth();
  const [channelDrawerOpen, setChannelDrawerOpen] = useState(false);
  const [telegramDrawerOpen, setTelegramDrawerOpen] = useState(false);
  const [libraryGeneration, setLibraryGeneration] = useState(0);
  const siteUnauthorized = telegram.error === "HTTP 401";
  const telegramAuthorized = telegram.status?.authorized === true;
  const telegramLoggedOut = telegram.status?.authorized === false;
  const telegramStatusFailed = !telegram.loading && !telegram.status && !siteUnauthorized;
  const showChannelDrawer = telegramAuthorized && (channelDrawerOpen || (!registry.loading && !registry.error && registry.channels.length === 0));
  const telegramLabel = buildTelegramLabel(telegram.status);

  async function loadChannel(id) {
    const result = await registry.activate(id);
    if (result.success) {
      setLibraryGeneration((generation) => generation + 1);
      setChannelDrawerOpen(false);
    }
    return result;
  }

  return (
    <>
      {siteUnauthorized && <div className="page"><PasswordGate onSuccess={() => { telegram.refresh(); registry.refresh(); }} /></div>}
      {!siteUnauthorized && telegram.loading && <div className="page page-status">Loading…</div>}
      {!siteUnauthorized && telegramStatusFailed && (
        <div className="page page-status">
          Error loading Telegram status: {telegram.error || "Status unavailable"}
        </div>
      )}
      {!siteUnauthorized && telegramLoggedOut && (
        <LoggedOutLibrary telegramLabel={telegramLabel} onOpenTelegram={() => setTelegramDrawerOpen(true)} />
      )}
      {!siteUnauthorized && telegramAuthorized && (
        <VideoLibrary
          key={`${registry.active?.id || "no-channel"}:${libraryGeneration}:authorized`}
          activeChannel={registry.active}
          onOpenChannels={() => setChannelDrawerOpen(true)}
          onAuthed={() => { registry.refresh(); telegram.refresh(); }}
          telegramLabel={telegramLabel}
          onOpenTelegram={() => setTelegramDrawerOpen(true)}
        />
      )}
      {showChannelDrawer && (
        <ChannelDrawer
          channels={registry.channels}
          busy={registry.busy}
          error={registry.error}
          onLoad={loadChannel}
          onMakeDefault={registry.makeDefault}
          onRemove={registry.remove}
          onAdd={registry.add}
          onClose={() => setChannelDrawerOpen(false)}
        />
      )}
      {telegramDrawerOpen && !siteUnauthorized && (
        <TelegramAuthDrawer
          status={telegram.status}
          busy={telegram.busy}
          error={telegram.error}
          onSendCode={telegram.sendCode}
          onSubmitCode={telegram.submitCode}
          onSubmitPassword={telegram.submitPassword}
          onLogout={telegram.logout}
          onClose={() => setTelegramDrawerOpen(false)}
        />
      )}
    </>
  );
}

function VideoLibrary({ activeChannel, onOpenChannels, onAuthed, telegramLabel, onOpenTelegram }) {
  const [selectedCategory, setSelectedCategory] = useState(null);
  const [sortDirection, setSortDirection] = useState("asc");
  const [searchInput, setSearchInput] = useState("");
  const [searchTerm, setSearchTerm] = useDebouncedValue(searchInput.trim(), SEARCH_DEBOUNCE_MS);
  const categoryData = useCategories();
  const selectedCategoryData = findCategory(categoryData.categories, selectedCategory);
  const { videos, total, loading, loadingMore, error, unauthorized, refetch, jumpTo, loadMore } = useVideos(50, selectedCategory, searchTerm, sortDirection);
  const { status, speedBps, togglePaused, saveSettings, clearCache } = useCacheStatus(!loading && !unauthorized && !error);
  const [expandedId, setExpandedId] = useState(null);
  const [cacheDrawerOpen, setCacheDrawerOpen] = useState(true);
  const cacheDrawerVisible = cacheDrawerOpen && Boolean(status);
  useEffect(() => {
    document.body.classList.toggle("cache-panel-open", cacheDrawerVisible);
    return () => document.body.classList.remove("cache-panel-open");
  }, [cacheDrawerVisible]);
  const sentinelRef = useSentinel(loadMore);
  const observeRow = useVisibleVideos(videos);
  const toggleRow = (id) => setExpandedId((current) => (current === id ? null : id));
  const jumpToPosition = (offset) => {
    setExpandedId(null);
    jumpTo(offset);
  };
  const clearSearch = () => {
    setSearchInput("");
    setSearchTerm("");
  };

  if (loading) return <div className="page page-status">Loading…</div>;
  if (unauthorized) return <div className="page"><PasswordGate onSuccess={() => { onAuthed(); refetch(); }} /></div>;
  if (error) return <div className="page page-status">Error loading videos: {error}</div>;

  return (
    <div className="page">
      <FloodAlert flood={status?.flood} paused={status?.paused} onPause={togglePaused} />
      <header className="ledger-header">
        <div className="ledger-heading">
          <h1>Videos</h1>
          {selectedCategoryData && (
            <span className="ledger-category-summary">
              {selectedCategoryData.name} · {selectedCategoryData.count.toLocaleString()} videos
            </span>
          )}
          <button className="channel-header-btn" onClick={onOpenChannels}>
            {activeChannel?.title || "Add a channel"} ▸
          </button>
        </div>
        <div className="ledger-summary">
          {buildLibrarySummary(videos)}
          {total !== null && <span>{total.toLocaleString()} videos</span>}
          <JumpControls total={total} disabled={loading} onJump={jumpToPosition} />
          {status && (
            <>
              <button className="cache-header-btn" onClick={() => setCacheDrawerOpen((open) => !open)}>
                cache {formatSize(status.total_bytes)} / {formatSize(status.max_bytes)} ▸
              </button>
              <button className={status.paused ? "cache-pause-btn paused" : "cache-pause-btn"} onClick={togglePaused}>
                {status.paused ? "▶ Resume caching" : "⏸ Pause caching"}
              </button>
              {status.flood?.count > 0 && (
                <span className="flood-badge" title="Telegram transport-level 429 incidents since backend start">
                  429 ×{status.flood.count} · {formatAgo(status.flood.last_seconds_ago)}
                </span>
              )}
            </>
          )}
          <TelegramTrigger label={telegramLabel} onClick={onOpenTelegram} />
        </div>
      </header>
      <div className="category-bar-row">
        <CategoryBar
          categories={categoryData.categories}
          loading={categoryData.loading}
          selectedKey={selectedCategory}
          onSelect={setSelectedCategory}
        />
        <AlphaCategoryBar
          categories={categoryData.categories}
          loading={categoryData.loading}
          selectedKey={selectedCategory}
          onSelect={setSelectedCategory}
        />
        <SortControl value={sortDirection} onChange={setSortDirection} disabled={Boolean(searchTerm)} />
        <VideoSearchInput value={searchInput} onChange={setSearchInput} onClear={clearSearch} />
        <BulkCacheButton
          selectedCategory={selectedCategory}
          selectedCategoryLabel={selectedCategoryData?.name}
          videoCount={selectedCategoryData?.count}
          batch={status?.batch}
          searchActive={Boolean(searchTerm)}
        />
      </div>
      <main key={`${selectedCategory || "all-videos"}:${sortDirection}`}>
        {videos.length === 0
          ? <div className="page-status">No videos found.</div>
          : buildRowList(videos, expandedId, toggleRow, status, observeRow)}
      </main>
      <div ref={sentinelRef} className="scroll-sentinel" aria-hidden="true" />
      {cacheDrawerVisible && (
        <CacheDrawer
          videos={videos}
          status={status}
          speedBps={speedBps}
          onClose={() => setCacheDrawerOpen(false)}
          onSaveSettings={saveSettings}
          onClearCache={clearCache}
        />
      )}
      {loadingMore && <div className="page-status">Loading more…</div>}
    </div>
  );
}

function LoggedOutLibrary({ telegramLabel, onOpenTelegram }) {
  return (
    <div className="page">
      <header className="ledger-header">
        <div className="ledger-heading"><h1>Videos</h1></div>
        <div className="ledger-summary"><TelegramTrigger label={telegramLabel} onClick={onOpenTelegram} /></div>
      </header>
      <main className="page-status telegram-logged-out">
        <p>Telegram is logged out. Log in to load and stream videos.</p>
        <button className="telegram-login-button" type="button" onClick={onOpenTelegram}>Log in</button>
      </main>
    </div>
  );
}

function TelegramTrigger({ label, onClick }) {
  return <button className="telegram-header-btn" type="button" onClick={onClick}>{label} ▸</button>;
}

function VideoSearchInput({ value, onChange, onClear }) {
  return (
    <div className="video-search-wrap">
      <input
        type="search"
        className="video-search-input"
        placeholder="Search captions…"
        aria-label="Search videos"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      {value && (
        <button type="button" className="video-search-clear" aria-label="Clear search" onClick={onClear}>
          ×
        </button>
      )}
    </div>
  );
}

// Debounces rawValue by delayMs (useHoverPreview's timer-effect pattern).
// Returns [debounced, setDebounced] so callers can also force an immediate
// value (e.g. a clear button) without waiting out the delay.
function useDebouncedValue(rawValue, delayMs) {
  const [debounced, setDebounced] = useState(rawValue);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(rawValue), delayMs);
    return () => clearTimeout(timer);
  }, [rawValue, delayMs]);

  return [debounced, setDebounced];
}

function buildTelegramLabel(status) {
  if (!status?.authorized) return "Telegram · logged out";
  return `Telegram · ${status.user?.username || status.user?.phone || "connected"}`;
}

const buildRowList = (videos, expandedId, toggleRow, status, observeRow) => videos.map((video) => (
  <VideoRow
    key={video.id}
    video={video}
    isExpanded={video.id === expandedId}
    onToggle={toggleRow}
    cachedBytes={status?.videos?.[String(video.id)] || 0}
    isDownloading={isVideoDownloading(status, video.id)}
    paused={Boolean(status?.paused)}
    rowRef={observeRow(video.id)}
  />
));

const buildLibrarySummary = (videos) => {
  let totalBytes = 0;
  for (const video of videos) totalBytes += video.size;
  return `${videos.length} loaded · ${formatSize(totalBytes)}`;
};

function findCategory(categories, selectedKey) {
  if (!selectedKey) return null;
  for (const category of categories) {
    if (category.key === selectedKey) return category;
    for (const sub of category.subs || []) {
      if (sub.key === selectedKey) return sub;
    }
  }
  return null;
}
