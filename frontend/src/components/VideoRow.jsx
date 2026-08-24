import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { previewStreamUrl, requestPriorityCache, thumbUrl } from "../api/client";
import { VideoPlayer } from "./VideoPlayer";
import { useHoverPreview } from "../hooks/useHoverPreview";
import { formatDate, formatDuration, formatSize } from "../format";

export const PREVIEW_POPUP_WIDTH = 720;
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
  const showQueueButton = pct === 0 && !isDownloading;
  const label = buildCacheLabel(pct, isDownloading, paused);
  const rowTitle = video.caption || video.name;
  const metaLine = buildMetaLine(video);
  const [previewProgress, setPreviewProgress] = useState(0);
  const { previewing, onMouseEnter, onMouseLeave, onPopupEnter, onPopupLeave } = useHoverPreview(isExpanded);
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
        {showQueueButton ? (
          <CacheQueueButton video={video} />
        ) : (
          <span className="cache-strip-label">{label}</span>
        )}
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
            onMouseEnter={onPopupEnter}
            onMouseLeave={onPopupLeave}
            onClick={(event) => event.stopPropagation()}
          >
            <video
              className="preview-popup-video"
              src={previewStreamUrl(video.id, buildPreviewStart(video.duration))}
              poster={thumbUrl(video.id)}
              muted
              autoPlay
              loop
              playsInline
              onClick={(event) => seekPreview(event, video.duration)}
              onTimeUpdate={(event) => setPreviewProgress(buildPreviewProgress(event.currentTarget, video.duration))}
            />
            <div className="preview-popup-progress" aria-hidden="true">
              <div className="preview-popup-progress-fill" style={{ width: `${previewProgress}%` }} />
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}

//---

// pct === 0 && !isDownloading never reaches here — the row renders
// CacheQueueButton in that slot instead of this text label.
function buildCacheLabel(pct, isDownloading, paused) {
  if (pct >= 100) return "cached";
  if (isDownloading && paused) return `${pct}% paused`;
  if (isDownloading) return `${pct}% ↓`;
  return `${pct}%`;
}

// The cache-status poll runs every 3000ms (see App.jsx); a video whose
// download the backend actually accepted should show real progress within
// a few of those polls. QUEUE_GRACE_MS is that allowance.
export const QUEUE_GRACE_MS = 15000;

// Replaces the cache-strip's "—" placeholder for an uncached, idle video: a
// real button that jumps this video to the front of the download queue
// (POST /api/prefetch/priority, same endpoint CacheDrawer's "cache now"
// button uses). stopPropagation guards against the row-header's onToggle
// and hover-preview handlers even though the strip sits outside that
// button's subtree today, so a future markup change can't silently wire
// this click into row expansion.
//
// The backend can report success for a job it silently drops later (e.g. a
// video larger than the cache cap, discovered only when the worker gets to
// it) — see prefetch.py's select_priority_job. Without a bound, "queued"
// would then never clear: no failure ever arrives, and the parent keeps
// rendering this button forever since pct stays 0 and isDownloading stays
// false. The grace timer below is the fallback that unsticks it regardless
// of the backend's own validation, since VideoRow (not this component)
// still unmounts the button the normal way the moment real progress shows.
function CacheQueueButton({ video }) {
  const [queued, setQueued] = useState(false);
  const [error, setError] = useState("");
  const graceTimer = useRef(null);

  useEffect(() => clearGraceTimer, []);

  function clearGraceTimer() {
    if (graceTimer.current === null) return;
    clearTimeout(graceTimer.current);
    graceTimer.current = null;
  }

  async function queueNow(event) {
    event.stopPropagation();
    setQueued(true);
    setError("");
    clearGraceTimer();
    graceTimer.current = setTimeout(() => {
      failQueue("No download progress yet — try again.");
    }, QUEUE_GRACE_MS);
    try {
      const result = await requestPriorityCache(video.id);
      if (!result?.success) failQueue(result?.message || "Unable to queue this video.");
    } catch (e) {
      failQueue(e.message || "Unable to queue this video.");
    }
  }

  function failQueue(message) {
    clearGraceTimer();
    setQueued(false);
    setError(message);
  }

  const name = video.caption || video.name;
  const title = error || "Download now — jumps to the front of the queue";

  return (
    <button
      type="button"
      className={queued ? "cache-strip-queue is-queued" : "cache-strip-queue"}
      aria-label={`Download ${name} now`}
      title={title}
      disabled={queued}
      onClick={queueNow}
    >
      {queued ? "…" : "+"}
    </button>
  );
}

function buildPreviewStart(duration) {
  if (!duration || duration <= 0) return 0;
  return Math.floor(duration * 0.25);
}

function seekPreview(event, fallbackDuration) {
  const preview = event.currentTarget;
  const rect = preview.getBoundingClientRect();
  if (!(rect.width > 0)) return;
  const duration = getPreviewDuration(preview, fallbackDuration);
  if (!(duration > 0)) return;
  const ratio = clamp((event.clientX - rect.left) / rect.width, 0, 1);
  preview.currentTime = ratio * duration;
  preview.play()?.catch(() => {});
}

function buildPreviewProgress(preview, fallbackDuration) {
  const duration = getPreviewDuration(preview, fallbackDuration);
  if (!(duration > 0)) return 0;
  return clamp((preview.currentTime / duration) * 100, 0, 100);
}

function getPreviewDuration(preview, fallbackDuration) {
  if (Number.isFinite(preview.duration)) return preview.duration;
  return Number.isFinite(fallbackDuration) ? fallbackDuration : 0;
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
  // Mirrors index.css (width: min(720px, calc(100vw - 24px)); aspect-ratio:
  // 16 / 9) so the position math never assumes a larger popup than renders.
  const popupWidth = Math.min(PREVIEW_POPUP_WIDTH, window.innerWidth - PREVIEW_POPUP_GAP * 2);
  const popupHeight = (popupWidth * 9) / 16;
  const left = clamp(
    rect.left + ROW_THUMB_WIDTH + PREVIEW_POPUP_GAP,
    PREVIEW_POPUP_GAP,
    window.innerWidth - popupWidth - PREVIEW_POPUP_GAP,
  );
  // A popup taller than the viewport keeps its top edge on-screen rather
  // than getting a negative top from an inverted clamp.
  const top = clamp(
    rect.top + rect.height / 2 - popupHeight / 2,
    PREVIEW_POPUP_GAP,
    Math.max(PREVIEW_POPUP_GAP, window.innerHeight - popupHeight - PREVIEW_POPUP_GAP),
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
