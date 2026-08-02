import { formatSize } from "../format";

export function CacheDrawer({ videos, status, speedBps, onClose }) {
  if (status == null) return null;

  const gaugePct = buildPercentage(status.total_bytes, status.max_bytes);

  return (
    <aside className="cache-drawer">
      <div className="cache-drawer-title">
        Cache
        <button className="cache-drawer-close" onClick={onClose}>×</button>
      </div>
      <div className="cache-drawer-total">
        {formatSize(status.total_bytes)} / {formatSize(status.max_bytes)} used
      </div>
      <div className="cache-drawer-gauge">
        <div className="cache-drawer-gauge-fill" style={{ width: `${gaugePct}%` }} />
      </div>
      {buildActiveCard(videos, status, speedBps)}
      {buildItems(videos, status)}
    </aside>
  );
}

//---

function buildActiveCard(videos, status, speedBps) {
  if (status.paused) {
    return <div className="cache-drawer-active">Background caching is paused</div>;
  }
  if (!status.active) {
    return <div className="cache-drawer-active">Idle</div>;
  }

  const activeVideo = findVideo(videos, status.active.msg_id);
  const name = activeVideo?.name || `video_${status.active.msg_id}`;
  const cachedBytes = status.videos[String(status.active.msg_id)] || 0;
  const pct = buildPercentage(cachedBytes, activeVideo?.size);
  const speed = speedBps == null ? "" : ` · ≈ ${(speedBps / (1024 * 1024)).toFixed(1)} MB/s`;
  const tierLabel = buildTierLabel(status.active.tier);

  return (
    <div className="cache-drawer-active">
      <div>Downloading {name}</div>
      <div>{pct}%{speed} · {tierLabel}</div>
    </div>
  );
}

function buildItems(videos, status) {
  const items = [];

  for (const video of videos) {
    items.push(
      <div className="cache-drawer-item" key={video.id}>
        <div className="cache-drawer-item-name">{video.name}</div>
        <div className="cache-drawer-item-state">{buildItemState(video, status)}</div>
      </div>,
    );
  }

  return items;
}

function buildItemState(video, status) {
  const cachedBytes = status.videos[String(video.id)] || 0;
  const pct = buildPercentage(cachedBytes, video.size);
  const downloading = status.active?.msg_id === video.id;

  if (pct >= 100) return "cached";
  if (downloading && status.paused) return `${pct}% paused`;
  if (downloading) return `${pct}% ↓`;
  if (pct === 0) return "—";
  return `${pct}%`;
}

function findVideo(videos, id) {
  for (const video of videos) {
    if (video.id === id) return video;
  }
  return null;
}

function buildPercentage(cachedBytes, totalBytes) {
  if (!(totalBytes > 0)) return 0;
  return Math.min(100, Math.round((cachedBytes / totalBytes) * 100));
}

function buildTierLabel(tier) {
  if (tier === "pin") return "finishing current video";
  if (tier === "prewarm") return "prewarming library";
  return "";
}
