import { useState } from "react";
import { useVideos } from "./hooks/useVideos";
import { useSentinel } from "./hooks/useSentinel";
import { VideoRow } from "./components/VideoRow";
import { PasswordGate } from "./components/PasswordGate";
import { formatSize } from "./format";

// Root component. Composes data (useVideos) with presentation: a ledger-style
// list where each row expands into an inline player. Accordion behavior —
// opening a row collapses whichever row was open before it.
export default function App() {
  const { videos, loading, loadingMore, error, unauthorized, refetch, loadMore } = useVideos();
  const [expandedId, setExpandedId] = useState(null);
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
        <span className="ledger-summary">{buildLibrarySummary(videos)}</span>
      </header>
      <main>{buildRowList(videos, expandedId, toggleRow)}</main>
      <div ref={sentinelRef} className="scroll-sentinel" aria-hidden="true" />
      {loadingMore && <div className="page-status">Loading more…</div>}
    </div>
  );
}

//---

const buildRowList = (videos, expandedId, toggleRow) => {
  const rows = [];
  for (const video of videos) {
    rows.push(<VideoRow key={video.id} video={video} isExpanded={video.id === expandedId} onToggle={toggleRow} />);
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
