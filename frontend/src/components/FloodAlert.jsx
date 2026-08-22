import { useEffect, useState } from "react";
import { formatAgo } from "../format";

const TITLE_ID = "flood-alert-title";

export function FloodAlert({ flood, paused, onPause }) {
  const [dismissedCount, setDismissedCount] = useState(0);
  const count = flood?.count ?? 0;
  const lastSecondsAgo = flood?.last_seconds_ago;
  // A backend restart resets its count to 0; a dismissal from before that must not hide new incidents.
  const effectiveDismissed = count < dismissedCount ? 0 : dismissedCount;
  const isVisible = count > effectiveDismissed && lastSecondsAgo !== null && lastSecondsAgo !== undefined && lastSecondsAgo <= 600;

  useEffect(() => {
    if (!isVisible) return undefined;

    function dismissOnEscape(event) {
      if (event.key === "Escape") setDismissedCount(count);
    }

    document.addEventListener("keydown", dismissOnEscape);
    return () => document.removeEventListener("keydown", dismissOnEscape);
  }, [count, isVisible]);

  if (!isVisible) return null;

  return (
    <div className="flood-alert-backdrop">
      <section className="flood-alert" role="alertdialog" aria-labelledby={TITLE_ID} aria-modal="true">
        <h2 id={TITLE_ID}>Telegram is rate-limiting this account (HTTP 429)</h2>
        <p>{count} {count === 1 ? "incident" : "incidents"} since the backend started. The last incident was {formatAgo(lastSecondsAgo)}.</p>
        <p>Downloads back off for a short cooldown and then resume. If this keeps happening, pause downloading.</p>
        <div className="flood-alert-actions">
          {paused
            ? <button type="button" disabled>Downloads are paused</button>
            : <button type="button" onClick={onPause}>Pause downloads</button>}
          <button type="button" onClick={() => setDismissedCount(count)}>Dismiss</button>
        </div>
      </section>
    </div>
  );
}
