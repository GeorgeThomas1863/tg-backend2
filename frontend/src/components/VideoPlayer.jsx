import { useEffect, useRef } from "react";
import { streamUrl, thumbUrl } from "../api/client";
import { formatDate, formatDuration, formatSize } from "../format";

const SKIP_SECONDS = 5;

// iOS-style circular-arrow glyph with the skip amount centered inside. One
// path is drawn for "forward" (clockwise); "back" mirrors it horizontally so
// the arc and arrowhead read as counter-clockwise while the numeral, drawn
// outside the mirrored group, stays upright either way.
function SkipIcon({ direction }) {
  const arcTransform = direction === "back" ? "translate(24,0) scale(-1,1)" : undefined;
  return (
    <svg viewBox="0 0 24 24" width="48" height="48" aria-hidden="true" focusable="false">
      <g transform={arcTransform}>
        <path
          d="M16.5,4.21 A9,9 0 1,1 7.5,4.21"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        <polygon points="10.3,2.6 7.6,6.5 5.6,3.0" fill="currentColor" />
      </g>
      <text
        x="12"
        y="12.5"
        textAnchor="middle"
        dominantBaseline="central"
        fontSize="8.5"
        fontWeight="600"
        fill="currentColor"
      >
        {SKIP_SECONDS}
      </text>
    </svg>
  );
}

function SkipButton({ direction, onClick }) {
  const label = `${direction === "back" ? "Back" : "Forward"} ${SKIP_SECONDS} seconds`;
  return (
    <button type="button" className={`player-skip player-skip-${direction}`} onClick={onClick} aria-label={label}>
      <SkipIcon direction={direction} />
    </button>
  );
}

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
        <SkipButton direction="back" onClick={() => skip(-SKIP_SECONDS)} />
        <SkipButton direction="forward" onClick={() => skip(SKIP_SECONDS)} />
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
