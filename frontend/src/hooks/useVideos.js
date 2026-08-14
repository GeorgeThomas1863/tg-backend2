import { useEffect, useRef, useState } from "react";
import { fetchVideos } from "../api/client";

export function useVideos(limit = 50, category = null) {
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

  useEffect(() => {
    const generation = ++requestGeneration.current;
    startOffset.current = 0;
    loadingMoreRef.current = false;
    setLoading(true);
    setError(null);
    setUnauthorized(false);

    setVideos([]);
    setTotal(null);
    setHasMore(false);
    setLoadingMore(false);

    fetchVideos(buildVideoQuery(limit, category))
      .then((data) => {
        if (generation !== requestGeneration.current) return;
        setVideos(data.videos);
        setTotal(data.total);
        setHasMore(calculateHasMore(0, data.videos.length, data.total, limit));
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
  }, [limit, category, fetchCount]);

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
      const data = await fetchVideos(buildVideoQuery(limit, category, { offset }));
      if (generation !== requestGeneration.current) return;
      setVideos(data.videos);
      setTotal(data.total);
      setHasMore(calculateHasMore(offset, data.videos.length, data.total, limit));
    } catch (requestError) {
      if (generation !== requestGeneration.current) return;
      applyRequestError(requestError, setUnauthorized, setError);
    } finally {
      if (generation === requestGeneration.current) setLoading(false);
    }
  };

  const loadMore = async () => {
    if (loading || loadingMoreRef.current || !hasMore || videos.length === 0) return;

    const generation = ++requestGeneration.current;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    const lastId = videos[videos.length - 1].id;

    try {
      const data = await fetchVideos(buildVideoQuery(limit, category, { beforeId: lastId }));
      if (generation !== requestGeneration.current) return;
      const loadedCount = videos.length + data.videos.length;
      setVideos((current) => [...current, ...data.videos]);
      setTotal(data.total);
      setHasMore(calculateHasMore(startOffset.current, loadedCount, data.total, limit, data.videos.length));
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

function buildVideoQuery(limit, category, cursor = {}) {
  const query = { limit, ...cursor };
  if (category) query.category = category;
  return query;
}

function calculateHasMore(offset, loadedCount, total, limit, pageCount = loadedCount) {
  if (typeof total === "number") return offset + loadedCount < total;
  return pageCount === limit;
}

function applyRequestError(error, setUnauthorized, setError) {
  if (error.status === 401) setUnauthorized(true);
  else setError(error.message);
}
