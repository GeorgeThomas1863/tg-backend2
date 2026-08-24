import { useEffect, useRef, useState } from "react";
import { cancelBatchCache, requestBatchCache } from "../api/client";

const ARM_RESET_MS = 5000;

// "Cache all" for a category (or the whole library) is a bulk, expensive
// action — thousands of videos, many GB — so a single click only arms the
// button; a second, explicit click fires the request. Arming resets itself
// after ARM_RESET_MS of inactivity or whenever the selected category changes.
export function BulkCacheButton({ selectedCategory, selectedCategoryLabel, videoCount, batch, searchActive }) {
  const [armed, setArmed] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const armTimerRef = useRef(null);

  useEffect(() => {
    disarm();
    setError(null);
  }, [selectedCategory]);
  useEffect(() => clearArmTimer, []);

  function clearArmTimer() {
    clearTimeout(armTimerRef.current);
    armTimerRef.current = null;
  }

  function disarm() {
    clearArmTimer();
    setArmed(false);
  }

  function arm() {
    setError(null);
    setArmed(true);
    clearArmTimer();
    armTimerRef.current = setTimeout(() => setArmed(false), ARM_RESET_MS);
  }

  async function confirm() {
    disarm();
    setBusy(true);
    const result = await requestBatchCache(selectedCategory);
    setBusy(false);
    if (!result.success) setError(result.message);
  }

  async function cancel() {
    setBusy(true);
    const result = await cancelBatchCache();
    setBusy(false);
    if (!result.success) setError(result.message);
  }

  // An active batch outranks the search-disabled state: searching must never
  // hide the only control that can stop a running multi-gigabyte download.
  if (batch?.active) {
    return (
      <div className="bulk-cache">
        <span className="bulk-cache-progress">{buildProgressLabel(batch)}</span>
        <button type="button" className="bulk-cache-btn bulk-cache-btn-cancel" onClick={cancel} disabled={busy}>
          Cancel
        </button>
        {error && <span className="bulk-cache-error">{error}</span>}
      </div>
    );
  }

  if (searchActive) {
    return (
      <button
        type="button"
        className="bulk-cache-btn bulk-cache-btn-disabled"
        disabled
        title="Bulk caching works on a selected category (or the whole library), not a search — clear the search to use it."
      >
        Cache all
      </button>
    );
  }

  const label = armed
    ? buildConfirmLabel(selectedCategory, videoCount)
    : buildIdleLabel(selectedCategory, selectedCategoryLabel, videoCount);

  return (
    <div className="bulk-cache">
      <button
        type="button"
        className={armed ? "bulk-cache-btn bulk-cache-btn-confirm" : "bulk-cache-btn"}
        onClick={armed ? confirm : arm}
        disabled={busy}
        title={label}
      >
        {label}
      </button>
      {error && <span className="bulk-cache-error">{error}</span>}
    </div>
  );
}

function buildIdleLabel(selectedCategory, selectedCategoryLabel, videoCount) {
  if (!selectedCategory) return "Cache library";
  if (typeof videoCount === "number") return `Cache all ${videoCount.toLocaleString()} · ${selectedCategoryLabel}`;
  return `Cache all · ${selectedCategoryLabel}`;
}

function buildConfirmLabel(selectedCategory, videoCount) {
  if (!selectedCategory) return "Really cache the entire library?";
  if (typeof videoCount === "number") return `Really cache ${videoCount.toLocaleString()} videos?`;
  return "Really cache this category?";
}

function buildProgressLabel(batch) {
  const total = batch.total || 0;
  const done = Math.max(0, total - (batch.remaining || 0));
  return `Caching ${done.toLocaleString()}/${total.toLocaleString()} …`;
}
