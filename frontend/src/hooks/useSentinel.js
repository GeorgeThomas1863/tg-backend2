import { useCallback, useRef } from "react";

// Returns a callback ref; whenever the ref'd element scrolls into view the latest
// onVisible is called. Used as the infinite-scroll trigger.
export function useSentinel(onVisible) {
  const callbackRef = useRef(onVisible);
  const observerRef = useRef(null);
  callbackRef.current = onVisible;

  const sentinelRef = useCallback((node) => {
    observerRef.current?.disconnect();
    observerRef.current = null;
    if (!node) return;

    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) callbackRef.current();
      }
    });
    observerRef.current = observer;
    observer.observe(node);
  }, []);

  return sentinelRef;
}
