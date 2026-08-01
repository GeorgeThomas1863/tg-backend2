import { thumbUrl } from "../api/client";
import { VideoPlayer } from "./VideoPlayer";
import { formatDate, formatDuration, formatSize } from "../format";

// One ledger row: a clickable header line (thumb, title, mono metadata
// columns, chevron) that expands into a player panel below it. The <video>
// only mounts while the row is expanded, so exactly one stream is ever open.
export function VideoRow({ video, isExpanded, onToggle }) {
  const chevronClass = isExpanded ? "row-chevron expanded" : "row-chevron";

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
      {isExpanded && (
        <div className="row-panel">
          <VideoPlayer video={video} />
        </div>
      )}
    </div>
  );
}
