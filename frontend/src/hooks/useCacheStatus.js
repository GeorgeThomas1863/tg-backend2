import { useEffect, useRef, useState } from "react";
import { fetchCacheStatus, postCachePaused } from "../api/client";

// Polls cache status while enabled, derives transfer speed from consecutive
// successful samples, and exposes a pause toggle that refreshes immediately.
// Authentication failures stop the active polling cycle until re-enabled.
export function useCacheStatus(enabled) {
  const [status, setStatus] = useState(null);
  const [speedBps, setSpeedBps] = useState(null);
  const intervalRef = useRef(null);
  const sampleRef = useRef(null);
  const generationRef = useRef(0);
  const authenticationStoppedRef = useRef(false);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  const stopPolling = () => {
    if (intervalRef.current === null) return;
    clearInterval(intervalRef.current);
    intervalRef.current = null;
  };

  const fetchStatus = async (generation) => {
    if (authenticationStoppedRef.current) return;

    try {
      const nextStatus = await fetchCacheStatus();
      if (
        authenticationStoppedRef.current
        || !enabledRef.current
        || generation !== generationRef.current
      ) return;

      const nextSample = { total_bytes: nextStatus.total_bytes, timestamp: Date.now() };
      const previousSample = sampleRef.current;
      if (previousSample) {
        const delta = nextSample.total_bytes - previousSample.total_bytes;
        const seconds = (nextSample.timestamp - previousSample.timestamp) / 1000;
        setSpeedBps(seconds > 0 ? Math.max(0, delta) / seconds : 0);
      }
      sampleRef.current = nextSample;
      setStatus(nextStatus);
    } catch (err) {
      if (!enabledRef.current || generation !== generationRef.current) return;
      if (err.status === 401) {
        authenticationStoppedRef.current = true;
        stopPolling();
      }
    }
  };

  useEffect(() => {
    generationRef.current += 1;
    const generation = generationRef.current;

    if (!enabled) {
      stopPolling();
      sampleRef.current = null;
      setStatus(null);
      setSpeedBps(null);
      return undefined;
    }

    authenticationStoppedRef.current = false;
    fetchStatus(generation);
    intervalRef.current = setInterval(() => fetchStatus(generation), 3000);

    return () => {
      generationRef.current += 1;
      stopPolling();
    };
  }, [enabled]);

  const togglePaused = async () => {
    if (status === null || authenticationStoppedRef.current) return;

    const generation = generationRef.current;
    const result = await postCachePaused(!status.paused);
    if (!enabledRef.current || generation !== generationRef.current) return;
    if (result.success) await fetchStatus(generation);
  };

  return { status, speedBps, togglePaused };
}
