import { useEffect, useRef } from "react";

// Returns a ref; whenever the ref'd element scrolls into view the latest
// onVisible is called. Used as the infinite-scroll trigger.
export function useSentinel(onVisible) {
  const nodeRef = useRef(null);
  const callbackRef = useRef(onVisible);
  callbackRef.current = onVisible;

  useEffect(() => {
    const node = nodeRef.current;
    if (!node) return;

    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) callbackRef.current();
      }
    });
    observer.observe(node);

    return () => observer.disconnect();
  }, []);

  return nodeRef;
}
