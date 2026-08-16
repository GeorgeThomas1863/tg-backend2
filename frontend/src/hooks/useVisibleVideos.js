import { useCallback, useEffect, useRef } from "react";
import { postVisibleVideos } from "../api/client";

const SEND_DELAY_MS = 400;

// Tracks which video rows are on screen (IntersectionObserver) and reports
// them to the backend, debounced, so the cache worker downloads what the
// user is looking at first. Returns observeRow(id) -> a stable callback ref
// to attach to that row's root element.
export function useVisibleVideos(videos) {
  const stateRef = useRef(null);
  if (stateRef.current === null) stateRef.current = createTrackerState();
  stateRef.current.videos = videos;

  const observeRow = useCallback((id) => buildRowRef(stateRef.current, id), []);

  useEffect(() => {
    const state = stateRef.current;
    return () => teardownTracker(state);
  }, []);

  return observeRow;
}

//---

function createTrackerState() {
  return {
    videos: [],
    observer: null,
    rowRefs: new Map(), // id -> stable callback ref
    rowElements: new Map(), // id -> observed element
    elementIds: new Map(), // observed element -> id
    visibleIds: new Set(),
    timer: null,
    lastSent: "",
  };
}

function buildRowRef(state, id) {
  const existing = state.rowRefs.get(id);
  if (existing) return existing;
  const rowRef = (node) => attachRowNode(state, id, node);
  state.rowRefs.set(id, rowRef);
  return rowRef;
}

function attachRowNode(state, id, node) {
  if (!node) {
    detachRow(state, id);
    return;
  }
  const observer = ensureObserver(state);
  state.rowElements.set(id, node);
  state.elementIds.set(node, id);
  observer.observe(node);
}

function detachRow(state, id) {
  const node = state.rowElements.get(id);
  if (node) {
    state.observer?.unobserve(node);
    state.rowElements.delete(id);
    state.elementIds.delete(node);
  }
  if (state.visibleIds.delete(id)) scheduleSend(state);
}

function ensureObserver(state) {
  if (state.observer) return state.observer;
  state.observer = new IntersectionObserver((entries) => applyEntries(state, entries));
  return state.observer;
}

function applyEntries(state, entries) {
  let changed = false;
  for (const entry of entries) {
    const id = state.elementIds.get(entry.target);
    if (id === undefined) continue;
    if (entry.isIntersecting && !state.visibleIds.has(id)) {
      state.visibleIds.add(id);
      changed = true;
    }
    if (!entry.isIntersecting && state.visibleIds.has(id)) {
      state.visibleIds.delete(id);
      changed = true;
    }
  }
  if (changed) scheduleSend(state);
}

function scheduleSend(state) {
  if (state.timer) clearTimeout(state.timer);
  state.timer = setTimeout(() => sendVisibleIds(state), SEND_DELAY_MS);
}

function sendVisibleIds(state) {
  state.timer = null;
  const orderedIds = buildOrderedVisibleIds(state);
  const serialized = orderedIds.join(",");
  if (serialized === state.lastSent) return;
  state.lastSent = serialized;
  postVisibleVideos(orderedIds);
}

function buildOrderedVisibleIds(state) {
  const orderedIds = [];
  for (const video of state.videos) {
    if (state.visibleIds.has(video.id)) orderedIds.push(video.id);
  }
  return orderedIds;
}

function teardownTracker(state) {
  if (state.timer) clearTimeout(state.timer);
  state.timer = null;
  state.observer?.disconnect();
  state.observer = null;
}
