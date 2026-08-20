import { useEffect, useRef } from "react";
import { streamUrl, thumbUrl } from "../api/client";
import { formatDate, formatDuration, formatSize } from "../format";

const SKIP_SECONDS = 5;

// Renders one video with native controls plus its metadata list.
// Pure presentation: takes a video object, knows nothing about fetching.
export function VideoPlayer({ video }) {
  const videoRef = useRef(null);

  useEffect(() => {
    if (!videoRef.current) return;
    videoRef.current.volume = 0.5;
  }, []);

  function skip(deltaSeconds) {
    const el = videoRef.current;
    if (!el) return;
    el.currentTime = Math.max(0, el.currentTime + deltaSeconds);
  }

  return (
    <div className="player">
      <div className="player-video-wrap">
        <video
          ref={videoRef}
          className="player-video"
          src={streamUrl(video.id)}
          poster={thumbUrl(video.id)}
          controls
          autoPlay
          preload="metadata"
        />
        <button type="button" className="player-skip player-skip-back" onClick={() => skip(-SKIP_SECONDS)}>
          « 5s
        </button>
        <button type="button" className="player-skip player-skip-forward" onClick={() => skip(SKIP_SECONDS)}>
          5s »
        </button>
      </div>
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
