import { useEffect, useRef, useState } from "react";
import { fetchVideos } from "../api/client";

// Fetches the video list with cursor pagination and exposes
// loading/error/auth state. A 401 surfaces as `unauthorized` (the password
// gate), not as an error. `refetch` restarts from the first page (called
// after login). `loadMore` appends the next page; `hasMore` is false once a
// page comes back short.
export function useVideos(limit = 50) {
  const [videos, setVideos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState(null);
  const [unauthorized, setUnauthorized] = useState(false);
  const [fetchCount, setFetchCount] = useState(0);
  const loadingMoreRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setUnauthorized(false);

    fetchVideos(limit)
      .then((data) => {
        if (cancelled) return;
        setVideos(data);
        setHasMore(data.length === limit);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err.status === 401) setUnauthorized(true);
        else setError(err.message);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [limit, fetchCount]);

  const refetch = () => setFetchCount((count) => count + 1);

  const loadMore = async () => {
    if (loading || loadingMoreRef.current || !hasMore || videos.length === 0) return;

    loadingMoreRef.current = true;
    setLoadingMore(true);
    const lastId = videos[videos.length - 1].id;

    try {
      const page = await fetchVideos(limit, lastId);
      setVideos((current) => [...current, ...page]);
      setHasMore(page.length === limit);
    } catch (err) {
      if (err.status === 401) setUnauthorized(true);
      else setError(err.message);
    }

    loadingMoreRef.current = false;
    setLoadingMore(false);
  };

  return { videos, loading, loadingMore, hasMore, error, unauthorized, refetch, loadMore };
}
