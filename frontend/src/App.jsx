import { useState } from "react";
import { useVideos } from "./hooks/useVideos";
import { useSentinel } from "./hooks/useSentinel";
import { useCacheStatus } from "./hooks/useCacheStatus";
import { VideoRow } from "./components/VideoRow";
import { PasswordGate } from "./components/PasswordGate";
import { CacheDrawer } from "./components/CacheDrawer";
import { formatSize } from "./format";

// Root component. Composes data (useVideos) with presentation: a ledger-style
// list where each row expands into an inline player. Accordion behavior —
// opening a row collapses whichever row was open before it.
export default function App() {
  const { videos, loading, loadingMore, error, unauthorized, refetch, loadMore } = useVideos();
  const { status, speedBps, togglePaused } = useCacheStatus(!loading && !unauthorized && !error);
  const [expandedId, setExpandedId] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const sentinelRef = useSentinel(loadMore);

  const toggleRow = (id) => setExpandedId((current) => (current === id ? null : id));

  if (loading) return <div className="page page-status">Loading…</div>;
  if (unauthorized)
    return (
      <div className="page">
        <PasswordGate onSuccess={refetch} />
      </div>
    );
  if (error) return <div className="page page-status">Error loading videos: {error}</div>;
  if (videos.length === 0) return <div className="page page-status">No videos found.</div>;

  return (
    <div className="page">
      <header className="ledger-header">
        <h1>Videos</h1>
        <span className="ledger-summary">
          {buildLibrarySummary(videos)}
          {status && (
            <>
              <button className="cache-header-btn" onClick={() => setDrawerOpen((open) => !open)}>
                cache {formatSize(status.total_bytes)} / {formatSize(status.max_bytes)} ▸
              </button>
              <button className={status.paused ? "cache-pause-btn paused" : "cache-pause-btn"} onClick={togglePaused}>
                {status.paused ? "▶ Resume caching" : "⏸ Pause caching"}
              </button>
            </>
          )}
        </span>
      </header>
      <main>{buildRowList(videos, expandedId, toggleRow, status)}</main>
      <div ref={sentinelRef} className="scroll-sentinel" aria-hidden="true" />
      {drawerOpen && status && (
        <CacheDrawer videos={videos} status={status} speedBps={speedBps} onClose={() => setDrawerOpen(false)} />
      )}
      {loadingMore && <div className="page-status">Loading more…</div>}
    </div>
  );
}

//---

const buildRowList = (videos, expandedId, toggleRow, status) => {
  const rows = [];
  for (const video of videos) {
    rows.push(
      <VideoRow
        key={video.id}
        video={video}
        isExpanded={video.id === expandedId}
        onToggle={toggleRow}
        cachedBytes={status?.videos?.[String(video.id)] || 0}
        isDownloading={status?.active?.msg_id === video.id}
        paused={Boolean(status?.paused)}
      />,
    );
  }
  return rows;
};

const buildLibrarySummary = (videos) => {
  let totalBytes = 0;
  for (const video of videos) {
    totalBytes += video.size;
  }
  const noun = videos.length === 1 ? "item" : "items";
  return `${videos.length} ${noun} · ${formatSize(totalBytes)}`;
};
