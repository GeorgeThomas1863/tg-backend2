// Pure formatters for video metadata display. Builders only — no side effects.
// Each returns a display string, or "—" when the value is missing.

export function formatDate(isoString) {
  if (!isoString) return "—";
  return isoString.slice(0, 10);
}

export function formatDuration(seconds) {
  if (seconds == null) return "—";

  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const paddedSeconds = String(total % 60).padStart(2, "0");

  if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")}:${paddedSeconds}`;
  return `${minutes}:${paddedSeconds}`;
}

export function formatSize(bytes) {
  if (!bytes) return "—";

  const mb = bytes / (1024 * 1024);
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  if (mb >= 10) return `${Math.round(mb)} MB`;
  return `${mb.toFixed(1)} MB`;
}
