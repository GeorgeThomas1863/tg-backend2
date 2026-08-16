import { useEffect, useRef, useState } from "react";

const HOVER_DELAY_MS = 300;

// Debounced hover intent for row previews: previewing flips true only after
// the pointer rests on the row for HOVER_DELAY_MS, so scrolling the list
// never opens streams. disabled (row expanded) suppresses and cancels.
export function useHoverPreview(disabled) {
  const [previewing, setPreviewing] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    if (disabled) stopPreview();
  }, [disabled]);
  useEffect(() => () => clearTimeout(timerRef.current), []);

  function startPreviewTimer() {
    if (disabled || timerRef.current !== null) return;
    timerRef.current = setTimeout(() => setPreviewing(true), HOVER_DELAY_MS);
  }

  function stopPreview() {
    clearTimeout(timerRef.current);
    timerRef.current = null;
    setPreviewing(false);
  }

  return { previewing, onMouseEnter: startPreviewTimer, onMouseLeave: stopPreview };
}
