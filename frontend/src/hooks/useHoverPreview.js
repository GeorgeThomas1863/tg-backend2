import { useEffect, useRef, useState } from "react";

const HOVER_DELAY_MS = 300;

// Debounced hover intent for row previews: previewing flips true only after
// the pointer rests on the row for HOVER_DELAY_MS, so scrolling the list
// never opens streams. disabled (row expanded) suppresses and cancels.
export function useHoverPreview(disabled) {
  const [previewing, setPreviewing] = useState(false);
  const timerRef = useRef(null);
  const hoveringRef = useRef(false);

  // Re-arm through the same debounce on disabled:true->false (e.g. collapsing
  // a row without the pointer ever leaving it) only if the pointer is still
  // over the row; startPreviewTimer's own guard keeps this from stacking a
  // second timer on top of one a mouseenter already started.
  useEffect(() => {
    if (disabled) {
      stopPreview();
      return;
    }
    if (hoveringRef.current) startPreviewTimer();
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

  function onMouseEnter() {
    hoveringRef.current = true;
    startPreviewTimer();
  }

  function onMouseLeave() {
    hoveringRef.current = false;
    stopPreview();
  }

  return { previewing, onMouseEnter, onMouseLeave };
}
