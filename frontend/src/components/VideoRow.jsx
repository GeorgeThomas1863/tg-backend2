import { thumbUrl } from "../api/client";
import { VideoPlayer } from "./VideoPlayer";
import { formatDate, formatDuration, formatSize } from "../format";

// One ledger row: a clickable header line (thumb, title, mono metadata
// columns, chevron) that expands into a player panel below it. The <video>
// only mounts while the row is expanded, so exactly one stream is ever open.
export function VideoRow({
  video,
  isExpanded,
  onToggle,
  cachedBytes = 0,
  isDownloading = false,
  paused = false,
}) {
  const chevronClass = isExpanded ? "row-chevron expanded" : "row-chevron";
  const pct = video.size > 0 ? Math.min(100, Math.round((cachedBytes / video.size) * 100)) : 0;
  const fillClass = isDownloading && !paused && pct < 100 ? "cache-strip-fill downloading" : "cache-strip-fill";
  const label = buildCacheLabel(pct, isDownloading, paused);

  return (
    <div className="video-row">
      <button className="row-header" aria-expanded={isExpanded} onClick={() => onToggle(video.id)}>
        <img className="row-thumb" src={thumbUrl(video.id)} alt="" loading="lazy" />
        <span className="row-title">{video.name}</span>
        <span className="row-col row-col-wide">{formatDate(video.date)}</span>
        <span className="row-col">{formatDuration(video.duration)}</span>
        <span className="row-col row-col-wide">{formatSize(video.size)}</span>
        <span className={chevronClass} aria-hidden="true">
          ▸
        </span>
      </button>
      <div className="cache-strip">
        <div className="cache-strip-track">
          {pct > 0 && <div className={fillClass} style={{ width: `${pct}%` }} />}
        </div>
        <span className="cache-strip-label">{label}</span>
      </div>
      {isExpanded && (
        <div className="row-panel">
          <VideoPlayer video={video} />
        </div>
      )}
    </div>
  );
}

//---

function buildCacheLabel(pct, isDownloading, paused) {
  if (pct >= 100) return "cached";
  if (isDownloading && paused) return `${pct}% paused`;
  if (isDownloading) return `${pct}% ↓`;
  if (pct === 0) return "—";
  return `${pct}%`;
}
