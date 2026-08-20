import { useEffect, useRef, useState } from "react";
import { fetchVideos } from "../api/client";

export function useVideos(limit = 50, category = null, search = null) {
  const [videos, setVideos] = useState([]);
  const [total, setTotal] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState(null);
  const [unauthorized, setUnauthorized] = useState(false);
  const [fetchCount, setFetchCount] = useState(0);
  const loadingMoreRef = useRef(false);
  const requestGeneration = useRef(0);
  const startOffset = useRef(0);
  const searchOffsetRef = useRef(0);

  useEffect(() => {
    const generation = ++requestGeneration.current;
    startOffset.current = 0;
    searchOffsetRef.current = 0;
    loadingMoreRef.current = false;
    setLoading(true);
    setError(null);
    setUnauthorized(false);

    setVideos([]);
    setTotal(null);
    setHasMore(false);
    setLoadingMore(false);

    fetchVideos(buildVideoQuery(limit, category, search))
      .then((data) => {
        if (generation !== requestGeneration.current) return;
        setVideos(data.videos);
        setTotal(data.total);
        if (search) {
          const nextOffset = computeNextSearchOffset(0, data, limit);
          searchOffsetRef.current = nextOffset;
          setHasMore(calculateSearchHasMore(nextOffset, 0, data.total));
        } else {
          setHasMore(calculateHasMore(0, data.videos.length, data.total, limit));
        }
        setLoading(false);
      })
      .catch((requestError) => {
        if (generation !== requestGeneration.current) return;
        applyRequestError(requestError, setUnauthorized, setError);
        setLoading(false);
      });

    return () => {
      if (generation === requestGeneration.current) requestGeneration.current += 1;
    };
  }, [limit, category, search, fetchCount]);

  const refetch = () => {
    requestGeneration.current += 1;
    setFetchCount((count) => count + 1);
  };

  const jumpTo = async (offset) => {
    if (!Number.isInteger(offset) || offset < 0) return;

    const generation = ++requestGeneration.current;
    startOffset.current = offset;
    loadingMoreRef.current = false;
    setVideos([]);
    setLoading(true);
    setLoadingMore(false);
    setHasMore(false);
    setError(null);
    setUnauthorized(false);

    try {
      const data = await fetchVideos(buildVideoQuery(limit, category, search, { offset }));
      if (generation !== requestGeneration.current) return;
      setVideos(data.videos);
      setTotal(data.total);
      if (search) {
        const nextOffset = computeNextSearchOffset(offset, data, limit);
        searchOffsetRef.current = nextOffset;
        setHasMore(calculateSearchHasMore(nextOffset, offset, data.total));
      } else {
        setHasMore(calculateHasMore(offset, data.videos.length, data.total, limit));
      }
    } catch (requestError) {
      if (generation !== requestGeneration.current) return;
      applyRequestError(requestError, setUnauthorized, setError);
    } finally {
      if (generation === requestGeneration.current) setLoading(false);
    }
  };

  const loadMore = async () => {
    if (loading || loadingMoreRef.current || !hasMore || (!search && videos.length === 0)) return;

    const generation = ++requestGeneration.current;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    // Search mode has no stable ordering key to page by beforeId, so it pages
    // by the server-reported next_offset cursor instead of videos already
    // loaded — that offset counts raw matches before Telegram resolution
    // drops deleted ids, so it stays valid even when a whole page drops.
    const sentOffset = searchOffsetRef.current;
    const cursor = search ? { offset: sentOffset } : { beforeId: videos[videos.length - 1].id };

    try {
      const data = await fetchVideos(buildVideoQuery(limit, category, search, cursor));
      if (generation !== requestGeneration.current) return;
      setVideos((current) => appendNewVideos(current, data.videos));
      setTotal(data.total);
      if (search) {
        const nextOffset = computeNextSearchOffset(sentOffset, data, limit);
        searchOffsetRef.current = nextOffset;
        setHasMore(calculateSearchHasMore(nextOffset, sentOffset, data.total));
      } else {
        const loadedCount = videos.length + data.videos.length;
        setHasMore(calculateHasMore(startOffset.current, loadedCount, data.total, limit, data.videos.length));
      }
    } catch (requestError) {
      if (generation !== requestGeneration.current) return;
      applyRequestError(requestError, setUnauthorized, setError);
    } finally {
      if (generation === requestGeneration.current) {
        loadingMoreRef.current = false;
        setLoadingMore(false);
      }
    }
  };

  return { videos, total, loading, loadingMore, hasMore, error, unauthorized, refetch, jumpTo, loadMore };
}

function buildVideoQuery(limit, category, search, cursor = {}) {
  const query = { limit, ...cursor };
  // Search mode ignores category server-side, so it is never sent alongside it.
  if (search) {
    query.search = search;
    return query;
  }
  if (category) query.category = category;
  return query;
}

function calculateHasMore(offset, loadedCount, total, limit, pageCount = loadedCount) {
  if (typeof total === "number") return offset + loadedCount < total;
  return pageCount === limit;
}

function calculateSearchHasMore(nextOffset, sentOffset, total) {
  return nextOffset > sentOffset && nextOffset < total;
}

function computeNextSearchOffset(sentOffset, data, limit) {
  if (typeof data.next_offset === "number") return data.next_offset;
  return sentOffset + limit;
}

function appendNewVideos(current, incoming) {
  const existingIds = new Set();
  for (const video of current) existingIds.add(video.id);

  const appended = [...current];
  for (const video of incoming) {
    if (!existingIds.has(video.id)) appended.push(video);
  }
  return appended;
}

function applyRequestError(error, setUnauthorized, setError) {
  if (error.status === 401) setUnauthorized(true);
  else setError(error.message);
}
