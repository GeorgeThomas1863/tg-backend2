import { streamUrl, thumbUrl } from "../api/client";
import { formatDate, formatDuration, formatSize } from "../format";

// Renders one video with native controls plus its metadata list.
// Pure presentation: takes a video object, knows nothing about fetching.
export function VideoPlayer({ video }) {
  return (
    <div className="player">
      <video className="player-video" src={streamUrl(video.id)} poster={thumbUrl(video.id)} controls autoPlay preload="metadata" />
      <dl className="player-meta">
        <div className="player-meta-item">
          <dt>Message</dt>
          <dd>{video.id}</dd>
        </div>
        <div className="player-meta-item">
          <dt>Date</dt>
          <dd>{formatDate(video.date)}</dd>
        </div>
        <div className="player-meta-item">
          <dt>Duration</dt>
          <dd>{formatDuration(video.duration)}</dd>
        </div>
        <div className="player-meta-item">
          <dt>Size</dt>
          <dd>{formatSize(video.size)}</dd>
        </div>
        {video.width && video.height ? (
          <div className="player-meta-item">
            <dt>Resolution</dt>
            <dd>
              {video.width}×{video.height}
            </dd>
          </div>
        ) : null}
      </dl>
    </div>
  );
}
