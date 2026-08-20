import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { previewStreamUrl, thumbUrl } from "../api/client";
import { VideoPlayer } from "./VideoPlayer";
import { useHoverPreview } from "../hooks/useHoverPreview";
import { formatDate, formatDuration, formatSize } from "../format";

const PREVIEW_POPUP_WIDTH = 360;
const PREVIEW_POPUP_HEIGHT = (PREVIEW_POPUP_WIDTH * 9) / 16;
const PREVIEW_POPUP_GAP = 12;
const ROW_THUMB_WIDTH = 96;

// One ledger row: a clickable header line (thumb, title, mono metadata
// columns, chevron) that expands into a player panel below it. The <video>
// only mounts while the row is expanded, so exactly one stream is ever open.
// rowRef lets App observe the row for on-screen cache prioritization.
export function VideoRow({
  video,
  isExpanded,
  onToggle,
  cachedBytes = 0,
  isDownloading = false,
  paused = false,
  rowRef = null,
}) {
  const chevronClass = isExpanded ? "row-chevron expanded" : "row-chevron";
  const pct = video.size > 0 ? Math.min(100, Math.round((cachedBytes / video.size) * 100)) : 0;
  const fillClass = isDownloading && !paused && pct < 100 ? "cache-strip-fill downloading" : "cache-strip-fill";
  const label = buildCacheLabel(pct, isDownloading, paused);
  const rowTitle = video.caption || video.name;
  const metaLine = buildMetaLine(video);
  const { previewing, onMouseEnter, onMouseLeave } = useHoverPreview(isExpanded);
  // Never trust the hook's previewing state alone: it only tears down via a
  // deferred effect, so an expand-click could otherwise commit the header
  // preview and the row-panel player in the same paint. Gating on isExpanded
  // here too closes that gap synchronously, in the same render.
  const showPreview = previewing && !isExpanded;
  const headerRef = useRef(null);
  const popupPosition = usePopupPosition(showPreview, headerRef);

  return (
    <div className="video-row" ref={rowRef}>
      <button
        className="row-header"
        aria-expanded={isExpanded}
        onClick={() => onToggle(video.id)}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
        ref={headerRef}
      >
        <img className="row-thumb" src={thumbUrl(video.id)} alt="" loading="lazy" />
        <span className="row-title-wrap">
          <span className="row-title" title={video.caption ? video.name : undefined}>
            {rowTitle}
          </span>
          {metaLine && <span className="row-meta">{metaLine}</span>}
        </span>
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
      {showPreview &&
        popupPosition &&
        createPortal(
          <div
            className="preview-popup"
            style={{ top: `${popupPosition.top}px`, left: `${popupPosition.left}px` }}
          >
            <video
              className="preview-popup-video"
              src={previewStreamUrl(video.id, buildPreviewStart(video.duration))}
              poster={thumbUrl(video.id)}
              muted
              autoPlay
              loop
              playsInline
            />
          </div>,
          document.body,
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

function buildPreviewStart(duration) {
  if (!duration || duration <= 0) return 0;
  return Math.floor(duration * 0.25);
}

// Keeps the popup's position pinned to the row header while it's open.
// getBoundingClientRect is only render-accurate at the instant showPreview
// flips true, so scroll/resize must recompute it live — mouseleave alone
// doesn't fire reliably under wheel scroll, leaving a stale popup otherwise.
// Scroll listens in the capture phase since the scroll can originate on an
// ancestor container, not just window, and both listeners are throttled
// through requestAnimationFrame so rapid events don't thrash layout.
function usePopupPosition(showPreview, headerRef) {
  const [position, setPosition] = useState(null);

  // Synchronous, before paint: avoids a blank first frame where the popup
  // would otherwise render unpositioned while a regular effect is pending.
  useLayoutEffect(() => {
    if (!showPreview) {
      setPosition(null);
      return;
    }
    setPosition(computePopupPosition(headerRef.current));
  }, [showPreview, headerRef]);

  useEffect(() => {
    if (!showPreview) return;

    let frame = null;
    const recompute = () => {
      frame = null;
      setPosition(computePopupPosition(headerRef.current));
    };
    const onScrollOrResize = () => {
      if (frame !== null) return;
      frame = requestAnimationFrame(recompute);
    };
    window.addEventListener("scroll", onScrollOrResize, { capture: true, passive: true });
    window.addEventListener("resize", onScrollOrResize);
    return () => {
      if (frame !== null) cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScrollOrResize, { capture: true });
      window.removeEventListener("resize", onScrollOrResize);
    };
  }, [showPreview, headerRef]);

  return position;
}

// Places the popup to the right of the row's thumb, vertically centered on
// the row header, then clamps both axes so it never runs off the viewport.
function computePopupPosition(headerEl) {
  if (!headerEl) return null;
  const rect = headerEl.getBoundingClientRect();
  // Mirrors index.css's mobile clamp (min(360px, calc(100vw - 24px))) so the
  // position math never assumes more width than the popup actually renders at.
  const popupWidth = Math.min(PREVIEW_POPUP_WIDTH, window.innerWidth - PREVIEW_POPUP_GAP * 2);
  const left = clamp(
    rect.left + ROW_THUMB_WIDTH + PREVIEW_POPUP_GAP,
    PREVIEW_POPUP_GAP,
    window.innerWidth - popupWidth - PREVIEW_POPUP_GAP,
  );
  const top = clamp(
    rect.top + rect.height / 2 - PREVIEW_POPUP_HEIGHT / 2,
    PREVIEW_POPUP_GAP,
    window.innerHeight - PREVIEW_POPUP_HEIGHT - PREVIEW_POPUP_GAP,
  );
  return { top, left };
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

// Below the caption title: the file name (when the caption displaced it)
// and the original post date, joined so either can appear alone. Returns
// "" when there is nothing to show, so the row-meta span is skipped and
// caption-less, posted_ts-less rows render exactly as they did before.
function buildMetaLine(video) {
  const parts = [];
  if (video.caption) parts.push(video.name);
  const posted = formatPostedDate(video.posted_ts);
  if (posted) parts.push(`posted ${posted}`);
  return parts.join(" · ");
}

function formatPostedDate(postedTs) {
  if (postedTs == null || !Number.isFinite(postedTs)) return "";
  return formatDate(new Date(postedTs * 1000).toISOString());
}
