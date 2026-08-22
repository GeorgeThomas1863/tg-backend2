import { useEffect, useRef, useState } from "react";

const HOVER_DELAY_MS = 300;
const CLOSE_GRACE_MS = 150;

// Debounced hover intent for row previews: previewing flips true only after
// the pointer rests on the row for HOVER_DELAY_MS, so scrolling the list
// never opens streams. disabled (row expanded) suppresses and cancels.
export function useHoverPreview(disabled) {
  const [previewing, setPreviewing] = useState(false);
  const openTimerRef = useRef(null);
  const closeTimerRef = useRef(null);
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
  useEffect(() => () => clearTimers(), []);

  function startPreviewTimer() {
    if (disabled || openTimerRef.current !== null) return;
    openTimerRef.current = setTimeout(() => {
      openTimerRef.current = null;
      setPreviewing(true);
    }, HOVER_DELAY_MS);
  }

  function stopPreview() {
    clearTimers();
    setPreviewing(false);
  }

  function clearTimers() {
    clearTimeout(openTimerRef.current);
    clearTimeout(closeTimerRef.current);
    openTimerRef.current = null;
    closeTimerRef.current = null;
  }

  function cancelCloseTimer() {
    clearTimeout(closeTimerRef.current);
    closeTimerRef.current = null;
  }

  function scheduleClose() {
    cancelCloseTimer();
    closeTimerRef.current = setTimeout(stopPreview, CLOSE_GRACE_MS);
  }

  function onMouseEnter() {
    hoveringRef.current = true;
    cancelCloseTimer();
    if (previewing) return;
    startPreviewTimer();
  }

  function onMouseLeave() {
    hoveringRef.current = false;
    if (!previewing) {
      stopPreview();
      return;
    }
    scheduleClose();
  }

  function onPopupEnter() {
    cancelCloseTimer();
  }

  function onPopupLeave() {
    scheduleClose();
  }

  return { previewing, onMouseEnter, onMouseLeave, onPopupEnter, onPopupLeave };
}
