import { useState } from "react";
import { useVideos } from "./hooks/useVideos";
import { useSentinel } from "./hooks/useSentinel";
import { useCacheStatus } from "./hooks/useCacheStatus";
import { useChannels } from "./hooks/useChannels";
import { VideoRow } from "./components/VideoRow";
import { PasswordGate } from "./components/PasswordGate";
import { CacheDrawer } from "./components/CacheDrawer";
import { ChannelDrawer } from "./components/ChannelDrawer";
import { formatSize } from "./format";

export default function App() {
  const registry = useChannels();
  const [channelDrawerOpen, setChannelDrawerOpen] = useState(false);
  const [libraryGeneration, setLibraryGeneration] = useState(0);
  const showChannelDrawer = channelDrawerOpen || (!registry.loading && !registry.error && registry.channels.length === 0);

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
      <VideoLibrary
        key={`${registry.active?.id || "no-channel"}:${libraryGeneration}`}
        activeChannel={registry.active}
        onOpenChannels={() => setChannelDrawerOpen(true)}
        onAuthed={registry.refresh}
      />
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
    </>
  );
}

function VideoLibrary({ activeChannel, onOpenChannels, onAuthed }) {
  const { videos, loading, loadingMore, error, unauthorized, refetch, loadMore } = useVideos();
  const { status, speedBps, togglePaused, saveSettings, clearCache } = useCacheStatus(!loading && !unauthorized && !error);
  const [expandedId, setExpandedId] = useState(null);
  const [cacheDrawerOpen, setCacheDrawerOpen] = useState(false);
  const sentinelRef = useSentinel(loadMore);
  const toggleRow = (id) => setExpandedId((current) => (current === id ? null : id));

  if (loading) return <div className="page page-status">Loading…</div>;
  if (unauthorized) return <div className="page"><PasswordGate onSuccess={() => { onAuthed(); refetch(); }} /></div>;
  if (error) return <div className="page page-status">Error loading videos: {error}</div>;

  return (
    <div className="page">
      <header className="ledger-header">
        <div className="ledger-heading">
          <h1>Videos</h1>
          <button className="channel-header-btn" onClick={onOpenChannels}>
            {activeChannel?.title || "Add a channel"} ▸
          </button>
        </div>
        <span className="ledger-summary">
          {buildLibrarySummary(videos)}
          {status && (
            <>
              <button className="cache-header-btn" onClick={() => setCacheDrawerOpen((open) => !open)}>
                cache {formatSize(status.total_bytes)} / {formatSize(status.max_bytes)} ▸
              </button>
              <button className={status.paused ? "cache-pause-btn paused" : "cache-pause-btn"} onClick={togglePaused}>
                {status.paused ? "▶ Resume caching" : "⏸ Pause caching"}
              </button>
            </>
          )}
        </span>
      </header>
      <main>
        {videos.length === 0
          ? <div className="page-status">No videos found.</div>
          : buildRowList(videos, expandedId, toggleRow, status)}
      </main>
      <div ref={sentinelRef} className="scroll-sentinel" aria-hidden="true" />
      {cacheDrawerOpen && status && (
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

const buildRowList = (videos, expandedId, toggleRow, status) => videos.map((video) => (
  <VideoRow
    key={video.id}
    video={video}
    isExpanded={video.id === expandedId}
    onToggle={toggleRow}
    cachedBytes={status?.videos?.[String(video.id)] || 0}
    isDownloading={status?.active?.msg_id === video.id}
    paused={Boolean(status?.paused)}
  />
));

const buildLibrarySummary = (videos) => {
  let totalBytes = 0;
  for (const video of videos) totalBytes += video.size;
  const noun = videos.length === 1 ? "item" : "items";
  return `${videos.length} ${noun} · ${formatSize(totalBytes)}`;
};
