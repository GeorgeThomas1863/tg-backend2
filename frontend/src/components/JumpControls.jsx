import { useState } from "react";

export function JumpControls({ total, disabled, onJump }) {
  const [position, setPosition] = useState("");

  function submitJump(event) {
    event.preventDefault();
    if (!/^\d+$/.test(position)) return;

    const requestedOffset = Number(position);
    if (!Number.isSafeInteger(requestedOffset)) return;
    const offset = typeof total === "number" && total > 0
      ? Math.min(requestedOffset, total - 1)
      : requestedOffset;
    onJump(offset);
  }

  return (
    <form className="jump-controls" onSubmit={submitJump}>
      <label className="jump-controls-label" htmlFor="jump-position">Jump to #</label>
      <input
        id="jump-position"
        className="jump-controls-input"
        type="number"
        min="0"
        step="1"
        inputMode="numeric"
        value={position}
        onChange={(event) => setPosition(event.target.value)}
        disabled={disabled}
      />
      <button className="jump-controls-button" type="submit" disabled={disabled}>Go</button>
    </form>
  );
}
