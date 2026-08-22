import { useState } from "react";
import { formatSize } from "../format";
import { requestPriorityCache } from "../api/client";
import { getActiveSlots, isVideoDownloading } from "../hooks/useCacheStatus";

const LOCATION_WARNING = "Changing the location wipes the current cache. Continue?";
const CLEAR_WARNING = "Delete all cached data? Videos will re-download as needed.";

export function CacheDrawer({ videos, status, speedBps, onClose, onSaveSettings, onClearCache }) {
  const [queuedIds, setQueuedIds] = useState(() => new Set());
  const [cacheNowError, setCacheNowError] = useState("");

  if (status == null) return null;

  const gaugePct = buildPercentage(status.total_bytes, status.max_bytes);

  async function cacheNow(id) {
    setQueuedIds((prev) => new Set(prev).add(id));
    setCacheNowError("");
    try {
      const result = await requestPriorityCache(id);
      if (!result?.success) failCacheNow(id, result?.message || "Unable to queue this video.");
    } catch (e) {
      failCacheNow(id, e.message || "Unable to queue this video.");
    }
  }

  function failCacheNow(id, message) {
    setQueuedIds((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
    setCacheNowError(message);
  }

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
      <CacheSettings status={status} onSaveSettings={onSaveSettings} onClearCache={onClearCache} />
      {buildActiveCard(videos, status, speedBps)}
      {cacheNowError && <div className="cache-drawer-settings-error" role="alert">{cacheNowError}</div>}
      <div className="cache-drawer-list">
        {buildItems(videos, status, queuedIds, cacheNow)}
      </div>
    </aside>
  );
}

//---

function CacheSettings({ status, onSaveSettings, onClearCache }) {
  const [sizeInput, setSizeInput] = useState(() => String(status.max_gb));
  const [folderInput, setFolderInput] = useState(() => status.cache_dir);
  const [confirmingFolder, setConfirmingFolder] = useState(false);
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [savingRow, setSavingRow] = useState(null);
  const [error, setError] = useState("");

  function editSize(event) {
    setSizeInput(event.target.value);
    setError("");
  }

  function editFolder(event) {
    setFolderInput(event.target.value);
    setConfirmingFolder(false);
    setError("");
  }

  async function saveSize() {
    const size = Number(sizeInput);
    if (!Number.isFinite(size) || size <= 0) {
      setError("Max size must be greater than 0.");
      return;
    }
    await saveFields("size", { cache_max_gb: size });
  }

  async function saveFolder() {
    setConfirmingFolder(false);
    await saveFields("folder", { cache_dir: folderInput.trim() });
  }

  async function clearCache() {
    setConfirmingClear(false);
    await runAction("clear", onClearCache, "Unable to clear cache.");
  }

  async function saveFields(row, fields) {
    await runAction(row, () => onSaveSettings(fields), "Unable to save cache settings.");
  }

  async function runAction(row, action, fallbackMessage) {
    setSavingRow(row);
    const result = await action();
    setSavingRow(null);
    setError(result?.success ? "" : result?.message || fallbackMessage);
  }

  return (
    <section className="cache-drawer-settings" aria-label="Cache settings">
      <CacheSettingRow label="Max size (GB)" type="number" value={sizeInput} onChange={editSize} onSave={saveSize} busy={savingRow !== null} />
      <CacheSettingRow label="Cache folder" type="text" value={folderInput} onChange={editFolder} onSave={() => setConfirmingFolder(true)} busy={savingRow !== null} />
      {confirmingFolder && buildLocationConfirmation(savingRow !== null, saveFolder, () => setConfirmingFolder(false))}
      <button className="cache-drawer-clear" type="button" onClick={() => setConfirmingClear(true)} disabled={savingRow !== null}>Clear cache</button>
      {confirmingClear && buildClearConfirmation(savingRow !== null, clearCache, () => setConfirmingClear(false))}
      {error && <div className="cache-drawer-settings-error" role="alert">{error}</div>}
    </section>
  );
}

function buildClearConfirmation(busy, onContinue, onCancel) {
  return (
    <div className="cache-drawer-clear-confirm channel-drawer-confirm">
      <p>{CLEAR_WARNING}</p>
      <div className="cache-drawer-clear-actions channel-drawer-actions">
        <button type="button" onClick={onContinue} disabled={busy}>Continue</button>
        <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </div>
  );
}

function CacheSettingRow({ label, type, value, onChange, onSave, busy }) {
  return (
    <div className="cache-drawer-settings-row">
      <label className="cache-drawer-settings-label">
        {label}
        <input className="cache-drawer-settings-input" type={type} value={value} onChange={onChange} disabled={busy} />
      </label>
      <button className="cache-drawer-settings-save" type="button" onClick={onSave} disabled={busy}>Save</button>
    </div>
  );
}

function buildLocationConfirmation(busy, onContinue, onCancel) {
  return (
    <div className="cache-drawer-settings-confirm channel-drawer-confirm">
      <p>{LOCATION_WARNING}</p>
      <div className="channel-drawer-actions">
        <button type="button" onClick={onContinue} disabled={busy}>Continue</button>
        <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </div>
  );
}

//---

function buildActiveCard(videos, status, speedBps) {
  if (status.paused) {
    return <div className="cache-drawer-active">Background caching is paused</div>;
  }
  const activeSlots = getActiveSlots(status);
  if (activeSlots.length === 0) {
    return <div className="cache-drawer-active">Idle</div>;
  }

  const cards = [];
  for (const activeSlot of activeSlots) {
    cards.push(buildActiveSlot(videos, status, activeSlot, speedBps));
  }

  return <div className="cache-drawer-active">{cards}</div>;
}

function buildActiveSlot(videos, status, activeSlot, speedBps) {
  const activeVideo = findVideo(videos, activeSlot.msg_id);
  const name = activeVideo?.name || `video_${activeSlot.msg_id}`;
  const cachedBytes = status.videos[String(activeSlot.msg_id)] || 0;
  const pct = buildPercentage(cachedBytes, activeVideo?.size);
  const speed = speedBps == null ? "" : ` · ≈ ${(speedBps / (1024 * 1024)).toFixed(1)} MB/s`;
  const tierLabel = buildTierLabel(activeSlot.tier);

  return (
    <div className="cache-drawer-active-slot" key={activeSlot.msg_id}>
      <div>Downloading {name}</div>
      <div>{pct}%{speed} · {tierLabel}</div>
    </div>
  );
}

function buildItems(videos, status, queuedIds, onCacheNow) {
  const items = [];

  for (const video of videos) {
    items.push(
      <div className="cache-drawer-item" key={video.id}>
        <div className="cache-drawer-item-name">{video.name}</div>
        <div className="cache-drawer-item-state">{buildItemState(video, status, queuedIds, onCacheNow)}</div>
      </div>,
    );
  }

  return items;
}

function buildItemState(video, status, queuedIds, onCacheNow) {
  const cachedBytes = status.videos[String(video.id)] || 0;
  const pct = buildPercentage(cachedBytes, video.size);
  const downloading = isVideoDownloading(status, video.id);

  if (pct >= 100) return "cached";
  if (downloading && status.paused) return `${pct}% paused`;
  if (downloading) return `${pct}% ↓`;
  if (pct === 0) return buildCacheNowButton(video.id, queuedIds, onCacheNow);
  return `${pct}%`;
}

function buildCacheNowButton(id, queuedIds, onCacheNow) {
  return (
    <button
      type="button"
      className="cache-drawer-item-queue"
      title="Cache this video now"
      disabled={queuedIds.has(id)}
      onClick={() => onCacheNow(id)}
    >
      ↑
    </button>
  );
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
  if (tier === "priority") return "caching requested video";
  if (tier === "visible") return "caching on-screen videos";
  if (tier === "prewarm") return "prewarming library";
  return "";
}
